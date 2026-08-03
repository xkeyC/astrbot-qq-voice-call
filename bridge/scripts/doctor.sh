#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$script_dir/common.sh"

failures=0
ok() { printf '[ok] %s\n' "$1"; }
bad() { printf '[fail] %s\n' "$1" >&2; failures=$((failures + 1)); }

for command in curl pactl parec pacat pulseaudio xvfb-run; do
  if command -v "$command" >/dev/null 2>&1; then ok "command: $command"; else bad "missing command: $command"; fi
done

for file in \
  "$qq_dir/qq" \
  "$qq_dir/resources/app/loadNapCat.js" \
  "$avsdk_path" \
  "$napcat_dir/napcat.mjs" \
  "$napcat_dir/plugins/napcat-plugin-maibot-qq-voice-call/index.mjs" \
  "$bridge_dir/av-host/host.cjs" \
  "$token_file"; do
  if [[ -f "$file" ]]; then ok "file: $file"; else bad "missing file: $file"; fi
done

if [[ -f "$qq_dir/resources/app/loadNapCat.js" ]] && \
  grep -q 'MAIBOT_QQ_CALL_LOADER_HOOK_V1' "$qq_dir/resources/app/loadNapCat.js"; then
  ok "reversible QQ loader hook"
else
  bad "QQ loader hook is not installed"
fi

if "$script_dir/audio-control.sh" status >/dev/null 2>&1; then
  ok "isolated PulseAudio server"
else
  bad "isolated PulseAudio server is not running"
fi

if curl -fsS "http://127.0.0.1:${MAIBOT_QQ_CALL_AV_HOST_PORT:-6111}/healthz" >/dev/null 2>&1; then
  ok "AV host health endpoint"
else
  bad "AV host health endpoint"
fi

if [[ -f "$token_file" ]]; then
  token=$(tr -d '\r\n' <"$token_file")
  if curl -fsS \
    -H "Authorization: Bearer $token" \
    "http://127.0.0.1:${MAIBOT_QQ_CALL_BRIDGE_PORT:-6110}/v1/calls/current" \
    >/dev/null 2>&1; then
    ok "authenticated NapCat bridge endpoint"
  else
    bad "authenticated NapCat bridge endpoint"
  fi
  unset token
fi

if [[ $failures -gt 0 ]]; then
  printf '%s diagnostic check(s) failed\n' "$failures" >&2
  exit 1
fi
printf 'all bridge checks passed\n'
