#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$script_dir/common.sh"

require_command xvfb-run
require_file "$qq_dir/qq"
require_file "$avsdk_path"
require_file "$bridge_dir/av-host/host.cjs"
require_file "$token_file"
"$script_dir/audio-control.sh" start >/dev/null
ensure_runtime_dirs

export MAIBOT_QQ_CALL_AV_HOST=1
export MAIBOT_QQ_CALL_AV_HOST_ENTRY="$bridge_dir/av-host/host.cjs"
export MAIBOT_QQ_CALL_BRIDGE_DIR="$bridge_dir"
export MAIBOT_QQ_CALL_QQ_DIR="$qq_dir"
export MAIBOT_QQ_CALL_AVSDK_PATH="$avsdk_path"
export MAIBOT_QQ_CALL_BRIDGE_TOKEN_FILE="$token_file"
export PULSE_SERVER="$pulse_server"
avsdk_dir=$(dirname -- "$avsdk_path")
export LD_LIBRARY_PATH="$avsdk_dir:$avsdk_dir/bugly${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

while true; do
  set +e
  xvfb-run -a "$qq_dir/qq" \
    --no-sandbox \
    --user-data-dir="$runtime_dir/av-host-profile"
  status=$?
  set -e
  if [[ ${MAIBOT_QQ_CALL_RESTART_AV_HOST:-1} != 1 ]]; then
    exit "$status"
  fi
  printf '[MaiBotQQCallAVHost] exited with status %s; restarting\n' "$status" >&2
  sleep 2
done
