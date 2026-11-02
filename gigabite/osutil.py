"""The few places gigabite has to ask which operating system it is on.

Everything else is pathlib and the standard library, and runs the same on macOS
and Windows. What is here is what does not: file locks (``fcntl`` is POSIX-only,
``msvcrt`` Windows-only), starting a process that outlives its parent (fork and
setsid have no Windows equivalent), and the console (Windows decodes pipes in the
ANSI code page and only draws colour once asked to).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import IO, Optional

WINDOWS = os.name == "nt"

# For a console program started from a process with no console (the refresh
# worker): without it Windows opens a window for the child.
NO_WINDOW = {"creationflags": 0x08000000} if WINDOWS else {}    # CREATE_NO_WINDOW


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def tilde(path) -> str:
    """`~/Knowledge` rather than `/Users/jane/Knowledge`: shorter to read, and
    the same string the docs and the README use. Case-blind where the file system
    is (Windows), and only at a separator, so `/Users/janet` is not under
    `/Users/jane`."""
    text, home = str(path), str(Path.home()).rstrip(os.sep)
    if os.path.normcase(text) == os.path.normcase(home):
        return "~"
    if os.path.normcase(text).startswith(os.path.normcase(home + os.sep)):
        return "~" + text[len(home):]
    return text


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------

def lock(path, *, wait: bool = True) -> Optional[IO]:
    """An exclusive lock on *path*, or None when it is held elsewhere (``wait``
    False) or the platform cannot lock it at all.

    The lock belongs to the returned handle and goes with it: `unlock` it, or let
    the process exit, which releases it whatever killed the process — so a crash
    while holding it cannot wedge the next caller.
    """
    try:
        handle = open(path, "a+")
    except OSError:
        return None
    try:
        if WINDOWS:
            import msvcrt
            handle.seek(0)
            # LK_LOCK retries for about ten seconds, then raises; LK_NBLCK fails at once.
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except (OSError, ImportError):
        handle.close()
        return None
    return handle


def unlock(handle: Optional[IO]) -> None:
    if handle is None:
        return
    try:
        if WINDOWS:
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        try:
            handle.close()                     # releases a flock
        except OSError:
            pass


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------

def detach(argv: list, env: Optional[dict] = None) -> None:
    """Start *argv* so that it outlives this process and holds none of its pipes.

    A hook's caller waits until every handle on the hook's stdout is closed, so
    the child gets /dev/null for all three. On POSIX it gets a session of its own,
    out of reach of whatever tears down the hook's process group. On Windows it
    gets no console window, and leaves the caller's job object where the job
    allows it — a job that kills its members on close would otherwise take the
    child with it.
    """
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True, env=env)
    if not WINDOWS:
        subprocess.Popen(argv, start_new_session=True, **kwargs)
        return
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000      # CREATE_NO_WINDOW
    try:
        subprocess.Popen(argv, creationflags=flags | 0x01000000,  # CREATE_BREAKAWAY_FROM_JOB
                         **kwargs)
    except OSError:                            # the job forbids breakaway
        subprocess.Popen(argv, creationflags=flags, **kwargs)


# ---------------------------------------------------------------------------
# the console
# ---------------------------------------------------------------------------

def utf8_stdio() -> None:
    """Read and write UTF-8 on Windows whatever the code page says.

    Python decodes a Windows pipe in the ANSI code page (cp1252 and friends), so
    a hook payload with a non-Latin prompt arrived garbled and a ✓ written to a
    pipe or a log raised UnicodeEncodeError. Claude Code reads and writes UTF-8.
    A no-op elsewhere, where the locale already is UTF-8.
    """
    if not WINDOWS:
        return
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def ansi_ok(stream=None) -> bool:
    """May *stream* be written ANSI colour codes? Only a terminal, and on Windows
    only once the console has agreed to interpret them (Windows 10 and later)."""
    stream = stream or sys.stdout
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if not WINDOWS:
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11)                          # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # 0x0004 is ENABLE_VIRTUAL_TERMINAL_PROCESSING.
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False
