# Windows启动入口，业务逻辑复用src原型。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先在仓库根目录创建.venv，并安装requirements.txt。'
}
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m race_supply.phase1_rag @args
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
