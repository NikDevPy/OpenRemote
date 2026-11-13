#!/usr/bin/env bash
# Install a user-scoped, 60-second tunnel readiness monitor.
# Usage: install_tunnel_monitor.sh /absolute/venv/bin/python http://127.0.0.1:PORT/readyz [service] [--allow-restart]
set -euo pipefail
umask 077

if (( $# < 2 || $# > 4 )); then
    echo "Usage: $0 /absolute/python http://127.0.0.1:PORT/readyz [openremote-agent-tunnel.service] [--allow-restart]" >&2
    exit 2
fi

python="$1"
url="$2"
service="openremote-agent-tunnel.service"
restart=""
shift 2
for arg in "$@"; do
    if [[ "$arg" == "--allow-restart" ]]; then
        restart="--allow-restart"
    elif [[ "$arg" =~ ^openremote-[a-zA-Z0-9_-]*tunnel\.service$ ]]; then
        service="$arg"
    else
        echo "Unrecognized parameter or unsafe service name" >&2
        exit 2
    fi
done

# These values become literal systemd ExecStart arguments, not shell commands.
# Reject ambiguous whitespace, percent expansion and quoted metacharacters.
if [[ "$python" != /* || ! -x "$python" || "$python" =~ [[:space:]%\"\'\;\$] ]]; then
    echo "Expected an executable absolute Python path without whitespace/metacharacters" >&2
    exit 2
fi
if [[ ! "$url" =~ ^http://127\.0\.0\.1:[0-9]{1,5}/readyz$ ]]; then
    echo "Only http://127.0.0.1:PORT/readyz is supported" >&2
    exit 2
fi

# The gateway can run from an isolated source checkout while its venv still
# contains an older editable installation. Pin the monitor to this checkout.
release_root="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
if [[ ! -f "$release_root/openremote_agent/tunnel_health.py" ||
      ! "$release_root" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
    echo "Invalid release directory for OpenRemote tunnel monitor" >&2
    exit 2
fi
(cd "$release_root" && "$python" -c 'import openremote_agent.tunnel_health') >/dev/null
home_units="$HOME/.config/systemd/user"
mkdir -p "$home_units"
unit="$home_units/openremote-tunnel-monitor.service"
timer="$home_units/openremote-tunnel-monitor.timer"
if [[ -e "$unit" || -e "$timer" ]]; then
    echo "Existing monitor unit detected; refusing overwrite." >&2
    exit 1
fi

cat > "$unit" <<EOF
[Unit]
Description=OpenRemote loopback tunnel readiness check
After=network.target

[Service]
Type=oneshot
UMask=0077
WorkingDirectory=$release_root
ExecStart=$python -m openremote_agent.tunnel_health --url $url --service $service --failures 3 --cooldown-seconds 300 $restart
EOF

cat > "$timer" <<'EOF'
[Unit]
Description=Check OpenRemote tunnel readiness once per minute

[Timer]
OnStartupSec=45sec
OnUnitInactiveSec=60sec
AccuracySec=15sec
Persistent=true
Unit=openremote-tunnel-monitor.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now openremote-tunnel-monitor.timer
echo "Monitor active; restart enabled only when --allow-restart was explicitly set."
