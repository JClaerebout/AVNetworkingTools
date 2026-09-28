param([string]$Executable = 'dist\AVNetworkingTools.exe')

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$exe = Join-Path $repoRoot $Executable
$result = Join-Path $repoRoot 'dist\packaged-smoke.json'

if (-not (Test-Path -LiteralPath $exe)) {
    throw "Missing packaged executable: $exe"
}
Remove-Item -LiteralPath $result -ErrorAction SilentlyContinue

# The EXE requests administrator access, just as a normal desktop launch does.
$process = Start-Process -FilePath $exe -ArgumentList @('--packaged-smoke', "`"$result`"") -Wait -PassThru -WindowStyle Hidden
if (-not (Test-Path -LiteralPath $result)) {
    throw "The packaged smoke check produced no result file (exit code $($process.ExitCode))."
}
$report = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
if ($process.ExitCode -ne 0 -or -not $report.success) {
    throw "Packaged smoke check failed: $($report.error)"
}
$report | ConvertTo-Json -Depth 4
