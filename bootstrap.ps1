# gigabite bootstrap for Windows: one line from nothing to a working install.
#   irm https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.ps1 | iex
#
#   - checks for git, and says how to get it if it is missing (it does not install it)
#   - clones to %USERPROFILE%\gigabite, or updates the clone already there
#   - if uv is missing, installs it with winget (Microsoft's, which IT can allow or
#     block), and otherwise asks you to get it from IT
#   - installs gigabite as a uv tool, editable, so the clone is what runs and a
#     `git pull` updates it (uv brings a Python if there is none)
#   - puts uv's tool folder on PATH, then runs `gigabite install`
# Safe to run again any time. Your notes and settings are left alone.
#
# Nothing here changes PowerShell's execution policy or works around any other
# control, and it runs no script it downloads. If your organisation blocks a
# step, it stops there and says which.
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
    # A native command's own error text, one line each, for a failure message.
    function Lines($Output) { @($Output | ForEach-Object { "    $_" } | Where-Object { $_.Trim() }) }
    # Picks up what an installer just added to PATH, without a new window.
    function Refresh-Path {
        $parts = @([Environment]::GetEnvironmentVariable('Path', 'User'),
                   [Environment]::GetEnvironmentVariable('Path', 'Machine'),
                   (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'), $env:Path)
        $env:Path = ($parts | Where-Object { $_ }) -join ';'
    }

    # -----------------------------------------------------------------------
    Say '1/4  Checking this PC can run gigabite'
    if ($env:OS -ne 'Windows_NT') {
        Fail 'This script is for Windows. On a Mac, use bootstrap.sh (see the README).'
        return
    }
    if (-not (Have 'git')) {
        Fail 'git is not installed, and gigabite is downloaded with it.' @(
            'Get Git for Windows from Company Portal or your IT team (Claude Code uses',
            'the Git Bash that comes with it). If you may install software yourself:',
            '    winget install --id Git.Git -e',
            'Then open a new PowerShell window and run the gigabite command again.')
        return
    }
    Ok 'git'
    $claudeJson = Join-Path $env:USERPROFILE '.claude.json'
    if ($env:CLAUDE_CONFIG_DIR) { $claudeJson = Join-Path $env:CLAUDE_CONFIG_DIR '.claude.json' }
    if ((Have 'claude') -or (Test-Path $claudeJson) -or
        (Test-Path (Join-Path $env:USERPROFILE '.claude.json'))) {
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
        $out = git -C $Dest pull --ff-only --quiet 2>&1
        if ($LASTEXITCODE -eq 0) {
            Ok 'updated to the latest version'
        } else {
            # --ff-only cannot rewrite or discard anything, so a failure means local
            # edits, another branch, or no network. None is worth stopping for.
            Warn 'could not update this copy, carrying on with the version already there. git said:'
            foreach ($line in (Lines $out)) { Note $line }
            Note 'Usually local edits, a different branch, or no network (on a company'
            Note 'network, a proxy may be blocking github.com).'
        }
    } else {
        $env:GIT_TERMINAL_PROMPT = '0'
        $cloneArgs = @('clone', '--quiet')
        if ($Branch) { $cloneArgs += @('--branch', $Branch) }
        $out = git @cloneArgs $RepoUrl $Dest 2>&1
        if ($LASTEXITCODE -ne 0) {
            Fail 'Could not download gigabite. git said:' (@(Lines $out) + @(
                'If that names a proxy, a certificate or a connection, a company proxy or',
                'firewall is the likely cause: ask IT whether git needs a proxy setting here.'))
            return
        }
        Ok "downloaded to $Dest"
    }

    # -----------------------------------------------------------------------
    Say '3/4  Installing gigabite with uv'
    if (-not (Have 'uv')) {
        $getUv = @('Get uv from Company Portal or your IT team, then run this again.',
                   'It is the tool gigabite is installed with: https://docs.astral.sh/uv/')
        if (-not (Have 'winget')) {
            Fail 'uv is not installed, and winget is not here to install it.' $getUv
            return
        }
        Note 'uv is not installed, so installing it with winget'
        winget install --id astral-sh.uv -e --scope user
        if ($LASTEXITCODE -ne 0) {
            Fail 'winget could not install uv (see above).' $getUv
            return
        }
        Refresh-Path
        if (-not (Have 'uv')) {
            Fail 'uv was installed, but this PC will not run it.' @(
                'Company policy may block programs installed in your user folder.',
                'Ask your IT team to allow uv, or to install it, then run this again.')
            return
        }
    }
    Ok 'uv'
    uv tool install --editable $Dest --force
    if ($LASTEXITCODE -ne 0) {
        Fail 'uv could not install gigabite.' @(
            'If Claude Code, or a terminal running gigabite, is open, it may be holding',
            'gigabite.exe: quit it and run this again.',
            'Otherwise the lines above say why. A refused download usually means a company',
            'proxy: uv fetches packages from pypi.org and Python from github.com.')
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
