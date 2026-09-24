#!/usr/bin/env bash
# Installs (idempotently) and starts the bridge, then QQ with NapCat.
#
# Environment:
#   ACCOUNT                         bot QQ number for quick login (optional)
#   ASTRBOT_QQ_CALL_BRIDGE_TOKEN    bridge token; generated into
#                                   /app/qqcall/runtime/control.token if unset
#   ASTRBOT_QQ_CALL_BRIDGE_HOST     bridge listen address (default 0.0.0.0)
set -euo pipefail

chown -R napcat:napcat /app
install_dir=/app/qqcall
gosu napcat /opt/astrbot-qq-voice-call/bridge/scripts/install.sh \
  --napcat-dir /app/napcat --qq-dir /opt/QQ --install-dir "$install_dir"

export ASTRBOT_QQ_CALL_BRIDGE_HOST=${ASTRBOT_QQ_CALL_BRIDGE_HOST:-0.0.0.0}
export ASTRBOT_QQ_CALL_BOT_UIN=${ACCOUNT:-}
rm -f /tmp/.X*-lock
exec gosu napcat "$install_dir/scripts/run-napcat.sh"
