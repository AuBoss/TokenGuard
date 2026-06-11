#!/usr/bin/env bash
# 一键启动 Minimax 监控（终端实时视图）
# 用法：./start.sh                # 启动监控
#       ./start.sh web            # 启动 Flask Web 服务
#       ./start.sh view           # 查看历史
#       ./start.sh install        # 安装依赖

set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR=".venv"
PY="${PY:-python3}"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

ensure_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    echo -e "${YELLOW}创建虚拟环境 $VENV_DIR ...${NC}"
    "$PY" -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
}

cmd="${1:-monitor}"

case "$cmd" in
  install)
    ensure_venv
    echo -e "${GREEN}依赖已安装${NC}"
    ;;
  monitor|"")
    ensure_venv
    echo -e "${GREEN}启动终端实时监控（Ctrl+C 退出）${NC}"
    exec python monitor.py
    ;;
  web)
    ensure_venv
    shift || true
    echo -e "${GREEN}启动 Flask Web 服务 (默认 0.0.0.0:5050)${NC}"
    echo -e "${YELLOW}提示: macOS Monterey+ AirPlay 占用 5000 端口，已改用 5050${NC}"
    echo -e "${YELLOW}      如要换端口: ./start.sh web --port 8080${NC}"
    exec python app.py "$@"
    ;;
  view)
    ensure_venv
    shift || true
    exec python viewer.py "$@"
    ;;
  summary)
    ensure_venv
    exec python viewer.py --summary
    ;;
  *)
    echo -e "${RED}未知命令: $cmd${NC}"
    echo "用法: $0 [monitor|web|view|summary|install]"
    exit 1
    ;;
esac
