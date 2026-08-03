# QQ AV Bridge Protocol

插件把 QQ/AVSDK 的非公开实现隔离在独立桥接进程中。桥和插件必须运行在
同一受信任主机；默认通过回环 HTTP 传递状态，通过 PulseAudio 设备传递音频。

## 鉴权

除仅返回存活布尔值的 `GET /healthz` 外，所有请求必须携带：

```http
Authorization: Bearer <random-token>
```

Token 至少使用 32 字节随机值。服务不得监听公网地址。

仓库实现额外使用两个仅供 Bridge 内部调用的鉴权端点：AV Host 的
`POST /v1/invoke` 和 NapCat Bridge 的 `POST /v1/avsdk/output`。它们不是
MaiBot 插件 API，且命令白名单仅包含登录、接听和 Kernel 数据转发。

## 当前通话

```http
GET /v1/calls/current
```

成功响应：

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

`phase` 支持：

- `idle`
- `ringing`
- `accepting`
- `accepted`
- `connected`
- `ended`
- `error`

只有 `connected` 表示 AVSDK 已进入房间、音频设备可以收发。

## 音频边界

- 桥把对端声音输出到一个 PulseAudio sink；插件读取其 monitor source。
- 插件把 TTS 播放到一个 PulseAudio sink；桥把该 sink 作为 QQ 麦克风输入。
- 默认设备名分别为 `maibot_qq_speaker.monitor`、`maibot_qq_mic` 和
  `maibot_qq_mic_source`。
- ASR 输入格式为单声道 PCM S16LE 16 kHz。
- 默认 TTS 输出格式为单声道 PCM S16LE 24 kHz。

桥不得把鉴权票据、QQ Cookie、登录态、Native 指针或 AVSDK 原始事件载荷
返回给插件。

## 兼容性

本协议只规定公开边界，不规定 NapCat/QQ 内部方法。桥实现应单独处理具体
NapCat 与 QQ 版本变化。
