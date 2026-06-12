# HIKARI_BOT 部署脚本 (systemd + uv)
# 用法:
#   .\deploy.ps1              上传 + uv sync + 重启服务
#   .\deploy.ps1 -Logs        查看实时日志
#   .\deploy.ps1 -Status      查看服务状态

param(
    [string]$Server    = "192.168.31.2",
    [string]$User      = "root",
    [string]$Password  = "123456",
    [string]$RemoteDir = "/opt/HIKARI_BOT",
    [switch]$Logs      = $false,
    [switch]$Status    = $false
)

$ErrorActionPreference = "Stop"

# ============================================================================
# 工具函数
# ============================================================================

function Write-Step {
    param([int]$N, [string]$Desc, [int]$Total = 4)
    Write-Host ""
    Write-Host ("─" * 55) -ForegroundColor DarkGray
    Write-Host " [$N/$Total] $Desc" -ForegroundColor Cyan
    Write-Host ("─" * 55) -ForegroundColor DarkGray
}
function Write-OK  { Write-Host "  ✓ $args" -ForegroundColor Green }
function Write-Warn { Write-Host "  ⚠ $args" -ForegroundColor Yellow }
function Write-Err  { Write-Host "  ✗ $args" -ForegroundColor Red; exit 1 }

# ============================================================================
# 检测 SSH 工具
# ============================================================================

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     HIKARI_BOT 部署 (systemd + uv)              ║" -ForegroundColor Cyan
Write-Host "║     目标: ${User}@${Server}  ${RemoteDir}           ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan

$SSH = $null; $SCP = $null; $SSH_TYPE = ""

$plinkPaths = @(
    "C:\Program Files\PuTTY\plink.exe",
    "C:\Program Files (x86)\PuTTY\plink.exe"
)
foreach ($p in $plinkPaths) {
    if (Test-Path $p) { $SSH = $p; $SCP = $p.Replace("plink.exe", "pscp.exe"); $SSH_TYPE = "plink"; break }
}
if (-not $SSH) {
    $cmd = Get-Command plink.exe -ErrorAction SilentlyContinue
    if ($cmd) { $SSH = $cmd.Source; $SCP = (Get-Command pscp.exe).Source; $SSH_TYPE = "plink" }
}
if (-not $SSH) {
    $cmd = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if ($cmd) { $SSH = $cmd.Source; $SCP = (Get-Command scp.exe).Source; $SSH_TYPE = "ssh" }
}
if (-not $SSH) {
    Write-Err "未找到 ssh.exe 或 plink.exe`n  OpenSSH: 设置 → 可选功能 → 添加 OpenSSH 客户端`n  PuTTY: https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html"
}

Write-Host ""
if ($SSH_TYPE -eq "plink") { Write-OK "SSH: PuTTY (密码模式)" }
else { Write-OK "SSH: OpenSSH"; Write-Warn "请确保已配置免密登录: ssh-copy-id ${User}@${Server}" }

$sshBaseArgs = if ($SSH_TYPE -eq "plink") {
    @("-l", $User, "-pw", $Password, "-P", "22", "-batch")
} else {
    @("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null")
}

function Invoke-SSH {
    param([string]$Command)
    if ($SSH_TYPE -eq "plink") {
        $tmp = [System.IO.Path]::GetTempFileName()
        $Command -replace "`r`n", "`n" | Out-File -FilePath $tmp -Encoding ASCII
        $r = Get-Content $tmp -Raw | & $SSH @sshBaseArgs $Server "bash -s" 2>&1
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        $r
    } else {
        $Command | & $SSH @sshBaseArgs "${User}@${Server}" "bash -s" 2>&1
    }
}

function Invoke-RSCP {
    param([string]$Source, [string]$Dest)
    if ($SSH_TYPE -eq "plink") {
        & $SCP @sshBaseArgs -r -q $Source "${Server}:${Dest}" 2>&1
    } else {
        & $SCP @sshBaseArgs -r -q $Source "${User}@${Server}:${Dest}" 2>&1
    }
}

# ============================================================================
# 快捷命令
# ============================================================================

if ($Logs) {
    Invoke-SSH "journalctl -u hikari-bot -f -n 50"
    exit 0
}

if ($Status) {
    Invoke-SSH "systemctl status hikari-bot --no-pager 2>&1; echo '---'; journalctl -u hikari-bot -n 30 --no-pager 2>&1"
    exit 0
}

# ============================================================================
# 1. 上传
# ============================================================================

Write-Step 1 "上传项目文件" 4

$LocalRoot = $PSScriptRoot

Write-Host "  创建远程目录..." -ForegroundColor Gray
Invoke-SSH "mkdir -p $RemoteDir/data/ai_memory $RemoteDir/data/admin $RemoteDir/skills $RemoteDir/logs $RemoteDir/prompts"

$UploadItems = @(
    @{Src="bot.py";                   Dst="$RemoteDir/bot.py"},
    @{Src="pyproject.toml";           Dst="$RemoteDir/pyproject.toml"},
    @{Src="uv.lock";                  Dst="$RemoteDir/uv.lock"},
    @{Src="config.prod.json";         Dst="$RemoteDir/config.prod.json"},
    @{Src="version.json";             Dst="$RemoteDir/version.json"},
    @{Src="bump_build.py";            Dst="$RemoteDir/bump_build.py"},
    @{Src="prompts";                  Dst="$RemoteDir/"},
    @{Src="skills";                   Dst="$RemoteDir/"},
    @{Src="deploy/hikari-bot.service"; Dst="$RemoteDir/hikari-bot.service"},
    @{Src="src";                      Dst="$RemoteDir/"}
)

Push-Location $LocalRoot
try {
    foreach ($item in $UploadItems) {
        Write-Host "  上传: $($item.Src)" -ForegroundColor Gray
        Invoke-RSCP -Source $item.Src -Dest $item.Dst
        if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "上传失败: $($item.Src)" }
    }
    Write-OK "全部上传完成"
} finally { Pop-Location }

# ============================================================================
# 2. 安装依赖
# ============================================================================

Write-Step 2 "安装依赖 (uv sync)" 4

$depScript = @'
set -e
cd REMOTE_DIR_PLACEHOLDER

# 确保 uv 已安装
if ! command -v uv &>/dev/null; then
    echo "uv 未安装，正在安装..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 安装/更新依赖
uv sync
echo "OK"
'@ -replace "REMOTE_DIR_PLACEHOLDER", $RemoteDir

$result = Invoke-SSH $depScript
if ($result -match "OK") { Write-OK "依赖已就绪" }
else { Write-Host $result -ForegroundColor Gray; Write-Err "依赖安装失败" }

# ============================================================================
# 3. 安装 & 启动服务
# ============================================================================

Write-Step 3 "安装 & 启动 systemd 服务" 4

$svcScript = @'
set -e
cd REMOTE_DIR_PLACEHOLDER

# ── 准备配置文件 ─────────────────────────────────
cp -f config.prod.json config.json

# ── 准备目录 ─────────────────────────────────────
mkdir -p data/ai_memory data/admin skills logs prompts

# ── 安装服务文件 ─────────────────────────────────
cp -f hikari-bot.service /etc/systemd/system/hikari-bot.service
systemctl daemon-reload
systemctl enable hikari-bot

# ── 重启 ─────────────────────────────────────────
systemctl restart hikari-bot
sleep 2
echo "OK"
'@ -replace "REMOTE_DIR_PLACEHOLDER", $RemoteDir

$result = Invoke-SSH $svcScript
if ($result -match "OK") { Write-OK "服务已启动" }
else { Write-Host $result -ForegroundColor Gray; Write-Err "服务启动失败" }

# ============================================================================
# 4. 状态
# ============================================================================

Write-Step 4 "运行状态" 4

$svcStatus = Invoke-SSH "systemctl is-active hikari-bot 2>&1 && echo '---' && journalctl -u hikari-bot -n 15 --no-pager 2>&1"
Write-Host $svcStatus -ForegroundColor Gray

if ($svcStatus -match "active") { Write-OK "服务运行中 ✓" }
else { Write-Warn "服务可能未启动，查看上方日志" }

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║           部署完成 ✓                            ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
Write-Host "  日常使用:" -ForegroundColor White
Write-Host "    .\deploy.ps1              上传 + uv sync + 重启" -ForegroundColor Gray
Write-Host "    .\deploy.ps1 -Status      查看服务状态 + 最近日志" -ForegroundColor Gray
Write-Host "    .\deploy.ps1 -Logs        实时跟踪日志" -ForegroundColor Gray
Write-Host ""
Write-Host "  服务器上:" -ForegroundColor White
Write-Host "    systemctl status hikari-bot    查看状态" -ForegroundColor Gray
Write-Host "    systemctl restart hikari-bot   重启" -ForegroundColor Gray
Write-Host "    journalctl -u hikari-bot -f    实时日志" -ForegroundColor Gray
Write-Host ""
