#!/usr/bin/env bash
# Installs (idempotently) and starts the bridge's audio server and AV host,
# then hands over to the NapCat image's own entrypoint, which starts QQ.
#
# Environment (besides the NapCat image's own, e.g. ACCOUNT, WEBUI_TOKEN):
#   ASTRBOT_QQ_CALL_BRIDGE_TOKEN    bridge token; otherwise one is generated
#                                   into /app/qqcall/runtime/control.token
#   ASTRBOT_QQ_CALL_BRIDGE_HOST     bridge listen address (default 0.0.0.0)
set -euo pipefail

cd /app
# NapCat is unpacked by the NapCat entrypoint on first start, but the bridge
# installs into it, so unpack it here first (the same way).
if [[ ! -f napcat/napcat.mjs ]]; then
  unzip -q -o NapCat.Shell.zip -d napcat
fi
mkdir -p napcat/plugins napcat/config qqcall
# Volumes are created by root; the NapCat entrypoint chowns /app too, but
# only after the audio server below needs its ~/.config.
chown -R napcat:napcat /app /opt/QQ/resources/app

# Run QQ as the same user as the bridge (the NapCat image defaults to root).
export NAPCAT_UID=${NAPCAT_UID:-$(id -u napcat)} NAPCAT_GID=${NAPCAT_GID:-$(id -g napcat)}
install_dir=/app/qqcall
gosu napcat /opt/astrbot-qq-voice-call/bridge/scripts/install.sh \
  --napcat-dir /app/napcat --qq-dir /opt/QQ --install-dir "$install_dir"
# Nothing runs yet in a fresh container: drop the previous audio server's
# pid file and socket kept in the runtime volume.
rm -f "$install_dir/runtime/pulse/pid" "$install_dir/runtime/pulse/native"
gosu napcat "$install_dir/scripts/audio-control.sh" start
gosu napcat bash -c "nohup '$install_dir/scripts/run-av-host.sh' >>'$install_dir/logs/av-host.log' 2>&1 &"

export ASTRBOT_QQ_CALL_BRIDGE_HOST=${ASTRBOT_QQ_CALL_BRIDGE_HOST:-0.0.0.0}
export ASTRBOT_QQ_CALL_BRIDGE_DIR=$install_dir
export ASTRBOT_QQ_CALL_QQ_DIR=/opt/QQ
export ASTRBOT_QQ_CALL_AVSDK_PATH=/opt/QQ/resources/app/avsdk/libAVSDKPlugin.so
export ASTRBOT_QQ_CALL_BRIDGE_TOKEN_FILE=$install_dir/runtime/control.token
export PULSE_SERVER=unix:$install_dir/runtime/pulse/native
exec bash /app/entrypoint.sh
