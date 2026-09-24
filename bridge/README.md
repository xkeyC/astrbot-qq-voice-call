# QQ AV Bridge

该目录提供 AstrBot QQ Voice Call 所需的公开桥接源码。它把 QQ 内部 AVSDK
事件限制在两个本地进程之间，只向 AstrBot 暴露通话状态、拨号/挂断接口，以及
经 WebSocket `/v1/stream` 传输的通话音频（见 [`PROTOCOL.md`](PROTOCOL.md)）。

## 组件

- `napcat-plugin/`：按 NapCat 官方外部插件结构发布，监听 QQ 来电事件、转发
  AVSDK 网络数据、自动接听，并在 `127.0.0.1:6110` 提供最小状态接口。
- `av-host/`：由第二个 QQ/Electron 进程加载用户安装目录内的
  `libAVSDKPlugin.so`，默认监听 `127.0.0.1:6111`。
- `scripts/`：安装、卸载、诊断、隔离 PulseAudio 和两个进程的启动脚本。
- `tests/`：验证原生 Accept 参数映射、敏感字段脱敏和配置边界。

两个 HTTP 服务共享至少 32 字节的 Bearer Token。AV Host 只监听回环地址；NapCat 插件的控制端口默认也只监听回环地址，可以通过 `ASTRBOT_QQ_CALL_BRIDGE_HOST` 改为容器网络地址。
未鉴权的 `/healthz` 只返回 `{ "ok": true }`，不包含运行状态。

## 安装

依赖命令：`pulseaudio`、`pactl`、`parec`、`pacat`、`xvfb-run`、`curl`。以
Debian/Ubuntu 为例：

```bash
sudo apt-get install pulseaudio pulseaudio-utils xvfb curl
```

定位 QQ 安装目录和它内部的 NapCat 目录，然后执行：

```bash
./scripts/install.sh \
  --qq-dir /opt/QQ \
  --napcat-dir /opt/QQ/resources/app/app_launcher/napcat \
  --check

./scripts/install.sh \
  --qq-dir /opt/QQ \
  --napcat-dir /opt/QQ/resources/app/app_launcher/napcat
```

可用 `--install-dir` 修改默认安装位置。对应环境变量为：

- `ASTRBOT_QQ_CALL_BRIDGE_DIR`
- `ASTRBOT_QQ_CALL_NAPCAT_DIR`
- `ASTRBOT_QQ_CALL_QQ_DIR`
- `ASTRBOT_QQ_CALL_AVSDK_PATH`
- `ASTRBOT_QQ_CALL_BRIDGE_TOKEN` 或 `ASTRBOT_QQ_CALL_BRIDGE_TOKEN_FILE`
- `ASTRBOT_QQ_CALL_BRIDGE_HOST` / `ASTRBOT_QQ_CALL_BRIDGE_PORT`：桥的监听地址和端口
- `ASTRBOT_QQ_CALL_BRIDGE_CONNECT_HOST`：可选，AV Host 回连桥用的地址（默认：监听地址是
  `0.0.0.0`/`::` 时用 `127.0.0.1`，否则用监听地址）
- `ASTRBOT_QQ_CALL_AV_HOST_HOST` / `ASTRBOT_QQ_CALL_AV_HOST_PORT`
- `ASTRBOT_QQ_CALL_AVSDK_LOGS=1`：调试模式，逆向和排查用（记录里有 uid 和通话参数，默认关闭）。
  AVSDK 日志行保存在 `/v1/status` 里；另有 `GET /v1/debug?since=<ISO 时间>`（AVSDK 原始输出、
  内核事件、发出的指令）、`POST /v1/debug/clear`、`POST /v1/debug/invoke`
  （`{"command", "params"}`，任意 AVSDK 指令）和 `POST /v1/debug/kernel`
  （`{"service" 或 "api", "method", "args"}`，调用 NapCat 内核服务方法；不给 `method` 时列出方法）。
  这些接口同样要求 Token

AV Host 只能监听 `127.0.0.1`、`::1` 或 `localhost`。桥的监听地址默认也是回环地址；
AstrBot 在另一个容器里时可以改成 `0.0.0.0` 或容器地址，所有接口（`/healthz` 除外）
仍然要求 Token，不要把端口暴露到公网。

AstrBot 没有连上 `/v1/stream` 时，桥不会自动接听来电（接了对方也只能听到静音）。

如果 QQ Loader 已被旧的 AV Host 集成改写，安装器会拒绝叠加 Hook。请先找到
升级前保存的干净 Loader，用 `--original-loader /path/to/clean-loader.js` 明确
指定；该文件会成为本插件卸载时恢复的基线。

## Loader Hook 与回滚

QQ 的打包 Electron 入口固定为 `resources/app/loadNapCat.js`。第二个进程要加载
AV Host，因此安装器会：

1. 校验 QQ 可执行文件、`package.json`、Loader 和 AVSDK 都位于指定目录；
2. 把原 Loader 原样备份为 `loadNapCat.astrbot-qq-call.backup.cjs`；
3. 写入带 `ASTRBOT_QQ_CALL_LOADER_HOOK_V1` 标记的最小分流代码；
4. 普通 QQ 进程继续加载备份的原入口，只有设置
   `ASTRBOT_QQ_CALL_AV_HOST=1` 的第二个进程才加载 AV Host。

`uninstall.sh` 只在标记仍然匹配时恢复备份。如果 QQ 升级改写了 Loader，它会
停止并保留备份，不会覆盖新文件。该 Hook 不绕过 QQ 鉴权，也不复制登录态。

## 启动与音频设备

推荐由服务管理器分别监管 `run-av-host.sh` 与普通 NapCat 进程。快速验证可直接：

```bash
ASTRBOT_QQ_CALL_BOT_UIN="机器人QQ号" /安装目录/scripts/run-napcat.sh
```

脚本创建仅当前用户可访问的 PulseAudio socket，并提供：

- `astrbot_qq_speaker.monitor`：AstrBot 的 ASR 输入；
- `astrbot_qq_mic`：AstrBot 的 TTS 输出；
- `astrbot_qq_mic_source`：QQ 使用的默认麦克风 source。

AstrBot 插件只需要桥的地址和 Token（`bridge_url`、`bridge_token_file`），音频经 `/v1/stream` 传输，AstrBot 端不需要 PulseAudio。AstrBot 在另一个容器里时，启动桥前设置 `ASTRBOT_QQ_CALL_BRIDGE_HOST=0.0.0.0`（或容器网络地址）。

## 诊断与升级

启动后运行：

```bash
/安装目录/scripts/doctor.sh
```

诊断会检查依赖、文件、Loader 标记、PulseAudio、AV Host 和带鉴权的 NapCat
Bridge。QQ 或 NapCat 升级后，重新运行安装器和诊断，并用测试账号完成一次来电、
接通、双向音频和挂断测试。不要未经验证直接替换正在运行的生产链路。

## 不包含的内容

本仓库不包含 QQ、NapCat、AVSDK 二进制、登录态、QQ 号或任何 API Key。
用户需自行遵守 QQ、NapCat、云模型供应商及当地法律的适用条款。
