#!/bin/bash
# Chu 语音 MCP 一键启动：本地服务 + cloudflared 免费隧道
# 用法：./start.sh     （第一次跑前先 export GEMINI_API_KEY=你的key）
set -u
cd "$(dirname "$0")"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "未安装 cloudflared，先跑：brew install cloudflared"; exit 1
fi

# 先起本地服务，token 会自动生成并持久化
# CHU_VOICE_LAN=1：同时监听局域网，iPhone 在家里 Wi-Fi 可直连上传
CHU_VOICE_LAN=1 python3 server.py &
SERVER_PID=$!
trap 'kill $SERVER_PID $TUNNEL_PID 2>/dev/null' EXIT INT TERM

sleep 1
TOKEN=$(cat ~/.chu-voice/token.txt 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
  echo "没拿到 token，检查 ~/.chu-voice/"; exit 1
fi
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "")

LOG=$(mktemp /tmp/chu-voice-tunnel.XXXXXX.log)
cloudflared tunnel --url "http://127.0.0.1:${CHU_VOICE_PORT:-8770}" >"$LOG" 2>&1 &
TUNNEL_PID=$!

echo "等隧道建立…"
URL=""
for _ in $(seq 1 30); do
  URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$LOG" | head -1)
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "$URL" ]; then
  echo "隧道没建起来，日志在 $LOG"; exit 1
fi

echo ""
echo "=============================================="
echo " 全部就绪 ✓"
echo ""
echo " ① Claude 连接器地址（claude.ai → 设置 → 连接器 →"
echo "    添加自定义连接器，粘贴这个）："
echo "    $URL/$TOKEN/mcp"
echo ""
echo " ② iPhone 快捷指令上传地址（二选一）："
if [ -n "$LAN_IP" ]; then
echo "    在家（推荐，局域网直连最快最稳）："
echo "      http://$LAN_IP:${CHU_VOICE_PORT:-8770}/$TOKEN/voice"
fi
echo "    在外（iPhone 挂了代理时可用）："
echo "      $URL/$TOKEN/voice"
echo ""
echo " ③ 测试：Safari 打开 $URL/$TOKEN/health 应显示 ok"
echo ""
echo " 注意：免费快速隧道的地址每次重启会变，变了以后"
echo "      更新连接器和快捷指令里的地址即可。"
echo "      想要固定地址见 README 的「固定域名」一节。"
echo "=============================================="
wait
