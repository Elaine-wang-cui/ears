# ears 👂

给 **官方 Claude App**（iPhone / Mac / 网页版都行）装上一只「耳朵」：
你用 iPhone 录一段语音，Claude 不只知道你**说了什么**，还能听到你**怎么说**的——
语气、语速、停顿、情绪，然后带语气回复你。

```
iPhone 快捷指令「录制音频」
      ↓ POST /<token>/voice（原始音频字节）
本服务（跑在你自己的 Mac 上）
      ↓ Claude 调用 MCP 工具 analyze_latest_voice
Gemini 2.5+ 听音频 → 转写 + 语气/语速/音量/停顿 + 情绪维度
      ↓
Claude 结合「文字 + 声音表达」回复你
```

- ✅ 纯 Python 标准库，**零第三方依赖**
- ✅ 录音只存在你自己机器上，分析时才发给所选引擎的 API
- ✅ 带 token 的私密路径，不会被人乱传文件
- ✅ 内置 5xx 自动重试（模型临时过载不用管）
- ✅ 引擎可换：Gemini API（推荐，免费额度够用）或 OpenRouter 音频模型

> 灵感：让 AI 伴侣「听懂撒娇和凶巴巴的区别」。情绪分数是「这段声音可能
> 给人留下的听感」，不是对说话人内心状态的事实判断——工具返回里内置了
> 这条提醒，Claude 不会机械复述情绪标签。

## 工作原理

1. `POST /<TOKEN>/voice` —— iPhone 快捷指令把录音（m4a/mp3/wav…）传上来，
   服务存为 `latest.m4a`（外加一份带时间戳的存档）
2. 官方 Claude App 通过 **自定义连接器（Remote MCP）** 调用
   `analyze_latest_voice` 工具
3. 服务把音频 base64 后发给 Gemini（或 OpenRouter 音频模型），
   拿到严格 JSON：转写 + 声音表达 + 建议回复语气
4. 格式化成一份「收听报告」返回给 Claude

注意方向：**上传是你（手机）发起的，「听」是 Claude 发起的**——
这正是它绕开「官方 App 拿不到原始音频」限制的原因。

## 快速开始

### ① 配语音引擎

去 [aistudio.google.com](https://aistudio.google.com) 免费申请一个
Gemini API Key（`AIza...` 或 `AQ.` 开头都行），然后：

```bash
git clone https://github.com/<你>/ears.git
cd chu-voice-mcp
export GEMINI_API_KEY=你的key
```

### ② 启动服务

```bash
chmod +x start.sh
./start.sh
```

`start.sh` 会做两件事：
1. 启动本地服务（默认 `127.0.0.1:8770`，token 自动生成，
   存在 `~/.chu-voice/token.txt`）
2. 起一个 **cloudflared 快速隧道**（需要 `brew install cloudflared`），
   并打印两个地址：

```
连接器地址:     https://xxxx.trycloudflare.com/<token>/mcp   ← 给 claude.ai 用
快捷指令上传地址: https://xxxx.trycloudflare.com/<token>/voice ← 给 iPhone 用
```

只想在自己局域网里玩？不跑隧道也行：

```bash
CHU_VOICE_LAN=1 python3 server.py
# iPhone 直接 POST http://<Mac的IP>:8770/<token>/voice
```

### ③ 添加 Claude 连接器

[claude.ai](https://claude.ai) → 设置 → **连接器（Connectors）** →
添加自定义连接器 → 粘贴**连接器地址** → 保存。
添加一次，手机 / Mac / 网页版官方 Claude App 全都能用。

### ④ iPhone 快捷指令（录音入口）

新建一个快捷指令，三个动作：

1. **录制音频**（Record Audio）—— 结束方式选「点按时」
2. **获取 URL 内容**（Get Contents of URL）：
   - URL：填**上传地址**
   - 方法：**POST**
   - 请求体：类型切到 **文件**，文件选上一步的「录制的音频」
3. **显示通知**：内容随便，比如「已发送 ✅」

（可选）再加一个「打开 URL」填 `claude://`，录完自动跳进 Claude App。

### 安卓（Android）怎么录

服务端不挑设备，任何能发 HTTP POST 的方式都行。三选一：

**① Tasker（付费，体验最像 iOS 快捷指令）**

1. 新建任务 → 动作 **录制音频**（Record Audio，可在参数里限时长）
2. 动作 **HTTP 请求**：方法 `POST`，地址填**上传地址**（局域网地址记得
   加 `?ext=m4a`），请求体选 **文件** → 勾选上一步的录音
3. 动作 **通知**：`已发送 ✅`
4. 把任务做成桌面图标，一点就录

**② HTTP Request Shortcuts（免费开源）**

建一个 POST 快捷方式指向**上传地址**，请求体选文件类型。它不能录音，
先用系统录音机录好，运行时在文件选择器里挑刚录的文件——多两步，零成本。

**③ Termux（极客流）**

仓库里带了现成脚本 [`termux-upload.sh`](termux-upload.sh)：
F-Droid 装 Termux + Termux:API → `pkg install curl termux-api` →
`export CHU_VOICE_SERVER=上传地址` → 以后一条命令边录边传，
配合 Termux Widget 可以做成桌面图标。

安卓官方 Claude App 登录同一账号即可使用连接器（连接器是账号级设置，
在 claude.ai 网页添加一次即可）。安卓各机型/各 App 的上传行为可能有
差异，遇到报错把信息发 issue。

### ⑤ 开聊

点快捷指令 → 说话 → 点停止 → 打开官方 Claude App 说：
**「听听我刚才那条」**。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `GEMINI_API_KEY` | — | 语音分析引擎（推荐，[免费申请](https://aistudio.google.com)） |
| `CHU_VOICE_MODEL` | `gemini-3.6-flash` | Gemini 模型名 |
| `OPENROUTER_API_KEY` | — | 备用引擎（OpenRouter 音频模型） |
| `CHU_VOICE_OPENROUTER_MODEL` | `google/gemini-2.5-flash` | OpenRouter 模型 |
| `CHU_VOICE_PORT` | `8770` | 监听端口 |
| `CHU_VOICE_LAN` | 关 | 设 `1` 时监听 `0.0.0.0`（局域网直传） |
| `CHU_VOICE_TOKEN` | 自动生成 | 私密路径 token，持久化在数据目录 |
| `CHU_VOICE_DIR` | `~/.chu-voice` | 录音与 token 存放目录 |
| `CHU_VOICE_KEEP_DAYS` | `7` | 历史录音存档保留天数，`0` 永久保留 |

也可以在 `~/.chu-voice/env` 里写 `KEY=VALUE`（配合 launchd/systemd 常驻很方便）。

## 常驻后台（可选）

macOS LaunchAgent 示例（`~/Library/LaunchAgents/com.you.voicemcp.plist`）：
`ProgramArguments` 指向 `python3 server.py`，`KeepAlive` 设 `true`，
`RunAtLoad` 设 `true`。或者简单点用 `nohup ./start.sh &`。

## 隐私与安全

**数据流向（先说清楚）**：
- 原始语音会以 base64 发给你配置的分析引擎（Google Gemini 或 OpenRouter），
  转写和语气分析结果经 MCP 返回给 Claude——除此之外音频不发任何第三方
- 录音只存在你机器的 `CHU_VOICE_DIR`（默认 `~/.chu-voice`）。带时间戳的
  历史存档**默认 7 天自动清理**（`CHU_VOICE_KEEP_DAYS` 可调，`0` 永久保留），
  `latest.*` 永远保留
- 数据目录、录音、token 文件在代码里强制 `700`/`600` 权限（仅当前用户可读，
  防多账户机器和备份同步误伤）

**访问控制**：
- 服务**只做两件事**：收录音、分析录音。没有文件读写、没有终端、没有截屏
- 所有路由藏在 `/<TOKEN>/` 后面；token 泄露就删掉数据目录里的
  `token.txt` 重启，自动换新
- 默认只监听 `127.0.0.1`；`CHU_VOICE_LAN=1` 开启局域网直传后是
  **http 明文（无传输加密）**，只在可信 Wi-Fi 下用；同一 Wi-Fi 的人
  没有 token 也传不了文件
- 隧道暴露到公网的，也只是这两个 token 保护的语音端点

**操作卫生**：
- 启动横幅只显示 token 前 4 位；`start.sh` 打印的完整连接器地址
  **含 token，当作密码保管**，别截图、别贴群
- 服务日志已对 token 脱敏（日志里是 `***`），但会记录客户端 IP，
  日志文件不要随便外发

## 已知坑（都是实踩过的）

- **iOS 快捷指令手打 URL 容易把 `/` 打成 `\`**：症状是返回
  `{"error":"not found",...,"path":"\\..."}`。直接长按粘贴，别手打
- **token 区分大小写**：`Fb` 和 `fb` 是两个东西
- **免费快速隧道（`*.trycloudflare.com`）在部分地区网络不可达**（TLS 被重置）：
  在家就用局域网地址；在外面就换成自己的域名 + 命名隧道：
  ```bash
  cloudflared tunnel create myvoice
  cloudflared tunnel route dns myvoice voice.你的域名.com
  # config.yml 加 ingress: voice.你的域名.com -> http://localhost:8770
  ```
- **Gemini 偶发 503**：服务端已内置指数退避重试 ×3，等它自己好
- **Mac 的局域网 IP 会变**（DHCP）：路由器里给 Mac 绑个固定 IP 一劳永逸

## 手动测试

```bash
TOKEN=$(cat ~/.chu-voice/token.txt)
curl http://127.0.0.1:8770/$TOKEN/health
curl -X POST http://127.0.0.1:8770/$TOKEN/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl -X POST http://127.0.0.1:8770/$TOKEN/voice \
  -F "file=@test.m4a;type=audio/mp4"
```

## License

[MIT](LICENSE)
