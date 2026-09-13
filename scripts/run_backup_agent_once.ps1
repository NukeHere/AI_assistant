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
    Add-Content -LiteralPath $outLog -Encoding UTF8 -Value "[$time] post-commit backup attempt $attempt"
    $output = & $pythonExe @arguments 2>&1
    if ($LASTEXITCODE -eq 0) {
        $output | Out-File -LiteralPath $outLog -Append -Encoding UTF8
    } else {
        $output | Out-File -LiteralPath $errLog -Append -Encoding UTF8
    }
    if ($attempt -lt 12) {
        Start-Sleep -Seconds 10
    }
}
