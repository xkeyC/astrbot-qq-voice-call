# AstrBot QQ Voice Call

让 AstrBot 接听和拨打 QQ 语音电话。通话由 Codex 实时语音全双工完成：边听边说、可以随时打断；遇到需要查资料或动手的事，交给后台的语音 Agent 处理。

本项目从 [maibot-qq-voice-call](https://github.com/ClaudiaGardner/maibot-qq-voice-call)（GPL-3.0）改造而来：保留了它的 NapCat AV 桥，原来串联的 DashScope ASR → LLM → TTS 整体换成了 AstrBot 的实时语音会话（和 Mumble 平台用的是同一套）。

> 需要 **AstrBot Codex fork**（提供 `astrbot.core.voice`），并在 Codex 执行器里登录一个包含 Codex 语音的 ChatGPT 订阅账号。上游 AstrBot 用不了。
>
> 当前版本要求 fork 包含 `PcmMedia(buffer_seconds=...)` 和 `VoiceOptions.media_tcp`（`qq-voice` 分支的 `eedf46bb`、`2f5f2a66` 及之后）；更早的核心上接通时会报 TypeError。

## 工作方式

```text
QQ ──(AVSDK)── NapCat AV 桥 ──WebSocket /v1/stream──> AstrBot 插件 ──WebRTC──> Codex realtime
                 │  文本帧：通话状态                         │
                 │  二进制帧：PCM 音频（双向）                 └─ 语音 Agent 线程（配套来电者私聊的工具、权限、记忆）
                 └─ PulseAudio 虚拟声卡 ⇄ QQ 的扬声器和麦克风
```

- **来电**：桥自动接听。接通后插件为来电者开一个实时语音会话，bot 先开口打招呼。
- **去电**：LLM 工具 `qq_voice_call(purpose, user_id)` 负责拨号。对方接听后，bot 根据 `purpose` 说明来意。
- **挂断**：工具 `qq_voice_hangup`。通话里的后台 Agent 在对方道别或事情说完时调用；另外，超过 `idle_hangup_seconds`（默认 120 秒）没听到对方说话也会自动挂断。
- **会话配套**：每通电话配套到 `<aiocqhttp 平台 ID>:FriendMessage:<QQ号>`。语音 Agent 拿到的是普通成员在这个私聊里能用的工具、审批规则和执行环境；开启记忆时，能读全局记忆和这个私聊的记忆。语音线程按来电者持久化，下次来电接着用。
- **权限**：`qq_voice_call` 是普通插件工具，谁能用、能不能拨给别人，都由现有的工具权限规则决定。

## 状态

| 功能 | 状态 |
|---|---|
| 接听来电、全双工对话、打断 | 已在 NapCat Docker 里用真实 QQ 来电验证（Codex 与本地 omni 两种后端） |
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
- `voice_backend`：语音后端，默认 `codex_realtime`（Codex 实时语音）；改为 `minicpm_omni` 就用自己部署的 llama.cpp-omni（MiniCPM-o）服务端在本地完成对话，查询、执行等任务仍交给语音 Agent 线程（Codex）。这时还要填 `omni_url`，可选填 `omni_ref_audio`（音色克隆）、`omni_tool_filler`、`omni_asr_dir`。omni 服务端同一时间只服务一个会话，被拒时插件会挂断这通电话

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
