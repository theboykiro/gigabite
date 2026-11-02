# gigabite bootstrap for Windows: one line from nothing to a working install.
#   irm https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.ps1 | iex
#
#   - checks for git, and says how to get it if it is missing (it does not install it)
#   - clones to %USERPROFILE%\gigabite, or updates the clone already there
#   - installs uv with uv's own installer if it is missing
#   - installs gigabite as a uv tool, editable, so the clone is what runs and a
#     `git pull` updates it (uv brings a Python if there is none)
#   - puts uv's tool folder on PATH, then runs `gigabite install`
# Safe to run again any time. Your notes and settings are left alone.
#
# Nothing here changes PowerShell's execution policy or works around any other
# control. If your organisation blocks a step, it stops there and says which.
#
# Testing a branch or a fork: set GIGABITE_REPO_URL (a URL or a local path) and
# GIGABITE_BRANCH before running it.
#
# Kept to plain ASCII and Windows PowerShell 5.1, the PowerShell every Windows PC has.

function Install-Gigabite {
    $ErrorActionPreference = 'Continue'
    $RepoUrl = 'https://github.com/theboykiro/gigabite.git'
    if ($env:GIGABITE_REPO_URL) { $RepoUrl = $env:GIGABITE_REPO_URL }
    $Branch = $env:GIGABITE_BRANCH
    $Dest = Join-Path $env:USERPROFILE 'gigabite'

    function Say([string]$Text) { Write-Host $Text -ForegroundColor White }
    function Note([string]$Text) { Write-Host "  $Text" -ForegroundColor DarkGray }
    function Ok([string]$Text) { Write-Host "  ok  $Text" -ForegroundColor Green }
    function Warn([string]$Text) { Write-Host "  !   $Text" -ForegroundColor Yellow }
    # Prints and returns; the caller returns straight after. Not `exit`, which
    # under `irm | iex` would close the window the message was written in.
    function Fail([string]$Text, [string[]]$Lines = @()) {
        Write-Host "  x   $Text" -ForegroundColor Red
        foreach ($line in $Lines) { Note $line }
        $global:LASTEXITCODE = 1
    }
    function Have([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

    # -----------------------------------------------------------------------
    Say '1/4  Checking this PC can run gigabite'
    if ($env:OS -ne 'Windows_NT') {
        Fail 'This script is for Windows. On a Mac, use bootstrap.sh (see the README).'
        return
    }
    if (-not (Have 'git')) {
        Fail 'git is not installed, and gigabite is downloaded with it.' @(
            'Install Git for Windows (Claude Code uses the Git Bash that comes with it), e.g.:',
            '    winget install --id Git.Git -e',
            'Then open a new PowerShell window and run the gigabite command again.')
        return
    }
    Ok 'git'
    if ((Have 'claude') -or (Test-Path (Join-Path $env:USERPROFILE '.claude.json'))) {
        Ok 'Claude Code'
    } else {
        Warn 'Claude Code is not installed on this PC.'
        Note 'gigabite still works in the terminal: gigabite search "..." over all your history.'
        Note 'Claude answering with your own past work loaded needs Claude Code. Install it,'
        Note 'run this again, and the rest switches on. Nothing is lost.'
    }

    # -----------------------------------------------------------------------
    Say '2/4  Getting gigabite'
    if (Test-Path -LiteralPath $Dest) {
        git -C $Dest rev-parse --git-dir 2>$null | Out-Null
        $isCheckout = ($LASTEXITCODE -eq 0) -and
            (Test-Path (Join-Path $Dest 'pyproject.toml')) -and
            (Test-Path (Join-Path $Dest 'gigabite\__init__.py'))
        if (-not $isCheckout) {
            Fail "There is already something at $Dest, and it is not gigabite." @(
                'Nothing has been touched. Move or rename that folder, then run this again:',
                "    Rename-Item `"$Dest`" gigabite-old")
            return
        }
        Note "already installed at $Dest, updating it"
        git -C $Dest pull --ff-only --quiet 2>$null
        if ($LASTEXITCODE -eq 0) {
            Ok 'updated to the latest version'
        } else {
            # --ff-only cannot rewrite or discard anything, so a failure means local
            # edits, another branch, or no network. None is worth stopping for.
            Warn 'could not update this copy, carrying on with the version already there'
            Note 'usually means local edits, a different branch, or no internet'
        }
    } else {
        $env:GIT_TERMINAL_PROMPT = '0'
        $cloneArgs = @('clone', '--quiet')
        if ($Branch) { $cloneArgs += @('--branch', $Branch) }
        git @cloneArgs $RepoUrl $Dest 2>$null
        if ($LASTEXITCODE -ne 0) {
            Fail 'Could not download gigabite.' @(
                'Check your internet connection and try again. On a work network,',
                'access to github.com may be blocked.')
            return
        }
        Ok "downloaded to $Dest"
    }

    # -----------------------------------------------------------------------
    Say '3/4  Installing gigabite with uv'
    if (-not (Have 'uv')) {
        Note 'uv is not installed, so installing it with its own installer (astral.sh)'
        try {
            [Net.ServicePointManager]::SecurityProtocol =
                [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-RestMethod 'https://astral.sh/uv/install.ps1' | Invoke-Expression
        } catch {
            Fail 'Could not install uv.' @(
                "The error was: $($_.Exception.Message)",
                'If your organisation restricts PowerShell scripts or downloads, ask IT to',
                'install uv (https://docs.astral.sh/uv/), then run this again.')
            return
        }
        # The installer puts uv on the user PATH for new windows; this one too.
        foreach ($dir in @((Join-Path $env:USERPROFILE '.local\bin'),
                           (Join-Path $env:USERPROFILE '.cargo\bin'))) {
            if ((Test-Path $dir) -and (($env:Path -split ';') -notcontains $dir)) {
                $env:Path = "$dir;$env:Path"
            }
        }
        if (-not (Have 'uv')) {
            Fail 'uv was installed, but this window cannot find it yet.' @(
                'Open a new PowerShell window and run the gigabite command again.')
            return
        }
    }
    Ok 'uv'
    uv tool install --editable $Dest --force
    if ($LASTEXITCODE -ne 0) {
        Fail 'uv could not install gigabite (the lines above say why).' @(
            'If a download was refused, your network may block pypi.org or python.org.')
        return
    }
    uv tool update-shell 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn 'could not add uv''s tool folder to your PATH; run: uv tool update-shell' }
    $binDir = "$(uv tool dir --bin 2>$null)".Trim()
    if (-not $binDir) { $binDir = Join-Path $env:USERPROFILE '.local\bin' }
    if (($env:Path -split ';') -notcontains $binDir) { $env:Path = "$binDir;$env:Path" }
    $exe = Join-Path $binDir 'gigabite.exe'
    if (-not (Test-Path $exe)) {
        Fail "uv reported success, but there is no gigabite.exe in $binDir."
        return
    }
    Ok "installed: $exe"

    # -----------------------------------------------------------------------
    Say '4/4  Running the installer'
    Write-Host ''
    & $exe install
    if ($LASTEXITCODE -ne 0) {
        Fail 'gigabite install stopped (see above).' @(
            'If Windows refused to start gigabite.exe, your organisation may restrict',
            'programs in your user folder. Ask IT, then run this again.')
        return
    }
    Write-Host ''
    Say 'Quit and restart Claude Code. Nothing is live until then.'
    Note 'In a new terminal, `gigabite` works from anywhere.'
}

Install-Gigabite
