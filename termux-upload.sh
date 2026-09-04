#!/data/data/com.termux/files/usr/bin/bash
# ears 安卓上传脚本（Termux 版）
#
# 准备（一次性）：
#   1. F-Droid 装 Termux + Termux:API 伴侣 App（两件套缺一不可）
#   2. Termux 里执行: pkg install curl termux-api
#   3. export CHU_VOICE_SERVER="https://你的域名/<token>/voice"
#      （或局域网: http://<Mac的IP>:8770/<token>/voice）
#      写进 ~/.bashrc 可以持久化
#
# 用法：
#   ./termux-upload.sh              # 边录边传：开始录音 → 说完按回车 → 自动上传
#   ./termux-upload.sh 某文件.m4a   # 直接上传一个已有录音文件
#
set -e
SERVER="${CHU_VOICE_SERVER:?请先 export CHU_VOICE_SERVER=https://你的域名/<token>/voice}"

if [ $# -ge 1 ] && [ -f "$1" ]; then
  F="$1"
else
  F="$HOME/ears-$(date +%Y%m%d-%H%M%S).m4a"
  echo "🎤 开始录音（最长 60 秒），说完按【回车】停止…"
  termux-microphone-record -f "$F" -l 60
  read -r
  termux-microphone-record -q
fi

echo "📤 上传中…"
if curl -sf -F "file=@$F;type=audio/mp4" "$SERVER" >/dev/null; then
  echo "已发送 ✅ 去 Claude 说：听听我刚才那条"
else
  echo "上传失败 ❌ 检查地址/网络，或 Mac 服务是否在跑"
  exit 1
fi
