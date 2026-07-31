# MaiBot QQ Voice Call

一个面向 MaiBot 1.x 的 QQ 语音电话插件。它把 QQ 通话媒体链路交给独立的
NapCat AV 桥处理，把人物身份、近期消息、记忆查询和模型路由留在 MaiBot
插件 SDK 内。

当前版本只提供生产用的 API 链路：

1. 外部 NapCat AV 桥处理来电、接听和 QQ 音频设备。
2. Qwen 实时 ASR 在用户说话时持续上传音频。
3. `ctx.person`、`ctx.chat` 和 `ctx.message` 组装来电者上下文。
4. `ctx.llm` 调用 MaiBot 已配置的电话回复模型，不经过 Planner。
5. Qwen3 实时克隆 TTS 将首包直接送进 QQ 麦克风。

仓库不包含 NapCat、QQ、IndexTTS、GPT-SoVITS 或其他本地推理模型。

## 状态

这是从一套麦麦 QQ 语音通话部署迁移出的 `0.1.0` 版本。插件骨架、SDK 上下文、
实时 ASR、LLM、实时 TTS、VAD、插话打断和运行状态 API 已迁移；NapCat
AV 桥仍作为外部组件部署。

## 要求

- MaiBot `1.0.0` 及以上
- `maibot-plugin-sdk` `2.5.4` 及以上、`3.0` 以下
- Python 3.12+
- Linux、PulseAudio/PipeWire Pulse 兼容层、`parec` 和 `pacat`
- 一个实现 [`bridge/PROTOCOL.md`](bridge/PROTOCOL.md) 的本地 QQ AV 桥
- DashScope 实时 ASR 与实时克隆 TTS 权限
- MaiBot 模型管理中可用的电话回复模型

## 安装

将仓库克隆到 MaiBot 的第三方插件目录：

```bash
cd /path/to/MaiBot/plugins
git clone https://github.com/ClaudiaGardner/maibot-qq-voice-call.git
```

把密钥放在 MaiBot 进程环境中，不要写进仓库或 `config.toml`：

```bash
export DASHSCOPE_API_KEY="..."
export MAIBOT_QQ_CALL_VOICE_ID="..."
export MAIBOT_QQ_CALL_BRIDGE_TOKEN="..."
```

启动 MaiBot 后，在 WebUI 插件配置中至少填写：

- `plugin.enabled = true`
- `plugin.account_id`：机器人 QQ 号
- `chat.task_name = "replyer"`
- 在 MaiBot 模型管理中把 `ali-glm-5.2` 等电话模型加入 `replyer.model_list`
- 正确的 PulseAudio capture/playback device
- 正确的本地 AV 桥地址

Runner 会根据配置模型生成 `config.toml`。完整示例见
[`examples/config.example.toml`](examples/config.example.toml)。

## 插件 API

- `github.claudiagardner.maibot-qq-voice-call.get_call_status`：通话状态、最近 ASR/LLM/TTS 耗时
- `github.claudiagardner.maibot-qq-voice-call.test_phone_reply`：不公开的电话回复测试入口

## 安全

AV 桥应只监听回环地址，并强制 Bearer Token。插件只从环境变量或显式
Token 文件读取凭据。发布前请阅读 [`SECURITY.md`](SECURITY.md)。

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

架构和迁移说明分别见
[`docs/architecture.md`](docs/architecture.md) 与
[`docs/migration.md`](docs/migration.md)。
版本变化见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可证

本项目使用 GPL-3.0-only。第三方组件说明见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
