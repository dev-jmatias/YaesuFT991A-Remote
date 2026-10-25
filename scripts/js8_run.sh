#!/usr/bin/env bash
# The JS8 side of the JS8 screen: JS8Call (the JS8Call-improved program, GPL-3.0, installed by the "Install JS8Call" button, a separate program) and the little private
# sound server it needs. Runs as the radio-remote user in its own systemd service (radio-remote-js8.service) so that its memory is counted apart from the web service.
# It is started by systemd when the file $DATA/run exists (radio-remote-js8.path) and stops by itself when the web page removes that file.
#
#   radio audio  --(web service)--> ALSA loopback hw:Loopback,0,0 --> js8_in (private PulseAudio) --> JS8Call
#   JS8Call --> js8_out --> ALSA loopback hw:Loopback,1,1 --> hw:Loopback,0,1 (web service) --> the radio, through the usual transmit protection
# JS8Call itself never touches the radio: it has no rig, no PTT and no serial port, and its own network reporting is switched off in its settings.
set -uo pipefail

DATA="${RR_JS8_DIR:-/var/lib/radio-remote/js8}"
RUN="$DATA/run"
APP="$DATA/app/squashfs-root"
LOG="$DATA/js8.log"

mkdir -p "$DATA/home" "$DATA/config" "$DATA/data" "$DATA/cache" "$DATA/pulse" "$DATA/pulse-state" "$DATA/pulse-config"
[ -x "$APP/AppRun" ] || { echo "JS8Call is not installed ($APP/AppRun is missing)" >&2; rm -f "$RUN"; exit 3; }
command -v pulseaudio >/dev/null || { echo "pulseaudio is not installed" >&2; rm -f "$RUN"; exit 4; }

export HOME="$DATA/home" XDG_CONFIG_HOME="$DATA/config" XDG_DATA_HOME="$DATA/data" XDG_CACHE_HOME="$DATA/cache"
export PULSE_RUNTIME_PATH="$DATA/pulse" PULSE_STATE_PATH="$DATA/pulse-state" PULSE_CONFIG_PATH="$DATA/pulse-config" PULSE_SERVER="unix:$DATA/pulse/native"

cat > "$DATA/js8.pa" <<EOF
load-module module-native-protocol-unix socket=$DATA/pulse/native auth-anonymous=1
load-module module-alsa-source device=hw:Loopback,1,0 source_name=js8_in rate=48000 channels=1
load-module module-alsa-sink device=hw:Loopback,1,1 sink_name=js8_out rate=48000 channels=1
set-default-source js8_in
set-default-sink js8_out
EOF

PA_PID=""; JS_PID=""
cleanup() {
  [ -n "$JS_PID" ] && kill "$JS_PID" 2>/dev/null
  [ -n "$PA_PID" ] && kill "$PA_PID" 2>/dev/null
  sleep 1
  [ -n "$JS_PID" ] && kill -9 "$JS_PID" 2>/dev/null
  [ -n "$PA_PID" ] && kill -9 "$PA_PID" 2>/dev/null
  exit 0
}
trap cleanup TERM INT EXIT

pulseaudio -n --daemonize=no --exit-idle-time=-1 --use-pid-file=no -F "$DATA/js8.pa" >> "$LOG" 2>&1 &
PA_PID=$!
for _ in $(seq 1 30); do [ -S "$DATA/pulse/native" ] && break; sleep 0.5; done
kill -0 "$PA_PID" 2>/dev/null || { echo "the private sound server did not start (is the loopback card there? see the web page)" >&2; rm -f "$RUN"; exit 5; }

QT_QPA_PLATFORM=offscreen "$APP/AppRun" --rig-name=rr >> "$LOG" 2>&1 &
JS_PID=$!

while [ -e "$RUN" ]; do
  kill -0 "$JS_PID" 2>/dev/null || { echo "JS8Call stopped" >&2; rm -f "$RUN"; break; }
  kill -0 "$PA_PID" 2>/dev/null || { echo "the sound server stopped" >&2; rm -f "$RUN"; break; }
  [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 5000000 ] && : > "$LOG"
  sleep 1
done
