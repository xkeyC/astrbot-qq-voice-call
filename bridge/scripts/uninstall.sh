#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$script_dir/common.sh"

purge=0
if [[ ${1:-} == --purge ]]; then
  purge=1
elif [[ $# -gt 0 ]]; then
  printf 'usage: %s [--purge]\n' "$0" >&2
  exit 2
fi
if [[ -z $qq_dir || -z $napcat_dir ]]; then
  printf 'QQ or NapCat path is unavailable; set MAIBOT_QQ_CALL_QQ_DIR and MAIBOT_QQ_CALL_NAPCAT_DIR\n' >&2
  exit 1
fi

loader_path="$qq_dir/resources/app/loadNapCat.js"
backup_path="$qq_dir/resources/app/loadNapCat.maibot-qq-call.backup.cjs"
plugin_dir="$napcat_dir/plugins/napcat-plugin-maibot-qq-voice-call"

if [[ -f "$loader_path" ]] && grep -q 'MAIBOT_QQ_CALL_LOADER_HOOK_V1' "$loader_path"; then
  [[ -f "$backup_path" ]] || {
    printf 'cannot restore QQ loader because its backup is missing\n' >&2
    exit 1
  }
  mv -- "$backup_path" "$loader_path"
elif [[ -e "$backup_path" ]]; then
  printf 'QQ loader has changed since installation; backup retained at %s\n' "$backup_path" >&2
  exit 1
fi

if [[ -d "$plugin_dir" ]]; then
  rm -f -- \
    "$plugin_dir/index.mjs" \
    "$plugin_dir/package.json" \
    "$plugin_dir/bridge-config.json"
  rmdir -- "$plugin_dir" 2>/dev/null || \
    printf 'plugin directory contains additional files and was retained: %s\n' "$plugin_dir" >&2
fi

if [[ $purge -eq 1 ]]; then
  resolved_bridge_dir=$(readlink -f -- "$bridge_dir")
  case "$resolved_bridge_dir" in
    */maibot-qq-voice-call)
      if [[ ! -f "$resolved_bridge_dir/runtime/qq-dir" || \
        ! -f "$resolved_bridge_dir/av-host/host.cjs" ]]; then
        printf 'refusing to purge a directory without bridge installation markers: %s\n' \
          "$resolved_bridge_dir" >&2
        exit 1
      fi
      rm -rf -- "$resolved_bridge_dir"
      printf 'bridge runtime and token were removed: %s\n' "$resolved_bridge_dir"
      ;;
    *)
      printf 'refusing to purge unexpected path: %s\n' "$resolved_bridge_dir" >&2
      exit 1
      ;;
  esac
else
  printf 'plugin and loader hook removed; runtime/token retained at %s\n' "$bridge_dir"
fi
