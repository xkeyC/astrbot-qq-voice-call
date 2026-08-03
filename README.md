# MaiBot QQ Voice Call

一个面向 MaiBot 1.x 的 QQ 语音电话插件。它把 QQ 通话媒体链路交给独立的
NapCat AV 桥处理，把人物身份、近期消息、记忆查询和模型路由留在 MaiBot
插件 SDK 内。

它为 MaiBot 增加 QQ 实时语音通话入口，让电话里的麦麦继续复用 MaiBot
的人设、记忆和模型体系。

<p align="center">
  <img src="docs/assets/qq-voice-call-demo.png" alt="麦麦 QQ 语音通话界面" width="360">
</p>

当前版本只提供生产用的 API 链路：

1. 外部 NapCat AV 桥处理来电、接听和 QQ 音频设备。
2. Qwen 实时 ASR 在用户说话时持续上传音频。
3. `ctx.person`、`ctx.chat` 和 `ctx.message` 组装来电者上下文。
4. `ctx.llm` 调用 MaiBot 已配置的电话回复模型，不经过 Planner。
5. Qwen3 实时克隆 TTS 将首包直接送进 QQ 麦克风。
6. 挂断后清洗有效对话，生成摘要和有原话证据的关键人物事实，并静默写回
   对应来电者的 MaiBot 私聊。

仓库不包含 NapCat、QQ、IndexTTS、GPT-SoVITS 或其他本地推理模型。

## 状态

`0.3.1` 同时提供 MaiBot 插件和可安装的 QQ AV Bridge 源码。Bridge 以独立
NapCat 插件加载，不修改 `napcat-plugin-builtin`；QQ Loader Hook 只用于启动
第二个 AVSDK Host，安装时自动备份，卸载时恢复原文件。

仓库仍不分发 QQ、NapCat 或 `libAVSDKPlugin.so`。Bridge 只加载用户自己的 QQ
安装所附带的 AVSDK，因此 QQ/NapCat 升级后应先运行诊断并重新做一次来电测试。

## 挂断后的记忆回写

每次已接通的电话结束后，插件会在后台完成以下操作，不阻塞下一次来电：

1. 再次过滤语气词、噪声误识别、重复内容、未完成句子、`[WAIT]` 和内部控制文本。
2. 只保留对方有效发言与麦麦实际开始播放的回复，整理成角色明确的通话记录。
3. 通过 MaiBot 的模型任务生成简短摘要；人物事实必须带有对方逐字原话证据，
   不把麦麦的回复或模型推断当成来电者事实。
4. 通过消息网关把记录写进该 QQ 来电者的私聊历史，并用内部 Command 静默拦截，
   因而挂断后不会额外向 QQ 发送一条文字回复。
5. 同时把同一记录追加到 Maisaka 当前上下文；持久化后的私聊记录会继续作为
   MaiBot 正常记忆学习链路的对话证据。

这一过程只使用公开插件 SDK，不直接读写 MaiBot 数据库。可在 WebUI 的
`memory` 配置段关闭写回、调整摘要任务或限制归档长度。

## 要求

- MaiBot `1.0.0` 及以上
- `maibot-plugin-sdk` `2.5.4` 及以上、`3.0` 以下
- Python 3.12+
- Linux、PulseAudio/PipeWire Pulse 兼容层、`parec` 和 `pacat`
- NapCat `4.14.0` 及以上与 Linux QQ（需包含 `libAVSDKPlugin.so`）
- PulseAudio、`pactl`、`parec`、`pacat`、`xvfb-run` 和 `curl`
- DashScope 实时 ASR 与实时克隆 TTS 权限
- MaiBot 模型管理中可用的电话回复模型

## 安装 MaiBot 插件

将仓库克隆到 MaiBot 的第三方插件目录：

```bash
cd /path/to/MaiBot/plugins
git clone https://github.com/ClaudiaGardner/maibot-qq-voice-call.git
```

把密钥放在 MaiBot 进程环境中，不要写进仓库或 `config.toml`：

```bash
export DASHSCOPE_API_KEY="..."
export MAIBOT_QQ_CALL_VOICE_ID="..."
```

## 安装 QQ AV Bridge

先安装并确认 Linux QQ 与 NapCat 能正常登录。然后在仓库根目录运行：

```bash
./bridge/scripts/install.sh \
  --napcat-dir /path/to/QQ/resources/app/app_launcher/napcat \
  --qq-dir /path/to/QQ \
  --check

./bridge/scripts/install.sh \
  --napcat-dir /path/to/QQ/resources/app/app_launcher/napcat \
  --qq-dir /path/to/QQ
```

安装器会完成以下操作：

- 把 `napcat-plugin-maibot-qq-voice-call` 安装到 NapCat 的独立 `plugins/` 目录；
- 在 `~/.local/share/maibot-qq-voice-call` 安装 AV Host 与运行脚本；
- 创建权限为 `0600` 的 32 字节随机 Bridge Token；
- 备份 QQ 原始 Loader，再安装带明确标记的最小可逆 Hook。

安装完成后，把终端显示的 Token 文件路径填入 MaiBot 的
`bridge.token_file`，再用桥接脚本启动机器人 QQ：

```bash
MAIBOT_QQ_CALL_BOT_UIN="机器人QQ号" \
  ~/.local/share/maibot-qq-voice-call/scripts/run-napcat.sh
```

验证所有组件：

```bash
~/.local/share/maibot-qq-voice-call/scripts/doctor.sh
```

卸载默认保留运行目录与 Token，便于恢复；`--purge` 才会一并删除：

```bash
~/.local/share/maibot-qq-voice-call/scripts/uninstall.sh
```

现有服务、容器、自定义端口和升级兼容说明见
[`bridge/README.md`](bridge/README.md)。

## 配置

启动 MaiBot 后，在 WebUI 插件配置中至少填写：

- `plugin.enabled = true`
- `plugin.account_id`：机器人 QQ 号
- `chat.task_name = "utils"`（仓库默认值）
- 在 MaiBot 模型管理中确认 `deepseek-v4-flash` 位于 `utils.model_list`
- `memory.summary_task_name = "utils"`（默认复用同一轻量模型任务）
- `bridge.token_file`：安装器输出的 Token 文件路径
- `audio.capture_device = "maibot_qq_speaker.monitor"`
- `audio.playback_device = "maibot_qq_mic"`
- `audio.pulse_server`：安装目录下的 `runtime/pulse/native` Unix socket

Runner 会根据配置模型生成 `config.toml`。完整示例见
[`examples/config.example.toml`](examples/config.example.toml)。

## 插件 API

- `github.claudiagardner.maibot-qq-voice-call.get_call_status`：通话状态、最近 ASR/LLM/TTS
  耗时及最后一次记忆写回结果
- `github.claudiagardner.maibot-qq-voice-call.test_phone_reply`：不公开的电话回复测试入口

## 安全

AV 桥强制只监听回环地址，并对状态与控制接口校验 Bearer Token。安装器不会
读取或复制 QQ 登录态，也不会把 Token 写进 Git 仓库。Loader Hook 和 AVSDK
属于 QQ/NapCat 版本敏感集成；升级后二次验证前不要直接切换生产账号。
更多边界见 [`SECURITY.md`](SECURITY.md)。

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
node --test bridge/tests/*.test.mjs
bash -n bridge/scripts/*.sh
```

架构和迁移说明分别见
[`docs/architecture.md`](docs/architecture.md) 与
[`docs/migration.md`](docs/migration.md)。
版本变化见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可证

本项目使用 GPL-3.0-only。第三方组件说明见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
