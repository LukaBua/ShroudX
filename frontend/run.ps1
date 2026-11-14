param([switch]$Build)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$taskScript = if ($Build) { 'build' } else { 'dev' }
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
$bundledPnpm = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pnpm/bin/pnpm.cjs'
if ($npmCommand) {
    if (-not (Test-Path 'node_modules/react')) { & $npmCommand.Source install; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
    & $npmCommand.Source run $taskScript
} elseif ($pnpmCommand) {
    if (-not (Test-Path 'node_modules/react')) { & $pnpmCommand.Source install; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
    & $pnpmCommand.Source run $taskScript
} elseif (Test-Path $bundledPnpm) {
    if (-not (Test-Path 'node_modules/react')) { node $bundledPnpm install; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
    node $bundledPnpm run $taskScript
} else {
    throw 'Install Node.js (including npm), then run npm install and npm run dev in frontend/.'
}
exit $LASTEXITCODE
