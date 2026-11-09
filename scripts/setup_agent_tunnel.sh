#!/usr/bin/env bash
# OpenRemote: provision a NEW private MCP tunnel on a Linux systemd user host.
# Run once per new profile; does not overwrite existing services/configurations.
#
# Usage: ./scripts/setup_agent_tunnel.sh tunnel_<your-32-character-hex-id>
#
# Optional environment overrides (absolute paths):
#   TUNNEL_CLIENT          path to the downloaded official tunnel-client
#   OPENREMOTE_CONFIG      path to private agent.json
#   OPENREMOTE_RUNTIME_KEY path to private Platform runtime key file
#   OPENREMOTE_AGENT_PYTHON path to Python venv interpreter with the agent installed
#   OPENREMOTE_HEALTH_ADDR local address (default 127.0.0.1:18767)
#
# This script never accepts or prints API keys in command-line arguments.
set -euo pipefail
umask 077

if (( $# != 1 )) || [[ ! "$1" =~ ^tunnel_[a-fA-F0-9]{32}$ ]]; then
    echo "Usage: $0 tunnel_<32 hexadecimal characters>" >&2
    exit 2
fi
tunnel_id="$1"
repo="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
home="$HOME"
profile_name="openremote-agent"
profile="$home/.config/tunnel-client/$profile_name.yaml"
wrapper="$home/.local/libexec/openremote-agent-tunnel-start"
unit="$home/.config/systemd/user/openremote-agent-tunnel.service"

agent_config="$home/.config/openremote-agent/agent.json"
key_file="$home/.config/openremote-agent/runtime-key"
python_bin="$repo/.venv/bin/python"
tunnel_bin="$(command -v tunnel-client || true)"
health_addr="127.0.0.1:18767"

if [[ -n "$(printenv OPENREMOTE_CONFIG 2>/dev/null || true)" ]]; then
    agent_config="$(printenv OPENREMOTE_CONFIG)"
fi
if [[ -n "$(printenv OPENREMOTE_RUNTIME_KEY 2>/dev/null || true)" ]]; then
    key_file="$(printenv OPENREMOTE_RUNTIME_KEY)"
fi
if [[ -n "$(printenv OPENREMOTE_AGENT_PYTHON 2>/dev/null || true)" ]]; then
    python_bin="$(printenv OPENREMOTE_AGENT_PYTHON)"
fi
if [[ -n "$(printenv TUNNEL_CLIENT 2>/dev/null || true)" ]]; then
    tunnel_bin="$(printenv TUNNEL_CLIENT)"
fi
if [[ -n "$(printenv OPENREMOTE_HEALTH_ADDR 2>/dev/null || true)" ]]; then
    health_addr="$(printenv OPENREMOTE_HEALTH_ADDR)"
fi

if [[ ! "$health_addr" =~ ^127\.0\.0\.1:[0-9]{2,5}$ ]]; then
    echo "The health listener must bind 127.0.0.1:<port>" >&2
    exit 2
fi
for path in "$agent_config" "$key_file"; do
    if [[ "$path" != /* || ! -f "$path" || -L "$path" ]]; then
        echo "Missing, non-absolute, or symbolic-link config/secret: $path" >&2
        exit 2
    fi
done
if [[ -z "$tunnel_bin" ]]; then
    echo "tunnel-client is not on PATH; set TUNNEL_CLIENT to an absolute executable path." >&2
    exit 2
fi
if [[ "$tunnel_bin" != /* || ! -x "$tunnel_bin" ]]; then
    echo "TUNNEL_CLIENT must be an absolute executable path." >&2
    exit 2
fi
if [[ "$python_bin" != /* || ! -x "$python_bin" ]]; then
    echo "OPENREMOTE_AGENT_PYTHON must be an absolute executable path." >&2
    exit 2
fi
if [[ -e "$profile" || -L "$profile" || -e "$wrapper" || -L "$wrapper" || -e "$unit" || -L "$unit" ]]; then
    echo "Profile, wrapper or service already exists; refusing to overwrite. See docs/TROUBLESHOOTING.md" >&2
    exit 2
fi

# Check the private key without printing its value.
key_mode="$(stat -c '%a' "$key_file")"
key_owner="$(stat -c '%u' "$key_file")"
if [[ "$key_owner" != "$(id -u)" || ( "$key_mode" != "600" && "$key_mode" != "400" ) ]]; then
    echo "The runtime key must be owned by the current user and mode 0600 (or 0400)." >&2
    exit 2
fi
if [[ ! -s "$key_file" ]]; then
    echo "The runtime key file is empty." >&2
    exit 2
fi

# Fail before changing anything when the local agent config is invalid.
"$python_bin" -m openremote_agent.server --config "$agent_config" --doctor >/dev/null
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "A working systemd --user manager is required; otherwise use tunnel-client run manually." >&2
    exit 2
fi

# Quote the stdio command so paths with spaces cannot become shell syntax.
printf -v mcp_command '%q ' \
    "$python_bin" -m openremote_agent.server \
    --transport stdio --config "$agent_config"

CONTROL_PLANE_API_KEY="$(cat "$key_file")"
export CONTROL_PLANE_API_KEY

"$tunnel_bin" init \
    --sample sample_mcp_stdio_local \
    --profile "$profile_name" \
    --tunnel-id "$tunnel_id" \
    --health-listen-addr "$health_addr" \
    --mcp-command "$mcp_command"

chmod 600 "$profile"
"$tunnel_bin" doctor --profile "$profile_name"

mkdir -p "$home/.local/libexec" "$home/.config/systemd/user"

printf -v key_quoted '%q' "$key_file"
printf -v tunnel_quoted '%q' "$tunnel_bin"
cat > "$wrapper" <<EOF
#!/usr/bin/env bash
set -euo pipefail
umask 077
key_file=$key_quoted
if [[ ! -r "\$key_file" ]]; then
    echo "OpenRemote runtime key unavailable" >&2
    exit 1
fi
CONTROL_PLANE_API_KEY="\$(cat "\$key_file")"
export CONTROL_PLANE_API_KEY
exec $tunnel_quoted run --profile openremote-agent
EOF
chmod 700 "$wrapper"

cat > "$unit" <<'UNIT'
[Unit]
Description=OpenRemote Agent private MCP tunnel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/libexec/openremote-agent-tunnel-start
Restart=on-failure
RestartSec=8
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT
chmod 600 "$unit"
systemctl --user daemon-reload
systemctl --user enable --now openremote-agent-tunnel.service
unset CONTROL_PLANE_API_KEY

echo "OpenRemote Agent tunnel configured."
echo "Service: openremote-agent-tunnel.service"
echo "Check readiness: curl -fsS http://$health_addr/readyz"
echo "Then connect YOUR tunnel from ChatGPT Plugins -> + -> Add custom MCP server -> Tunnel."
