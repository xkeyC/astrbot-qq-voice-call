#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
bridge_source=$(cd -- "$script_dir/.." && pwd -P)
install_dir=${MAIBOT_QQ_CALL_BRIDGE_DIR:-$HOME/.local/share/maibot-qq-voice-call}
napcat_dir=${MAIBOT_QQ_CALL_NAPCAT_DIR:-}
qq_dir=${MAIBOT_QQ_CALL_QQ_DIR:-}
check_only=0
original_loader=""

usage() {
  cat <<EOF
usage: $0 --napcat-dir DIR --qq-dir DIR [--install-dir DIR] [--original-loader FILE] [--check]

  --napcat-dir  directory containing napcat.mjs and plugins/
  --qq-dir      QQ installation directory containing qq and resources/app/
  --install-dir bridge runtime directory (default: $install_dir)
  --original-loader FILE
                clean Loader to restore on uninstall when replacing an older custom hook
  --check       validate paths and compatibility without changing files
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --napcat-dir)
      napcat_dir=${2:?missing value for --napcat-dir}
      shift 2
      ;;
    --qq-dir)
      qq_dir=${2:?missing value for --qq-dir}
      shift 2
      ;;
    --install-dir)
      install_dir=${2:?missing value for --install-dir}
      shift 2
      ;;
    --check)
      check_only=1
      shift
      ;;
    --original-loader)
      original_loader=${2:?missing value for --original-loader}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z $napcat_dir || -z $qq_dir ]]; then
  usage >&2
  exit 2
fi
if [[ $(uname -s) != Linux ]]; then
  printf 'the QQ AV bridge currently supports Linux only\n' >&2
  exit 1
fi

napcat_dir=$(readlink -f -- "$napcat_dir")
qq_dir=$(readlink -f -- "$qq_dir")
install_dir=$(readlink -m -- "$install_dir")
qq_app_dir="$qq_dir/resources/app"
loader_path="$qq_app_dir/loadNapCat.js"
backup_path="$qq_app_dir/loadNapCat.maibot-qq-call.backup.cjs"
plugin_dir="$napcat_dir/plugins/napcat-plugin-maibot-qq-voice-call"
token_file="$install_dir/runtime/control.token"

if [[ -n $original_loader ]]; then
  original_loader=$(readlink -f -- "$original_loader")
  [[ -f "$original_loader" ]] || {
    printf 'provided original Loader was not found: %s\n' "$original_loader" >&2
    exit 1
  }
fi

[[ -f "$napcat_dir/napcat.mjs" ]] || {
  printf 'napcat.mjs was not found under %s\n' "$napcat_dir" >&2
  exit 1
}
[[ -x "$qq_dir/qq" ]] || {
  printf 'QQ executable was not found under %s\n' "$qq_dir" >&2
  exit 1
}
[[ -f "$qq_app_dir/package.json" && -f "$loader_path" ]] || {
  printf 'QQ resources/app is incomplete under %s\n' "$qq_dir" >&2
  exit 1
}
if grep -Eq '"type"[[:space:]]*:[[:space:]]*"module"' "$qq_app_dir/package.json"; then
  printf 'this QQ package uses an ESM loader; the reversible CommonJS hook is incompatible\n' >&2
  exit 1
fi
if ! grep -Eq '"main"[[:space:]]*:[[:space:]]*"(\./)?loadNapCat\.js"' \
  "$qq_app_dir/package.json"; then
  printf 'QQ package.json does not use resources/app/loadNapCat.js as its entry point\n' >&2
  exit 1
fi
if grep -q 'AV_HOST' "$loader_path" && \
  ! grep -q 'MAIBOT_QQ_CALL_LOADER_HOOK_V1' "$loader_path" && \
  [[ -z $original_loader ]]; then
  printf '%s\n' \
    'an older custom AV Host loader is installed; pass --original-loader with a clean Loader backup' \
    >&2
  exit 1
fi
[[ -f "$qq_app_dir/avsdk/libAVSDKPlugin.so" ]] || {
  printf 'QQ AVSDK library was not found under %s\n' "$qq_dir" >&2
  exit 1
}
loader_hook_installed=0
if grep -q 'MAIBOT_QQ_CALL_LOADER_HOOK_V1' "$loader_path"; then
  loader_hook_installed=1
  [[ -f "$backup_path" ]] || {
    printf 'loader hook exists but its backup is missing: %s\n' "$backup_path" >&2
    exit 1
  }
elif [[ -e "$backup_path" ]]; then
  printf 'refusing to overwrite an unrelated loader backup: %s\n' "$backup_path" >&2
  exit 1
fi

if [[ $check_only -eq 1 ]]; then
  cat <<EOF
Bridge preflight passed without changing files.

NapCat directory: $napcat_dir
QQ directory:     $qq_dir
Install directory: $install_dir
Loader action:    $(
    if grep -q 'MAIBOT_QQ_CALL_LOADER_HOOK_V1' "$loader_path"; then
      printf 'reuse existing MaiBot hook'
    else
      if [[ -n $original_loader ]]; then
        printf 'use provided clean loader as backup and replace older hook'
      else
        printf 'back up current loader and install reversible hook'
      fi
    fi
  )
EOF
  exit 0
fi

if [[ ! -w "$loader_path" || ! -w "$qq_app_dir" || ! -w "$napcat_dir/plugins" ]]; then
  printf 'QQ Loader or NapCat plugins directory is not writable; rerun with suitable permissions\n' >&2
  exit 1
fi

install -d -m 0750 "$install_dir"
install -d -m 0750 \
  "$install_dir/av-host" "$install_dir/scripts" "$install_dir/logs" \
  "$plugin_dir"
install -d -m 0700 "$install_dir/runtime"
install -m 0644 "$bridge_source/av-host/host.cjs" "$install_dir/av-host/host.cjs"
install -m 0644 "$bridge_source/av-host/host.html" "$install_dir/av-host/host.html"
install -m 0755 "$bridge_source"/scripts/*.sh "$install_dir/scripts/"
install -m 0644 "$bridge_source/napcat-plugin/index.mjs" "$plugin_dir/index.mjs"
install -m 0644 "$bridge_source/napcat-plugin/package.json" "$plugin_dir/package.json"

umask 077
if [[ ! -f "$token_file" ]]; then
  if [[ -n ${MAIBOT_QQ_CALL_BRIDGE_TOKEN:-} ]]; then
    printf '%s\n' "$MAIBOT_QQ_CALL_BRIDGE_TOKEN" >"$token_file"
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32 >"$token_file"
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' >"$token_file"
    printf '\n' >>"$token_file"
  fi
fi
if [[ $(wc -c <"$token_file") -lt 33 ]]; then
  printf 'existing bridge token is shorter than 32 bytes: %s\n' "$token_file" >&2
  exit 1
fi
chmod 0600 "$token_file"
printf '%s\n' "$qq_dir" >"$install_dir/runtime/qq-dir"
printf '%s\n' "$napcat_dir" >"$install_dir/runtime/napcat-dir"

json_token_file=${token_file//\\/\\\\}
json_token_file=${json_token_file//\"/\\\"}
cat >"$plugin_dir/bridge-config.json" <<EOF
{
  "controlHost": "127.0.0.1",
  "controlPort": 6110,
  "avHost": "127.0.0.1",
  "avPort": 6111,
  "tokenFile": "$json_token_file"
}
EOF
chmod 0600 "$plugin_dir/bridge-config.json"

if [[ $loader_hook_installed -eq 0 ]]; then
  if [[ -n $original_loader ]]; then
    cp -p -- "$original_loader" "$backup_path"
  else
    cp -p -- "$loader_path" "$backup_path"
  fi
  json_host_entry=${install_dir//\\/\\\\}
  json_host_entry=${json_host_entry//\"/\\\"}
  json_host_entry="$json_host_entry/av-host/host.cjs"
  hook_tmp="$qq_app_dir/.loadNapCat.maibot-qq-call.$$"
  trap 'rm -f -- "$hook_tmp"' EXIT
  cat >"$hook_tmp" <<EOF
"use strict";
// MAIBOT_QQ_CALL_LOADER_HOOK_V1
if (process.env.MAIBOT_QQ_CALL_AV_HOST === "1") {
  require(process.env.MAIBOT_QQ_CALL_AV_HOST_ENTRY || "$json_host_entry");
} else {
  require("./loadNapCat.maibot-qq-call.backup.cjs");
}
EOF
  chmod "$(stat -c '%a' "$loader_path")" "$hook_tmp"
  mv -- "$hook_tmp" "$loader_path"
  trap - EXIT
fi

cat <<EOF
MaiBot QQ voice-call bridge installed.

NapCat plugin: $plugin_dir
Bridge runtime: $install_dir
Token file:     $token_file

Set MaiBot bridge.token_file to the token file above, then start NapCat with:
  MAIBOT_QQ_CALL_BOT_UIN=<bot-uin> $install_dir/scripts/run-napcat.sh

Run diagnostics with:
  $install_dir/scripts/doctor.sh
EOF
