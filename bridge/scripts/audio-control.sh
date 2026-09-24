#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=common.sh
source "$script_dir/common.sh"

config_path="$runtime_dir/pulse.pa"
log_path="$logs_dir/pulseaudio.log"

is_ready() {
  PULSE_SERVER="$pulse_server" pactl info >/dev/null 2>&1
}

write_config() {
  cat >"$config_path" <<EOF
.nofail
load-module module-native-protocol-unix socket=$pulse_socket auth-anonymous=1
load-module module-null-sink sink_name=astrbot_qq_speaker rate=48000 channels=2 sink_properties=device.description=AstrBot_QQ_Speaker
load-module module-null-sink sink_name=astrbot_qq_mic rate=48000 channels=2 sink_properties=device.description=AstrBot_QQ_Microphone_Feed
load-module module-remap-source master=astrbot_qq_mic.monitor source_name=astrbot_qq_mic_source channels=1 source_properties=device.description=AstrBot_QQ_Microphone
set-default-sink astrbot_qq_speaker
set-default-source astrbot_qq_mic_source
EOF
  chmod 0600 "$config_path"
}

start_audio() {
  require_command pulseaudio
  require_command pactl
  ensure_runtime_dirs
  if ! is_ready; then
    write_config
    PULSE_RUNTIME_PATH="$pulse_runtime_dir" pulseaudio \
      --daemonize=yes \
      --exit-idle-time=-1 \
      --disallow-exit=no \
      --disable-shm=yes \
      --file="$config_path" \
      --log-target="file:$log_path"
  fi
  for _ in $(seq 1 40); do
    if is_ready; then
      PULSE_SERVER="$pulse_server" pactl info |
        sed -n -E '/^(Server Name|Default Sink|Default Source):/p'
      return 0
    fi
    sleep 0.25
  done
  printf 'AstrBot QQ call audio server did not become ready\n' >&2
  return 1
}

case "${1:-status}" in
  start)
    start_audio
    ;;
  stop)
    if is_ready; then
      PULSE_SERVER="$pulse_server" pactl exit
    fi
    ;;
  status)
    if ! is_ready; then
      printf 'stopped\n'
      exit 1
    fi
    PULSE_SERVER="$pulse_server" pactl info |
      sed -n -E '/^(Server Name|Default Sink|Default Source):/p'
    PULSE_SERVER="$pulse_server" pactl list short sinks
    PULSE_SERVER="$pulse_server" pactl list short sources
    ;;
  *)
    printf 'usage: %s {start|stop|status}\n' "$0" >&2
    exit 2
    ;;
esac
