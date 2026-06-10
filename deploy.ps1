<#
    HIKARI_BOT 部署脚本 (PowerShell)
    使用 Windows 自带 ssh.exe / scp.exe
    部署到 192.168.31.2 的 /root/HIKARI_BOT/
#>

param(
    [string]$Server   = "192.168.31.2",
    [string]$User     = "root",
    [string]$RemoteDir = "/root/HIKARI_BOT"
)

$ErrorActionPreference = "Stop"
$LocalDir = $PSScriptRoot

# 常用 SSH 参数：跳过 known_hosts 检查
$SshArgs = @("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null")

# ==================== 颜色 ====================
function Write-Info  { Write-Host "[INFO]  $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN]  $args" -ForegroundColor Yellow }
function Write-Err   { Write-Host "[ERROR] $args" -ForegroundColor Red }

function Write-Step {
    param([int]$N, [string]$Desc)
    Write-Host ""
    Write-Host "=== $N/5 $Desc ===" -ForegroundColor Cyan
}

# ==================== 远程脚本（在服务器上执行） ====================
# 先把这段脚本上传到服务器，然后一次性执行，减少密码输入次数
$RemoteSetupScript = @'
#!/usr/bin/env bash
set -e

REMOTE_DIR="/root/HIKARI_BOT"

echo ">>> 检查 Python 环境..."
if ! command -v python3 &>/dev/null; then
    echo "安装 Python3..."
    apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python 版本: $PY_VER"

echo ""
echo ">>> 修复配置（bot 与 NapCat 同机部署，改用 127.0.0.1）..."
cd $REMOTE_DIR
sed -i 's|ws://192\.168\.31\.2:[0-9]\+/|ws://127.0.0.1:54258/|g' .env.prod
cp -f .env.prod .env
echo ".env 内容:"
cat .env

echo ""
echo ">>> 安装 Python 依赖..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q nonebot2 nonebot-adapter-onebot nonebot-plugin-docs nonebot-plugin-sentry
echo "依赖安装完成"

echo ""
echo ">>> 创建 systemd 服务..."
cat > /etc/systemd/system/hikari-bot.service << 'SVC_EOF'
[Unit]
Description=HIKARI_BOT QQ Bot (NoneBot2 + OneBot v11)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/HIKARI_BOT
Environment=ENVIRONMENT=prod
ExecStart=/root/HIKARI_BOT/.venv/bin/python /root/HIKARI_BOT/bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl daemon-reload
systemctl enable hikari-bot
systemctl restart hikari-bot
echo "服务已启动"

echo ""
echo "==================================="
echo "  HIKARI_BOT 部署完成！"
echo "==================================="
echo ""
echo "常用命令:"
echo "  查看日志: journalctl -u hikari-bot -f"
echo "  查看状态: systemctl status hikari-bot"
echo "  重启服务: systemctl restart hikari-bot"
echo "  停止服务: systemctl stop hikari-bot"
echo ""
'@

# ==================== 主流程 ====================
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  HIKARI_BOT 部署脚本" -ForegroundColor Cyan
Write-Host "  目标: ${User}@${Server}:${RemoteDir}" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Warn "部署过程中需要输入服务器密码 (123456)，共会提示 2 次"

# ---------- 1. 清理并上传 ----------
Write-Step 1 "上传项目文件到服务器"
# 先把远程设置脚本写到本地临时文件
$tempSetup = Join-Path $env:TEMP "hikari_setup.sh"
$RemoteSetupScript | Out-File -FilePath $tempSetup -Encoding ASCII -NoNewline

Write-Host "  清理旧目录..."
ssh.exe @SshArgs "${User}@${Server}" "rm -rf ${RemoteDir} && mkdir -p ${RemoteDir}/src"

Write-Host "  上传 bot.py, .env.prod, pyproject.toml ..."
scp.exe @SshArgs -r `
    "$LocalDir\bot.py",
    "$LocalDir\.env.prod",
    "$LocalDir\pyproject.toml",
    "$LocalDir\src" `
    "${User}@${Server}:${RemoteDir}/"

Write-Host "  上传远程设置脚本..."
scp.exe @SshArgs $tempSetup "${User}@${Server}:${RemoteDir}/setup.sh"
Remove-Item $tempSetup -Force

# ---------- 2. 执行远程设置 ----------
Write-Step 2 "在服务器上执行环境安装 & 配置"
ssh.exe @SshArgs "${User}@${Server}" "chmod +x ${RemoteDir}/setup.sh && bash ${RemoteDir}/setup.sh"

# ---------- 3. 查看服务状态 ----------
Write-Step 3 "查看服务运行状态"
Write-Host ""
ssh.exe @SshArgs "${User}@${Server}" "systemctl status hikari-bot --no-pager -l || true"

# ---------- 4. 提示后续操作 ----------
Write-Step 4 "实时日志（按 Ctrl+C 退出）"
Write-Host ""
ssh.exe @SshArgs "${User}@${Server}" "journalctl -u hikari-bot -f"
