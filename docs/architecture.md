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
                                                       │ hangup
                                                       ▼
                                      clean transcript + summarize
                                                       │
                           gateway.route_message + maisaka.context.append
                                                       │
                                  caller private chat + memory learning
```

## Trust boundaries

### MaiBot plugin runner

负责：

- 强类型配置与热重载
- 来电者 QQ 身份到 MaiBot `person_id` 的解析
- 私聊 stream 的打开与近期消息查询
- 无 Planner 的低延迟 LLM 调用
- ASR/TTS 会话预热、VAD、插话打断和链路指标
- 挂断后的有效对话清洗、摘要、事实提取与私聊记忆回写

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
- 当前 Qwen3-TTS Realtime 原始协议没有记录可取消已开始 `response` 的同会话事件；
  `input_text_buffer.clear` 只清理未提交文本。因此插话会关闭播放连接并立即后台预热，
  不套用仅适用于 Qwen-Audio-TTS/CosyVoice 的 `finish-task directive=cancel`。

## Turn lifecycle

1. 桥报告 `connected`。
2. 插件解析 caller、person、stream、记忆和近期消息。
3. VAD 检测语音并实时追加 ASR 音频。
4. 句尾静音达到阈值后提交 ASR。
5. 非空 ASR 文本直接进入电话回复模型；模型可用 `[WAIT]` 放弃不可靠输入。
6. `ctx.llm.generate` 生成短回复。
7. TTS 首包立即写入 QQ 麦克风。
8. 对方持续说话时终止当前 TTS，并预热下一条 TTS 会话。

## Hangup memory lifecycle

1. 每个已接通来电按 `inviteAt` 建立独立的内存轮次缓冲，只记录通过二次过滤且
   TTS 已完整播放的问答对；插话中断的回复不进入电话历史或记忆。
2. 挂断时立即冻结快照并清空通话状态，后台任务再调用 `ctx.llm.generate` 生成摘要。
3. 人物事实必须携带可在“对方”原话中逐字验证的证据，否则丢弃。
4. `ctx.gateway.route_message` 注入以 `[QQ语音通话记录]` 开头的合成私聊消息。
   插件内部 Command 拦截该消息的回复阶段，使 Host 保留正常入站持久化和后续
   记忆学习，同时不向 QQ 发送额外文字。
5. `ctx.maisaka.context.append` 用相同消息 ID 将记录立即加入当前上下文。

归档消息 ID 由来电标识、来电者 QQ 和起止时间生成稳定 SHA-256 摘要，Host
可据此去重。插件不直接访问 MaiBot 数据库。

## Known upstream gap

SDK 当前提供 `generate_with_tools()` 和工具定义查询，但没有一个“跳过
Planner、同时执行完整 Tool Call 循环”的统一电话会话入口。`0.1.0` 因而
选择无 Planner 的普通 `ctx.llm.generate`。该缺口适合后续提一个小型
MaiBot 上游 PR，而不是把 QQ/NapCat 细节并入核心。
