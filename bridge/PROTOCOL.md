# QQ AV Bridge Protocol

桥把 QQ/AVSDK 的非公开实现隔离在独立进程里，对 AstrBot 只暴露通话状态和通话音频。

## 鉴权

除了只返回存活布尔值的 `GET /healthz`，所有请求（包括 WebSocket 升级）都必须携带：

```http
Authorization: Bearer <random-token>
```

Token 至少 32 字节随机值。控制端口默认只监听 `127.0.0.1`；用 `ASTRBOT_QQ_CALL_BRIDGE_HOST` 可以改成容器网络地址，但不要暴露到公网。

另外两个鉴权端点只供桥内部使用：AV Host 的 `POST /v1/invoke`（固定只监听回环地址）和 NapCat 插件的 `POST /v1/avsdk/output`。它们不属于 AstrBot 接口。

## 当前通话

```http
GET /v1/calls/current
```

```json
{
  "data": {
    "phase": "connected",
    "inviteAt": "2026-07-30T12:00:00.000Z",
    "callerUid": "platform-internal-uid",
    "callerUin": "123456789",
    "callerName": "Caller"
  }
}
```

群通话还带 `scene`（`3`；一对一为 `1`）和 `groupId`（群号），`caller*` 指邀请 bot 的人。

`phase` 可能的值：`idle`、`dialing`（去电已发出）、`ringing`、`accepting`、`accepted`、`connected`、`ended`、`error`。只有 `connected` 表示 AVSDK 已经进入房间，可以收发音频。`inviteAt` 用来区分每一通电话。去电时 `outgoing` 为 `true`，`callerUin`、`callerName` 指的是对方。

## 通话流

```http
GET /v1/stream   (WebSocket)
```

- **文本帧**（桥 → AstrBot）：`{"type": "call", "call": {...}}`，`call` 的字段同上。连接建立后立即发一次，之后每次状态变化再发。
- **二进制帧**（双向）：通话音频，格式为 16 位小端、单声道、48 kHz PCM。
  - 桥 → AstrBot：对方的声音，只在 `connected` 期间发送，每帧长度不固定，但都是整数个采样。
  - AstrBot → 桥：bot 的声音，桥直接写入 QQ 的麦克风，播放缓冲约 60 ms。
- 客户端发的帧不能分片。支持 ping/pong 和 close。

有客户端连着、通话处于 `connected` 时，桥端才会运行 `parec`/`pacat`。

## 拨号与挂断

```http
POST /v1/calls/dial     {"uin": "123456789", "startCall": {...可选覆盖...}}
POST /v1/calls/hangup
```

- **拨号**：把 QQ 号转换成 uid 后，调用 AVSDK 指令 4（`StartCall`），参数是一个 JSON 字符串。接下来的状态依次为 `dialing` →（对方响铃）`ringing` → `accepted` → `connected`，或者变为 `ended`/`error`。60 秒内没有接通就自动挂断。可能的错误码：`400`（QQ 号不合法）、`409`（已经在通话）、`503`（AV Host 还没登录）、`404`（查不到 uid）。
- **挂断**：一对一通话调用 AVSDK 指令 10（`Close`），参数为 `[1, 对方 uid, 0]`，响铃中的去电和已接通的通话都能挂断；群通话调用指令 8（`Quit`），参数为 `[3, 0]`，只有 bot 退出，其他人继续（用 `Close` 只会断开音频，账号仍留在通话里）。返回 `{"closed": true|false}`。

群通话里，AVSDK 报告 bot 自己离开房间（输出 20009 `[0, uid, …]`）或房间出错（20003）时，通话结束，桥会补发一次 `Quit`，否则 AVSDK 会忽略下一次邀请。其他人都离开（20008 进房、20009 离房）后，bot 也会退出。

AV Host 登录（指令 1）的参数为 `[uid, QQ 号, 替代 uid, 数据目录, 机器 id]`。替代 uid 必须留空：AVSDK 用它作为进房身份，填成 QQ 号时，群通话里会多出一个不认识的成员 “0”。

以下 `StartCall` 的字段来自对 `libAVSDKPlugin.so` 的静态分析（QQ Linux 3.2.30），**还没有经过实机验证**，所以允许用 `startCall` 字段覆盖：`scene_id` 1（好友）、`self_uid`、`invite_uids` 与 `invite_count`、`relation_id` `"0"`、`sub_business_type` 3（纯语音）、`invite_reason` 0、`invite_original` 0、`audio_scene` 0、`use_ntrtc_dsp` false、`ntrtc_ai_denoise_update_model` ""。设置 `ASTRBOT_QQ_CALL_AVSDK_LOGS=1` 后，AVSDK 的日志行（输出 20050）会保存在 `GET /v1/status` 的 `avHost.logs` 里，其中会回显它解析到的 StartCall 和来电参数，排查时看这里；默认不保存（含 uid）。

AVSDK 用来判断去电状态的输出：`4` 是 StartCall 结果；`20007` 是邀请的应答；`20021` 表示对方开始响铃；`20020` 表示对方接听；`20004` 表示已进房；`20018`、`20019`、`20022`、`20011`、`20005` 都表示通话结束（依次为拒接、对方取消、通话关闭、房间销毁、连接超时）。

## 音频设备

- 对方的声音由 QQ 输出到 PulseAudio sink `astrbot_qq_speaker`，桥读它的 monitor source。
- bot 的声音由桥播放到 sink `astrbot_qq_mic`，QQ 把 `astrbot_qq_mic_source` 当作麦克风。
- 设备名可以用 `ASTRBOT_QQ_CALL_CAPTURE_DEVICE`、`ASTRBOT_QQ_CALL_PLAYBACK_DEVICE` 覆盖。

桥不会向 AstrBot 返回鉴权票据、QQ Cookie、登录态、Native 指针或 AVSDK 原始事件载荷（调试开关打开时的日志行除外，见上）。

没有客户端连在 `/v1/stream` 上时，桥不自动接听来电。

## 兼容性

本协议只规定公开边界，不规定 NapCat/QQ 的内部方法。NapCat 与 QQ 的版本差异由桥自己处理。
