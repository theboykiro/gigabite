import sys


def main(argv=None) -> int:
    """`python -m gigabite` and the installed `gigabite` command.

    The hooks run on every prompt and every agent spawn, so they are dispatched
    here, before the CLI and everything it imports are loaded. And a hook must never
    fail the turn, so a hook name this version does not know (an installer newer
    than the package, say) exits 0 rather than as argparse would, with a 2 that the
    agent-model hook's caller reads as "block this call".
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["hook"] and argv[1:] and not argv[1].startswith("-"):
        from . import hooks, osutil
        osutil.utf8_stdio()
        return hooks.run(argv[1]) if len(argv) == 2 and argv[1] in hooks.NAMES else 0
    if argv[:1] in (["install"], ["uninstall"]):
        # Their own flags and messages (install.sh's), and nothing else loaded:
        # uninstall must not touch the machine it is about to inspect.
        from . import installer, osutil
        osutil.utf8_stdio()
        return installer.main(argv[0], argv[1:])
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
