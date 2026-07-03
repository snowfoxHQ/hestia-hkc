#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
#  HKC 知识星球 — 一键启动
#  自动起后端(hkc-api) + 前端(hkc-ui),并打开浏览器。
#  按 Ctrl+C 一次性关闭全部。
# ════════════════════════════════════════════════════════════
set -euo pipefail

# ── 配置(可用环境变量覆盖)──
API_PORT="${HKC_PORT:-8000}"
UI_PORT="${HKC_UI_PORT:-8080}"
export HKC_DATA_DIR="${HKC_DATA_DIR:-./hkc_data}"
export HKC_EMBEDDING="${HKC_EMBEDDING:-local}"

# 切到脚本所在目录(项目根)
cd "$(dirname "$0")"

# ── 颜色 ──
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[0;34m'; N='\033[0m'
info(){ echo -e "${B}▸${N} $1"; }
ok(){ echo -e "${G}✓${N} $1"; }
warn(){ echo -e "${Y}!${N} $1"; }
err(){ echo -e "${R}✗${N} $1"; }

echo ""
echo "  🌌 HKC 知识星球 — 启动中"
echo "  ─────────────────────────────"

# ── 1. 检查 Python ──
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then err "未找到 Python,请先安装 Python 3.9+"; exit 1; fi
ok "Python: $($PY --version 2>&1)"

# ── 2. 检查/安装后端依赖 ──
if ! $PY -c "import hkc_api" >/dev/null 2>&1; then
  info "首次运行,安装后端依赖(可能需要几分钟)..."
  if $PY -m pip install -e ".[all]" 2>/dev/null; then
    ok "依赖安装完成"
  else
    warn "标准安装失败,尝试 --break-system-packages ..."
    $PY -m pip install -e ".[all]" --break-system-packages
    ok "依赖安装完成"
  fi
else
  ok "后端依赖已就绪"
fi

# ── 3. LLM Provider / API Key 提示 ──
HKC_LLM_PROVIDER="${HKC_LLM_PROVIDER:-anthropic}"
_KEY="${HKC_LLM_API_KEY:-${DEEPSEEK_API_KEY:-${OPENAI_API_KEY:-${ANTHROPIC_API_KEY:-}}}}"
export HKC_LLM_PROVIDER
if [ -z "$_KEY" ]; then
  warn "未检测到 LLM API Key —— 摄入知识需要 LLM 抽取。"
  warn "  没有 key 也能启动并浏览界面,但「摄入知识」功能无法使用。"
  warn "  DeepSeek 用法:"
  warn "    export HKC_LLM_PROVIDER=deepseek"
  warn "    export DEEPSEEK_API_KEY=sk-...    然后重新运行"
  warn "  (Claude 用法:export ANTHROPIC_API_KEY=sk-ant-... ,默认 provider 即 anthropic)"
else
  ok "LLM Provider=$HKC_LLM_PROVIDER,API Key 已设置(摄入功能可用)"
fi

# ── 4. 端口占用检查 ──
port_busy(){ $PY -c "import socket,sys; s=socket.socket(); r=s.connect_ex(('127.0.0.1',int('$1'))); s.close(); sys.exit(0 if r==0 else 1)"; }
if port_busy "$API_PORT"; then err "端口 $API_PORT 已被占用,请关闭占用进程或设 HKC_PORT=其他端口"; exit 1; fi
if port_busy "$UI_PORT"; then err "端口 $UI_PORT 已被占用,请关闭占用进程或设 HKC_UI_PORT=其他端口"; exit 1; fi

# ── 5. 清理函数(Ctrl+C / 退出时杀掉子进程)──
API_PID=""; UI_PID=""
cleanup(){
  echo ""
  info "正在关闭..."
  # 优雅终止记录的 PID(注意:python -m 的 $! 可能不是真正监听进程,故下面再兜底)
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null || true
  sleep 1
  # 兜底:按端口模式可靠清理(python -m 的真实 PID 与 $! 可能不一致)
  pkill -f "uvicorn.*--port $API_PORT" 2>/dev/null || true
  pkill -f "http.server $UI_PORT" 2>/dev/null || true
  ok "已关闭。再见 👋"
  exit 0
}
trap cleanup INT TERM

# ── 6. 起后端 ──
info "启动后端 hkc-api (端口 $API_PORT, embedding=$HKC_EMBEDDING)..."
$PY -m uvicorn hkc_api.main:app --host 127.0.0.1 --port "$API_PORT" --log-level warning &
API_PID=$!

# 等后端 ready(轮询 /health,最多 30 秒)
info "等待后端就绪..."
READY=0
for i in $(seq 1 60); do
  if $PY -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:$API_PORT/health',timeout=2); " >/dev/null 2>&1; then
    READY=1; break
  fi
  # 后端进程是否还活着
  if ! kill -0 "$API_PID" 2>/dev/null; then err "后端启动失败,请检查上面的错误信息"; exit 1; fi
  sleep 0.5
done
if [ "$READY" != "1" ]; then err "后端 30 秒内未就绪,启动失败"; cleanup; fi
ok "后端已就绪 → http://127.0.0.1:$API_PORT"

# ── 7. 起前端 ──
info "启动前端 hkc-ui (端口 $UI_PORT)..."
$PY -m http.server "$UI_PORT" --bind 127.0.0.1 --directory hkc-ui >/dev/null 2>&1 &
UI_PID=$!
sleep 1
ok "前端已就绪 → http://127.0.0.1:$UI_PORT"

UI_URL="http://localhost:$UI_PORT/index.html"

# ── 8. 自动打开浏览器 ──
open_browser(){
  if command -v open >/dev/null 2>&1; then open "$1" 2>/dev/null || true          # macOS
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" 2>/dev/null || true # Linux
  elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "$1" 2>/dev/null || true # WSL
  fi
}
open_browser "$UI_URL"

echo ""
echo "  ─────────────────────────────"
ok "HKC 已启动!"
echo -e "  ${G}前端界面:${N} $UI_URL"
echo -e "  ${G}后端 API:${N} http://localhost:$API_PORT"
echo ""
echo -e "  在界面里点左下「连接设置」→ 填 http://localhost:$API_PORT → 连接"
echo -e "  ${Y}按 Ctrl+C 关闭全部${N}"
echo "  ─────────────────────────────"

# ── 9. 保持运行,等 Ctrl+C ──
wait
