#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$script_dir/common.sh"

require_command curl
require_file "$qq_dir/qq"
require_file "$token_file"
"$script_dir/audio-control.sh" start >/dev/null
ensure_runtime_dirs

if ! curl -fsS "http://127.0.0.1:${MAIBOT_QQ_CALL_AV_HOST_PORT:-6111}/healthz" \
  >/dev/null 2>&1; then
  nohup "$script_dir/run-av-host.sh" >>"$logs_dir/av-host.log" 2>&1 &
  printf '%s\n' "$!" >"$runtime_dir/av-host.pid"
  for _ in $(seq 1 80); do
    if curl -fsS "http://127.0.0.1:${MAIBOT_QQ_CALL_AV_HOST_PORT:-6111}/healthz" \
      >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
fi
if ! curl -fsS "http://127.0.0.1:${MAIBOT_QQ_CALL_AV_HOST_PORT:-6111}/healthz" \
  >/dev/null 2>&1; then
  printf 'AV host did not become ready; inspect %s\n' "$logs_dir/av-host.log" >&2
  exit 1
fi

export MAIBOT_QQ_CALL_BRIDGE_DIR="$bridge_dir"
export MAIBOT_QQ_CALL_QQ_DIR="$qq_dir"
export MAIBOT_QQ_CALL_AVSDK_PATH="$avsdk_path"
export MAIBOT_QQ_CALL_BRIDGE_TOKEN_FILE="$token_file"
export PULSE_SERVER="$pulse_server"
unset MAIBOT_QQ_CALL_AV_HOST MAIBOT_QQ_CALL_AV_HOST_ENTRY

qq_args=(--no-sandbox)
if [[ -n ${MAIBOT_QQ_CALL_BOT_UIN:-} ]]; then
  qq_args+=(-q "$MAIBOT_QQ_CALL_BOT_UIN")
fi
exec xvfb-run -a "$qq_dir/qq" "${qq_args[@]}" "$@"
