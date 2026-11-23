#!/usr/bin/env bash
# 信鸽 SJTU —— 开发模式启动。
#
# 第一次跑会自动建 venv、装依赖；之后直接起。
#   ./start.sh              起后端 + 界面
#   ./start.sh --port 8730  换端口
set -e
cd "$(dirname "$0")"

PY=python3
if [ -n "$XINGE_PYTHON" ]; then PY="$XINGE_PYTHON"; fi

if [ ! -d ".venv" ]; then
  echo "[信鸽] 第一次运行，创建虚拟环境"
  "$PY" -m venv .venv
  echo "[信鸽] 安装依赖（几分钟）"
  ./.venv/bin/pip install --upgrade pip -q
  ./.venv/bin/pip install -r requirements.txt -q
  echo "[信鸽] 完成"
fi

export PYTHONIOENCODING=utf-8
exec ./.venv/bin/python -m app.server "$@"
