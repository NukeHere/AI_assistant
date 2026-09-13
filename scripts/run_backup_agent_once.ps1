$ErrorActionPreference = "SilentlyContinue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "data"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outLog = Join-Path $logDir "backup-agent-post-commit-$stamp.out.log"
$errLog = Join-Path $logDir "backup-agent-post-commit-$stamp.err.log"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $pythonExe = $venvPython
    $arguments = @("backup_agent.py", "once")
} else {
    $pythonExe = "py"
    $arguments = @("backup_agent.py", "once")
}
for ($attempt = 1; $attempt -le 12; $attempt++) {
    $time = Get-Date -Format "s"
    Add-Content -LiteralPath $outLog -Value "[$time] post-commit backup attempt $attempt"
    & $pythonExe @arguments 1>> $outLog 2>> $errLog
    if ($attempt -lt 12) {
        Start-Sleep -Seconds 10
    }
}
