# AstrBot QQ Voice Call

让 AstrBot 接听和拨打 QQ 语音电话。通话由 Codex 实时语音或本地语音服务全双工完成：边听边说、可以随时打断；遇到需要查资料或动手的事，交给来电者私聊的 Agent 去办（以来电者本人的身份，和文字聊天共用上下文）。

本项目从 [maibot-qq-voice-call](https://github.com/ClaudiaGardner/maibot-qq-voice-call)（GPL-3.0）改造而来：保留了它的 NapCat AV 桥，原来串联的 DashScope ASR → LLM → TTS 整体换成了 AstrBot 的实时语音会话（和 Mumble 平台用的是同一套）。

> 需要 **AstrBot Codex fork**（提供 `astrbot.core.voice`），并在 Codex 执行器里登录一个包含 Codex 语音的 ChatGPT 订阅账号。上游 AstrBot 用不了。
>
> 当前版本要求 fork 包含 `astrbot.core.voice.chat.VoiceChat`、`PcmMedia(buffer_seconds=..., trim_silence=...)` 和 `VoiceOptions.media_tcp`（AstrBot `qq-voice` 分支 `d48bea11` 及之后），以及支持 `realtime.host_routes_handoffs` 的 codex binding（codex `79c875d95` 及之后）。

## 语音后端

插件配置 `voice_backend` 二选一：

- `codex_realtime`（默认）：Codex 实时语音，经 WebRTC 连接，需要 ChatGPT 订阅。
- `local_cascade`：本地语音服务，即 [local-multimodal-infra](https://github.com/mercallureAI/local-multimodal-infra) 的 `/v1/realtime`。服务端跑 Silero VAD → SenseVoice → Qwen3-4B → IndexTTS，模型都在服务端（RTX 3060 12G 可以跑）。插件只传音频、执行它交给后台的事，并把通话信息（来电者、去电原因、怎么挂断）告诉服务端的模型。
  - 相关配置：`cascade_url`（默认 `ws://127.0.0.1:17890/v1/realtime`）、`cascade_token`（服务端配置了 `LOCAL_MCP_INFER_TOKENS` 时填写）、`cascade_ref_audio`（音色参考 WAV，留空用服务端默认音色）、`cascade_tool_filler`（交给后台时先说的一句话）。
  - 需要 AstrBot fork 包含 `astrbot.core.voice.cascade`，且 `CascadeVoiceSession` 支持 `instructions` 参数。
  - 群通话里，服务端靠语音识别出 `voice_name` 来判断是不是在叫它，所以名字最好是识别得出来的中文名。

## 工作方式

```text
QQ ──(AVSDK)── NapCat AV 桥 ──WebSocket /v1/stream──> AstrBot 插件 ──WebRTC──> Codex realtime
                 │  文本帧：通话状态                         │
                 │  二进制帧：PCM 音频（双向）                 └─ 来电者私聊的 Agent（一轮对话，来电者本人的身份）
                 └─ PulseAudio 虚拟声卡 ⇄ QQ 的扬声器和麦克风
```

- **来电**：桥自动接听。接通后插件为来电者开一个实时语音会话，bot 先开口打招呼。
- **去电**：LLM 工具 `qq_voice_call(purpose, user_id)` 负责拨号。对方接听后，bot 根据 `purpose` 说明来意。
- **群通话**：被邀请进群语音通话时，bot 也会自动加入。电话里交给后台的事作为该群聊（`<aiocqhttp 平台 ID>:GroupMessage:<群号>`）的一轮对话执行，身份是固定的“语音用户”（member 权限，分不清是谁在说话）；bot 只在被叫到名字时开口。`qq_voice_hangup` 在群通话里是退出通话，其他人继续；其他人都离开后 bot 也会退出。
- **挂断**：工具 `qq_voice_hangup`。通话里的后台 Agent 在对方道别或事情说完时调用；另外，超过 `idle_hangup_seconds`（默认 120 秒）没听到对方说话也会自动挂断。
- **和私聊共享 Agent**：电话里交给后台的事，作为来电者私聊（`<aiocqhttp 平台 ID>:FriendMessage:<QQ号>`）的一轮对话来执行，**以来电者本人的身份和权限**（管理员打来就是管理员）。上下文、人格、工具、记忆和审批都和文字聊天是同一套；文字消息和语音请求互相排队，聊天正忙时 bot 会先口头说一声。回答只念出来，不会在聊天里发文字。
- **权限**：`qq_voice_call` 是普通插件工具，谁能用、能不能拨给别人，都由现有的工具权限规则决定。

## 状态

| 功能 | 状态 |
|---|---|
| 接听来电、全双工对话、打断 | 已在 NapCat Docker 里用真实 QQ 来电验证 |
| 群通话：受邀加入、对话、退出 | 已用真实群通话验证 |
| WebSocket 音频/状态通道（可跨容器） | 已实现，有测试 |
| 主动拨号 `/v1/calls/dial`、挂断 `/v1/calls/hangup` | 已实现：指令和参数由静态逆向 `libAVSDKPlugin.so` 得出，**待实机验证** |
| 本地测试镜像 `docker/Dockerfile` | 基于 `mlikiowa/napcat-docker`，补齐 pulseaudio 与 AVSDK 依赖库并装好桥 |

## 安装

### 1. QQ AV 桥（Linux，和 NapCat/QQ 在同一台机器或同一个容器）

见 [`bridge/README.md`](bridge/README.md)。安装器会装好 NapCat 插件、AV Host、PulseAudio 虚拟设备和 Token 文件。

AstrBot 不在同一个网络命名空间里时（例如在另一个容器里），让桥监听容器网络：

```bash
ASTRBOT_QQ_CALL_BRIDGE_HOST=0.0.0.0 ~/.local/share/astrbot-qq-voice-call/scripts/run-napcat.sh
```

桥的每个接口（`/healthz` 除外）都要求 Token。不要把端口暴露到公网。

### 2. AstrBot 插件

把本仓库放到 AstrBot 的 `data/plugins/` 下（或者在 WebUI 里用仓库地址安装），然后在插件配置里填写：

- `bridge_url`：桥的地址，默认 `http://127.0.0.1:6110`
- `bridge_token` 或 `bridge_token_file`：桥的 Token
- `platform_id`：配套的 aiocqhttp 平台 ID，留空就用第一个
- `voice_name`、`voice`、`voice_prompt` 等：电话里的名字、音色和附加提示词
- `voice_backend` 及 `cascade_*`：语音后端，见上文

AstrBot 这边不需要装 PulseAudio 或 parec/pacat。

## 开发

```bash
# 插件测试需要 AstrBot Codex fork 的 Python 环境
PYTHONPATH=/path/to/AstrBot python -m pytest tests -o asyncio_mode=auto
ruff check .
node --test bridge/tests/*.test.mjs
bash -n bridge/scripts/*.sh
```

协议见 [`bridge/PROTOCOL.md`](bridge/PROTOCOL.md)，架构见 [`docs/architecture.md`](docs/architecture.md)。

## 许可证

GPL-3.0-only。原项目的署名和第三方说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
