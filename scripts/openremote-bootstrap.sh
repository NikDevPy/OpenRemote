#!/bin/sh
# OpenRemote Linux bootstrap. No root pip or OpenAI credentials at this stage.
set -eu
umask 077

PKG_VERSION="0.6.1"
WHEEL_DIR="${OPENREMOTE_ASSET_ROOT:-/usr/share/openremote}"
USER_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/openremote-control-center"
USER_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/openremote-control-center"
RUNTIME="$USER_DATA/runtime-$PKG_VERSION"
LOCK="$USER_DATA/bootstrap.lock"
WHEEL="$WHEEL_DIR/openremote_probe-${PKG_VERSION}-py3-none-any.whl"

if [ ! -f "$WHEEL" ]; then
  printf '%s\n' "OpenRemote wheel missing. Reinstall the .deb." >&2
  exit 1
fi
mkdir -p "$USER_DATA" "$USER_CACHE"
chmod 700 "$USER_DATA" "$USER_CACHE"
exec 9>"$LOCK"
flock -x 9

VERSION_FILE="$RUNTIME/.openremote-version"
CURRENT=""
if [ -f "$VERSION_FILE" ]; then CURRENT="$(cat "$VERSION_FILE")"; fi
if [ "$CURRENT" != "$PKG_VERSION" ] || [ ! -x "$RUNTIME/bin/openremote-gui" ]; then
  LOG="$USER_CACHE/bootstrap.log"
  : > "$LOG"
  chmod 600 "$LOG"
  (
    if [ ! -x "$RUNTIME/bin/python" ]; then python3 -m venv "$RUNTIME"; fi
    "$RUNTIME/bin/python" -m pip install       --disable-pip-version-check --no-input --no-cache-dir       --only-binary=:all: "$WHEEL[gui]"
  ) > "$LOG" 2>&1 &
  WORKER=$!
  SPINNER=""
  if [ "${OPENREMOTE_NO_PROGRESS:-0}" != 1 ] && command -v zenity >/dev/null 2>&1 && \
     [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    zenity --progress --pulsate --no-cancel --auto-close \
      --title="OpenRemote" \
      --text="Installing OpenRemote for this user (first launch requires internet)…" \
      >/dev/null 2>&1 &
    SPINNER=$!
  fi
  set +e
  wait "$WORKER"
  RC=$?
  set -e
  if [ -n "$SPINNER" ]; then kill "$SPINNER" 2>/dev/null || true; fi
  if [ "$RC" -ne 0 ]; then
    printf '%s\n' "Could not install dependencies. Check private log: $LOG" >&2
    if command -v zenity >/dev/null 2>&1 && [ "${OPENREMOTE_NO_PROGRESS:-0}" != 1 ]; then
      zenity --error --title="OpenRemote" \
        --text="First-launch setup failed. Check the local bootstrap log." \
        >/dev/null 2>&1 || true
    fi
    exit "$RC"
  fi
  echo "$PKG_VERSION" > "$RUNTIME/.openremote-version"
fi
flock -u 9
exec "$RUNTIME/bin/openremote-gui" "$@"
