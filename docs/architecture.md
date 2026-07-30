# Architecture

```text
QQ / NapCat
    │ call signalling + native audio
    ▼
External AV bridge ─────── GET /v1/calls/current ──────┐
    │                                                  │
    ├─ caller audio → Pulse monitor → VAD → Qwen ASR   │
    │                                                  ▼
    │                                            MaiBot plugin
    │                                      ctx.person / chat / message
    │                                                  │
    │                                             ctx.llm.generate
    │                                                  │
    └─ QQ microphone ← Pulse sink ← Qwen3 realtime TTS ◀
```

## Trust boundaries

### MaiBot plugin runner

负责：

- 强类型配置与热重载
- 来电者 QQ 身份到 MaiBot `person_id` 的解析
- 私聊 stream 的打开与近期消息查询
- 无 Planner 的低延迟 LLM 调用
- ASR/TTS 会话预热、VAD、插话打断和链路指标

插件不读取 MaiBot SQLite 文件、`bot_config.toml`、`model_config.toml` 或
WebUI Token。

### External AV bridge

负责：

- QQ 来电事件与自动接听
- AVSDK 房间生命周期
- QQ 扬声器与麦克风设备
- 屏蔽 Native/QQ/NapCat 版本差异

它只能通过回环地址和 Bearer Token 暴露最小状态接口。

### Cloud APIs

- 实时 ASR 和 TTS 直接复用预热 WebSocket，降低首包时间。
- LLM 通过 MaiBot `ctx.llm` 路由，不在插件中保存供应商 Key。
- DashScope Key 和音色 ID 只从运行环境读取。

## Turn lifecycle

1. 桥报告 `connected`。
2. 插件解析 caller、person、stream、记忆和近期消息。
3. VAD 检测语音并实时追加 ASR 音频。
4. 句尾静音达到阈值后提交 ASR。
5. 无意义文本被丢弃；明显未说完的片段最多等待数秒合并。
6. `ctx.llm.generate` 生成短回复。
7. TTS 首包立即写入 QQ 麦克风。
8. 对方持续说话时终止当前 TTS，并预热下一条 TTS 会话。

## Known upstream gap

SDK 当前提供 `generate_with_tools()` 和工具定义查询，但没有一个“跳过
Planner、同时执行完整 Tool Call 循环”的统一电话会话入口。`0.1.0` 因而
选择无 Planner 的普通 `ctx.llm.generate`。该缺口适合后续提一个小型
MaiBot 上游 PR，而不是把 QQ/NapCat 细节并入核心。
