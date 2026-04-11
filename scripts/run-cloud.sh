#!/usr/bin/env bash
# 在 Linux 云服务器上运行「英语单词听写」Web 服务。
#
# 用法（在项目根目录，或任意目录指定 APP_ROOT）：
#   chmod +x scripts/run-cloud.sh
#   ./scripts/run-cloud.sh
#
# 环境变量（可选）：
#   APP_ROOT      项目根目录（默认：本脚本所在目录的上一级）
#   VENV_DIR      虚拟环境路径（默认：$APP_ROOT/.venv）
#   FLASK_HOST    监听地址（默认 0.0.0.0，便于云主机外网访问）
#   FLASK_PORT    端口（默认 5000）
#   GUNICORN_WORKERS  进程数（默认 2；TTS 合成偏慢时可保持较小）
#   SKIP_INSTALL  设为 1 时跳过 pip install（加快重启）
#
# 访问密码（可选其一）：
#   export DICTATION_WEB_PASSWORD='你的密码'
#   或在项目根放置 web_access_password.txt（单行密码）
#
# 反向代理：若前面有 Nginx，可只监听 127.0.0.1 并 proxy_pass 到本端口。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${APP_ROOT:-$SCRIPT_DIR/..}" && pwd)"
cd "$APP_ROOT"

VENV_DIR="${VENV_DIR:-$APP_ROOT/.venv}"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "未找到 $PYTHON，请先安装 Python 3.10+（含 venv）。" >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "创建虚拟环境: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
  pip install -U pip setuptools wheel
  pip install -r "$APP_ROOT/requirements-cloud.txt"
fi

export FLASK_HOST="${FLASK_HOST:-0.0.0.0}"
export FLASK_PORT="${FLASK_PORT:-5000}"
WORKERS="${GUNICORN_WORKERS:-2}"

echo "工作目录: $APP_ROOT"
echo "监听: ${FLASK_HOST}:${FLASK_PORT}  workers=$WORKERS"

# Gunicorn：适合云上长期运行；单 worker 也可减轻 edge-tts 并发压力
exec gunicorn \
  --chdir "$APP_ROOT" \
  -w "$WORKERS" \
  -b "${FLASK_HOST}:${FLASK_PORT}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  web_app:app
