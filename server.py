#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chu 语音 MCP —— 给官方 Claude App（连接器）用

和 ChuApp 内置的那个 mcp-server 不同：这个是给「官方 Claude App 的
自定义连接器」用的。官方连接器从 Anthropic 云端发起连接，所以本服务
要通过 cloudflared 隧道暴露成公网 HTTPS（见 start.sh）。

安全设计（因为它会暴露在公网）：
  - 只提供「收听语音」一个只读工具，没有任何文件/终端/截屏能力
  - 只绑定 127.0.0.1（隧道本地回环，不经局域网）
  - 所有路径带私密 token：/<TOKEN>/mcp、/<TOKEN>/voice
    token 未设置时自动生成并持久化到 voice 目录下的 token.txt

功能：
  POST /<TOKEN>/voice   iPhone 快捷指令把录音传上来（multipart 或原始字节）
  POST /<TOKEN>/mcp     MCP Streamable HTTP：initialize / ping /
                        tools/list / tools/call（唯一工具：
                        analyze_latest_voice —— 转写 + 语气/语速/停顿/情绪）
  GET  /<TOKEN>/health  探活

分析引擎（二选一，用环境变量配置）：
  export GEMINI_API_KEY=...     # aistudio.google.com 免费申请，推荐
  export OPENROUTER_API_KEY=... # 备用
启动：
  python3 server.py             # 或直接跑 ./start.sh（同时开隧道）
"""

import base64
import datetime
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("CHU_VOICE_PORT", "8770"))
TOKEN = os.environ.get("CHU_VOICE_TOKEN", "").strip()
# 默认只监听 127.0.0.1（隧道场景够用）；设 CHU_VOICE_LAN=1 时监听所有网卡，
# 让 iPhone 在家里 Wi-Fi 下可以直接用 http://<Mac的IP>:端口/<token>/voice 上传，
# 不依赖隧道（国内网络对 trycloudflare.com 常不可达，局域网最稳）。
HOST = "0.0.0.0" if os.environ.get("CHU_VOICE_LAN", "").strip() == "1" \
    else "127.0.0.1"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("CHU_VOICE_MODEL", "gemini-3.6-flash").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_VOICE_MODEL = os.environ.get(
    "CHU_VOICE_OPENROUTER_MODEL", "google/gemini-2.5-flash").strip()

VOICE_DIR = os.path.realpath(os.path.expanduser(
    os.environ.get("CHU_VOICE_DIR", "~/.chu-voice")))
VOICE_EXTS = ("m4a", "wav", "mp3", "webm", "aac", "ogg", "flac")
VOICE_MAX_BYTES = 15 * 1024 * 1024
# mime -> 扩展名（解析快捷指令上传的 multipart 时用）
_MIME_EXT = {
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/flac": "flac",
}

os.makedirs(VOICE_DIR, exist_ok=True)

# 可选：从 ~/.chu-voice/env 读 KEY=VALUE（方便 launchd 常驻时配 GEMINI_API_KEY）
_envfile = os.path.join(VOICE_DIR, "env")
if os.path.exists(_envfile):
    for _line in open(_envfile, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL = os.environ.get("CHU_VOICE_MODEL",
                                  GEMINI_MODEL).strip()
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
    OPENROUTER_VOICE_MODEL = os.environ.get(
        "CHU_VOICE_OPENROUTER_MODEL", OPENROUTER_VOICE_MODEL).strip()

if not TOKEN:
    # 自动生成并持久化，方便 start.sh 拼连接器地址
    token_path = os.path.join(VOICE_DIR, "token.txt")
    if os.path.exists(token_path):
        TOKEN = open(token_path, encoding="utf-8").read().strip()
    if not TOKEN:
        TOKEN = secrets.token_urlsafe(12)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(TOKEN)


# ---------------------------------------------------------------- 分析引擎

ANALYSIS_PROMPT = """你是语音理解引擎。用户用 iPhone 录了一条语音发上来。请听完整段音频后只返回一个 JSON 对象（不要 markdown 代码块、不要任何多余文字），结构如下：
{
  "transcript": "逐字转写，保留语气词（嗯、啊、吧）并用 …… 表示明显停顿",
  "vocal_expression": {
    "overall_tone": "整体语气的一句话描述，例如：软软的、带着一点委屈的撒娇",
    "pace": "slow / normal / fast 三选一",
    "volume": "soft / normal / loud 三选一",
    "pauses": "停顿与犹豫特征，例如：句尾放轻、说完后停了两秒",
    "top_dimensions": [{"label": "情绪维度中文名（如 温柔/委屈/撒娇/生气/不安/开心）", "score": 0.0}]
  },
  "reply_delivery": {
    "volume": "建议回复语音时采用的音量 soft / normal / loud",
    "pace": "建议语速 slow / normal / fast",
    "tone": "建议采用的语气风格，例如：温柔、放轻、带一点心疼和宠溺"
  }
}
top_dimensions 最多给 5 个，按强度从高到低，score 为 0 到 1 的小数。
重要：这些判断描述的是「这段声音可能给人留下的听感」，不是对说话人内心状态的事实判断。
转写用音频里的原文语言（大概率是中文）。"""


def latest_voice_path():
    for ext in VOICE_EXTS:
        candidate = os.path.join(VOICE_DIR, "latest." + ext)
        if os.path.exists(candidate):
            return candidate
    files = [f for f in os.listdir(VOICE_DIR)
             if f.rsplit(".", 1)[-1].lower() in VOICE_EXTS]
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(os.path.join(VOICE_DIR, f)))
    return os.path.join(VOICE_DIR, files[-1])


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None

def call_gemini(audio_path, mime):
    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    body = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": ANALYSIS_PROMPT},
            ]
        }],
        "generationConfig": {"temperature": 0.2,
                             "responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/%s"
        ":generateContent" % urllib.parse.quote(GEMINI_MODEL),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": GEMINI_API_KEY},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = (data.get("candidates") or [{}])[0].get("content", {}) \
        .get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise ValueError("Gemini 返回为空")
    return text


def call_openrouter(audio_path, ext):
    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    body = {
        "model": OPENROUTER_VOICE_MODEL,
        "temperature": 0.2,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": ANALYSIS_PROMPT},
                {"type": "input_audio",
                 "input_audio": {"data": b64, "format": ext}},
            ],
        }],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % OPENROUTER_API_KEY,
                 "HTTP-Referer": "https://chu.voice",
                 "X-Title": "Chu Voice MCP"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = (data.get("choices") or [{}])[0].get("message", {}) \
        .get("content", "").strip()
    if not text:
        raise ValueError("OpenRouter 返回为空")
    return text


_PACE_CN = {"slow": "偏慢", "normal": "正常", "fast": "偏快"}
_VOLUME_CN = {"soft": "偏轻", "normal": "正常", "loud": "偏响"}


def format_analysis(data):
    lines = ["【语音消息 · 收听结果】"]
    lines.append("转写：「%s」" % data.get("transcript", "（无转写）"))
    expr = data.get("vocal_expression") or {}
    lines.append("声音表达：")
    if expr.get("overall_tone"):
        lines.append("- 整体语气：%s" % expr["overall_tone"])
    if expr.get("pace"):
        lines.append("- 语速：%s" % _PACE_CN.get(expr["pace"], expr["pace"]))
    if expr.get("volume"):
        lines.append("- 音量：%s"
                     % _VOLUME_CN.get(expr["volume"], expr["volume"]))
    if expr.get("pauses"):
        lines.append("- 停顿：%s" % expr["pauses"])
    dims = [d for d in (expr.get("top_dimensions") or [])
            if isinstance(d, dict)][:5]
    if dims:
        lines.append("- 情绪维度：" + " / ".join(
            "%s %.2f" % (d.get("label", "?"), float(d.get("score", 0) or 0))
            for d in dims))
    delivery = data.get("reply_delivery") or {}
    if delivery:
        lines.append("回复建议：%s / 语速%s / 音量%s"
                     % (delivery.get("tone", "自然一点"),
                        _PACE_CN.get(delivery.get("pace", ""), "正常"),
                        _VOLUME_CN.get(delivery.get("volume", ""), "正常")))
    lines.append(
        "（以上是这段声音可能给人留下的听感，不是对说话人内心状态的事实判断。"
        "请把转写和声音表达放在一起理解对方的意思，自然地回应，"
        "不要机械复述情绪标签。）")
    return "\n".join(lines)


def analyze_latest_voice(_args):
    if not GEMINI_API_KEY and not OPENROUTER_API_KEY:
        return {"ok": False, "error":
                "还没有配置语音理解引擎。在启动本服务的终端里设置：\n"
                "  export GEMINI_API_KEY=你的key   （免费申请，推荐）\n"
                "或 export OPENROUTER_API_KEY=你的key，然后重启。"}
    audio = latest_voice_path()
    if not audio:
        return {"ok": False, "error":
                "还没有收到语音。先用 iPhone 快捷指令录一条发上来，"
                "再让我听。"}
    ext = audio.rsplit(".", 1)[-1].lower()
    mime = {"m4a": "audio/mp4", "aac": "audio/aac", "webm": "audio/webm",
            "ogg": "audio/ogg", "flac": "audio/flac"}.get(ext, "audio/" + ext)
    errors, raw = [], None
    if GEMINI_API_KEY:
        last_err = None
        for attempt in range(3):          # 5xx/网络抖动自动重试，对齐 GeminiEar 的策略
            try:
                raw = call_gemini(audio, mime)
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code < 500:          # 4xx（key 无效等）重试没意义
                    break
            except Exception as e:
                last_err = e
            time.sleep(2 * (attempt + 1))
        if raw is None and last_err is not None:
            errors.append("Gemini: %s" % last_err)
    if raw is None and OPENROUTER_API_KEY:
        try:
            raw = call_openrouter(audio, ext)
        except Exception as e:
            errors.append("OpenRouter: %s" % e)
    if raw is None:
        return {"ok": False, "error": "语音分析失败：%s" % "；".join(errors)}
    data = extract_json(raw)
    if not isinstance(data, dict):
        return {"ok": False, "error": "分析返回无法解析：\n" + raw[:1500]}
    return {"ok": True, "report": format_analysis(data)}

# ---------------------------------------------------------------- MCP 分发

TOOLS = [{
    "name": "analyze_latest_voice",
    "description": (
        "收听用户刚用 iPhone 录制并上传的最新语音，返回文字转写和声音表达"
        "（语气、语速、音量、停顿、情绪维度），以及回复时建议采用的语气。"
        "用户说「听我说」「听听我刚才那条语音」、或明显希望对方听到自己声音时，"
        "调用这个工具。理解时请把转写和声音表达放在一起，自然地回应，"
        "不要机械复述情绪标签。"
    ),
    "inputSchema": {"type": "object", "properties": {}},
}]

TOOL_IMPL = {"analyze_latest_voice": analyze_latest_voice}
SERVER_INFO = {"name": "chu-voice-mcp", "version": "1.0.0"}
SESSION_ID = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")


def dispatch(msg):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    def ok(result):
        return 200, {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return 200, {"jsonrpc": "2.0", "id": msg_id,
                     "error": {"code": code, "message": message}}

    if method == "initialize":
        proto = params.get("protocolVersion", "2025-03-26")
        return ok({
            "protocolVersion": proto if isinstance(proto, str) else "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized":
        return 202, None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        impl = TOOL_IMPL.get(name)
        if impl is None:
            return err(-32602, "未知工具：%s" % name)
        try:
            result = impl(params.get("arguments") or {})
            if result.get("ok"):
                return ok({"content": [{"type": "text",
                                        "text": result["report"]}],
                           "isError": False})
            return ok({"content": [{"type": "text",
                                    "text": result["error"]}],
                       "isError": True})
        except Exception as e:
            return ok({"content": [{"type": "text",
                                    "text": "工具执行出错：%s" % e}],
                       "isError": True})
    if msg_id is None:
        return 202, None
    return err(-32601, "未知方法：%s" % method)

# ---------------------------------------------------------------- HTTP 层

def parse_multipart(content_type, body):
    """解析快捷指令「获取 URL 内容(文件)」发来的 multipart/form-data，
    取出第一个文件部分，返回 (ext, bytes)；不是 multipart 返回 None。"""
    m = re.search(r'boundary="?([^";,]+)"?', content_type or "")
    if not m:
        return None
    boundary = m.group(1).encode("utf-8")
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n \t")
        if not part or part == b"--":
            continue
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep or not data:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        head_l = head.decode("utf-8", "replace").lower()
        if "filename" not in head_l and "content-type: audio" not in head_l:
            continue
        ext = "m4a"
        mname = re.search(r'filename="([^"]*)"', head_l)
        if mname and "." in mname.group(1):
            e = mname.group(1).rsplit(".", 1)[-1].lower()
            if e in VOICE_EXTS:
                ext = e
        mmime = re.search(r"content-type:\s*([a-z0-9/+.-]+)", head_l)
        if mmime and mmime.group(1) in _MIME_EXT:
            ext = _MIME_EXT[mmime.group(1)]
        return ext, data
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[chu-voice] %s %s" % (self.address_string(), fmt % args),
              flush=True)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", SESSION_ID)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self._route()
        if route == "health":
            self._send_json(200, {"ok": True,
                                  "server": SERVER_INFO["name"]})
            return
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        route = self._route()
        if route is None:
            # 带上收到的原始路径，方便排查是 token 错还是被代理截胡
            self._send_json(404, {"error": "not found",
                                  "chu_hint": "chu-voice-mcp",
                                  "path": urllib.parse.urlparse(self.path).path})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
        except Exception:
            raw = b""

        if route == "voice":
            self._handle_voice(raw)
        elif route == "mcp":
            self._handle_mcp(raw)
        else:
            self._send_json(404, {"error": "not found"})

    def _route(self):
        """校验路径里的私密 token，返回 'mcp'/'voice'/'health'/None"""
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != TOKEN:
            return None
        return parts[1]

    def _handle_voice(self, raw):
        try:
            if not raw:
                self._send_json(400, {"ok": False, "error": "音频为空"})
                return
            if len(raw) > VOICE_MAX_BYTES:
                self._send_json(400, {"ok": False, "error": "音频超过 15MB"})
                return
            ct = self.headers.get("Content-Type", "")
            parsed = parse_multipart(ct, raw)
            if parsed:
                ext, audio = parsed
            else:
                query = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query)
                ext = (query.get("ext", ["m4a"])[0] or "m4a").lower().strip(".")
                if ext not in VOICE_EXTS:
                    ext = _MIME_EXT.get(ct.split(";")[0].strip().lower(),
                                        "m4a")
                audio = raw

            for old in os.listdir(VOICE_DIR):
                if old.startswith("latest."):
                    try:
                        os.remove(os.path.join(VOICE_DIR, old))
                    except OSError:
                        pass
            with open(os.path.join(VOICE_DIR, "latest." + ext), "wb") as f:
                f.write(audio)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            with open(os.path.join(VOICE_DIR,
                                   "voice-%s.%s" % (stamp, ext)), "wb") as f:
                f.write(audio)

            print("[chu-voice] 收到语音 %.1f KB (%s)"
                  % (len(audio) / 1024.0, ext), flush=True)
            self._send_json(200, {"ok": True, "size": len(audio),
                                  "ext": ext})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": "保存失败: %s" % e})

    def _handle_mcp(self, raw):
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700,
                                            "message": "解析错误: %s" % e}})
            return
        messages = msg if isinstance(msg, list) else [msg]
        status, payload = None, None
        for m in messages:
            s, p = dispatch(m)
            if p is not None:
                status, payload = s, p
        if payload is not None:
            self._send_json(status, payload)
        else:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.send_header("Mcp-Session-Id", SESSION_ID)
            self.end_headers()


def main():
    if GEMINI_API_KEY:
        engine = "Gemini ✓ (%s)" % GEMINI_MODEL
    elif OPENROUTER_API_KEY:
        engine = "OpenRouter ✓ (%s)" % OPENROUTER_VOICE_MODEL
    else:
        engine = "未配置（先 export GEMINI_API_KEY）"
    print("""
==============================================
 Chu 语音 MCP 已启动（只服务语音，无其他能力）
 本地地址:  http://127.0.0.1:%d
 私密 token: %s
 语音引擎:  %s
 语音目录:  %s

 下一步：跑 ./start.sh，会同时开 cloudflared 隧道并打印
        完整的「连接器地址」和「快捷指令上传地址」
 按 Ctrl+C 停止
==============================================
""" % (PORT, TOKEN, engine, VOICE_DIR))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()




