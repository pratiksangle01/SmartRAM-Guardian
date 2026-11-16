# SmartRAM Guardian installer for Windows (PowerShell 5.1 or newer). No admin rights needed.
# Everything runs inside one block so nothing is left behind in your PowerShell session.
& {
    $ErrorActionPreference = 'Stop'

    $source = if ($env:SMARTRAM_SOURCE) { $env:SMARTRAM_SOURCE } else { 'https://github.com/pratiksangle01/SmartRAM-Guardian/archive/refs/heads/main.zip' }
    $homeDir = if ($env:SMARTRAM_HOME) { $env:SMARTRAM_HOME } else { Join-Path $HOME '.smartram-guardian' }
    $venv = Join-Path $homeDir 'venv'

    function Find-Python {
        foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
            $exe = $candidate[0]
            $rest = @($candidate | Select-Object -Skip 1)
            if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
            try {
                & $exe @rest -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>$null
                if ($LASTEXITCODE -eq 0) { return , $candidate }
            } catch { }
        }
        return $null
    }

    $python = Find-Python
    if (-not $python -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'Python not found. Installing it with winget...'
        winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $python = Find-Python
    }
    if (-not $python) {
        throw 'Python 3.8 or newer is required. Install it from https://www.python.org/downloads/ and run this again.'
    }

    function Invoke-Checked {
        param([string]$Exe, [string[]]$Arguments)
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
    }

    New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (Test-Path $venvPython) {
        # Stop a previous install so its files are not locked during the upgrade.
        try { & $venvPython -m smartram stop 2>&1 | Out-Null } catch { }
        Start-Sleep -Seconds 1
    }
    $pyRest = @($python | Select-Object -Skip 1)
    Invoke-Checked $python[0] ($pyRest + @('-m', 'venv', $venv))
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--quiet', '--upgrade', $source)
    Invoke-Checked $venvPython @('-m', 'smartram', 'enable')
    Invoke-Checked $venvPython @('-m', 'smartram', 'start')
    Invoke-Checked $venvPython @('-m', 'smartram', 'link')

    # Make the command work in this window too, not only in new ones.
    $binDir = Join-Path $homeDir 'bin'
    if (-not $env:SMARTRAM_NO_MODIFY_PATH -and (($env:Path -split ';') -notcontains $binDir)) {
        $env:Path = $env:Path.TrimEnd(';') + ';' + $binDir
    }
}
