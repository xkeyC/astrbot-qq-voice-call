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

`phase` 可能的值：`idle`、`ringing`、`accepting`、`accepted`、`connected`、`ended`、`error`。只有 `connected` 表示 AVSDK 已经进入房间，可以收发音频。`inviteAt` 用来区分每一通电话。对于去电，`callerUin` 和 `callerName` 指的是对方。

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
POST /v1/calls/dial     {"uin": "123456789"}
POST /v1/calls/hangup
```

目前都返回 `501`：AVSDK 发起和结束通话的指令还没有逆向出来。实现后，拨号成功返回 `200`，接下来的通话状态照常通过通话流推送。

## 音频设备

- 对方的声音由 QQ 输出到 PulseAudio sink `astrbot_qq_speaker`，桥读它的 monitor source。
- bot 的声音由桥播放到 sink `astrbot_qq_mic`，QQ 把 `astrbot_qq_mic_source` 当作麦克风。
- 设备名可以用 `ASTRBOT_QQ_CALL_CAPTURE_DEVICE`、`ASTRBOT_QQ_CALL_PLAYBACK_DEVICE` 覆盖。

桥不会向 AstrBot 返回鉴权票据、QQ Cookie、登录态、Native 指针或 AVSDK 原始事件载荷。

## 兼容性

本协议只规定公开边界，不规定 NapCat/QQ 的内部方法。NapCat 与 QQ 的版本差异由桥自己处理。
