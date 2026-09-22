$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if ($env:OS -ne "Windows_NT") {
    throw "该脚本必须在 Windows x64 构建机执行。"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "未找到 uv，请先安装 uv。"
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "未找到 npm，请先安装 Node.js。"
}

Write-Host "[1/3] 构建 Windows 后端..."
& uv run --no-project --python 3.11 python scripts/build_app.py --backend-only

Write-Host "[2/3] 构建 Windows NSIS 安装包..."
& uv run --no-project --python 3.11 python scripts/build_app.py

$installer = Get-ChildItem "src-tauri/target/release/bundle/nsis/*.exe" | Select-Object -First 1
if ($null -eq $installer) {
    throw "未生成 NSIS 安装包。"
}

Write-Host "[3/3] 构建完成：$($installer.FullName)"
