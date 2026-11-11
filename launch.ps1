# 双击入口（由 启动.cmd 调用）。额外参数原样传给程序，例如 -start-hidden 写成 --start-hidden。
$ErrorActionPreference = 'Stop'
try {
    $venv = Join-Path $PSScriptRoot '.venv'
    $focusPython = Join-Path $venv 'Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $focusPython)) { throw 'Project runtime is missing. Restore .venv using requirements.txt.' }
    $focusEntry = Join-Path $PSScriptRoot 'app\main.py'
    $arguments = @('"' + $focusEntry + '"') + @($args | ForEach-Object { '"' + $_ + '"' })
    # .venv\Scripts\pythonw.exe is only a redirector that keeps waiting beside a second,
    # real interpreter. Starting the base pythonw.exe with __PYVENV_LAUNCHER__ gives the
    # same venv in one process (this is exactly what the redirector does).
    $exe = $focusPython
    $config = Join-Path $venv 'pyvenv.cfg'
    if (Test-Path -LiteralPath $config) {
        $homeLine = Get-Content -LiteralPath $config | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
        if ($homeLine) {
            $base = Join-Path ($homeLine -replace '^\s*home\s*=\s*', '').Trim() 'pythonw.exe'
            if (Test-Path -LiteralPath $base) { $exe = $base; $env:__PYVENV_LAUNCHER__ = $focusPython }
        }
    }
    # No -WindowStyle Hidden: pythonw has no console, and Windows applies the launcher's
    # show mode to the program's first window, which would hide the main window.
    Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $PSScriptRoot
} catch {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($_.Exception.Message, 'Focus Time Assistant') | Out-Null
} finally {
    Remove-Item Env:__PYVENV_LAUNCHER__ -ErrorAction SilentlyContinue
}
