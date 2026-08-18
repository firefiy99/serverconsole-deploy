#!/usr/bin/env bash
# ============================================================
# ServerConsole 三端协同系统 - 一键部署脚本
# 用法: bash install.sh
# 要求: Linux x86_64/aarch64, 已安装 Docker
# ============================================================
set -e
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info(){ echo -e "${GREEN}[信息]${NC} $1"; }
warn(){ echo -e "${YELLOW}[提示]${NC} $1"; }
err(){ echo -e "${RED}[错误]${NC} $1"; exit 1; }

# ---------- 0. 检查 Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  err "未检测到 Docker，请先安装：curl -fsSL https://get.docker.com | bash"
fi
if ! docker info >/dev/null 2>&1; then
  err "Docker 未运行或无权限（可能需要 sudo）"
fi
info "Docker 已就绪: $(docker --version)"

# ---------- 1. 生成 .env（不存在时） ----------
if [ ! -f .env ]; then
  warn "未找到 .env，正在生成默认配置..."
  AGENT_KEY=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  cat > .env <<EOF
AGENT_KEY=$AGENT_KEY
QQ_ACCOUNT=10001
SERVER_IP=
DSH_USER=admin
DSH_PASSWORD=
EOF
  info "已生成 .env，密钥: $AGENT_KEY"
  warn "请编辑 .env 填写你的 QQ_ACCOUNT，然后重新运行本脚本"
  exit 0
fi
info "已读取 .env 配置"

# ---------- 2. 检查必要配置 ----------
source .env 2>/dev/null || true
if [ -z "$AGENT_KEY" ] || [ "$AGENT_KEY" = "change_me_to_a_random_key" ]; then
  err ".env 中 AGENT_KEY 无效，请设置一个随机密钥"
fi
if [ -z "$QQ_ACCOUNT" ] || [ "$QQ_ACCOUNT" = "10001" ]; then
  warn "QQ_ACCOUNT 尚未设置（当前 $QQ_ACCOUNT），NapCat 将无法登录，请编辑 .env"
fi

# ---------- 3. 准备数据目录 ----------
mkdir -p data/astrbot data/gsuid_core data/dsh data/dsh-workspace
info "数据目录已就绪"

# ---------- 4. 构建并启动 ----------
info "构建并启动全部容器（首次拉取镜像可能需要几分钟）..."
docker compose up -d --build
info "全部容器已启动"

# ---------- 5. 输出访问信息 ----------
IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "你的服务器IP")
echo
echo "====================================================="
echo " 🎉 ServerConsole 三端协同系统部署完成！"
echo "-----------------------------------------------------"
echo " 控制台面板:  http://$IP:8000"
echo "   密钥:      $AGENT_KEY"
echo " AstrBot:    http://$IP:6185"
echo " NapCat:     http://$IP:6099"
echo " GsCore:     http://$IP:8765"
echo " DSH:        https://$IP:8443"
echo "-----------------------------------------------------"
echo " 手机 App: 下载 ServerConsole App，填入以上地址和密钥"
echo "====================================================="
echo
warn "安全提醒：请务必在 .env 中修改 AGENT_KEY，并在云服务商安全组中"
warn "仅放行你需要暴露的端口（8000/6185/6099），不要对公网暴露 8443。"
