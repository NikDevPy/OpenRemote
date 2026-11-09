#!/usr/bin/env bash
# Owner-operated provisioning for a NEW, private OpenRemote Agent tunnel.
# Requires a new tunnel id created in Platform settings and tunnel Use permission.
# Does not change the existing OpenRemote test tunnel or service.
set -euo pipefail
umask 077

if (( $# != 1 )) || [[ ! "$1" =~ ^tunnel_[a-fA-F0-9]{32}$ ]]; then
  echo "Usage: $0 tunnel_<32hex>; create a new tunnel in Platform first" >&2
  exit 2
fi
new_tunnel_id="$1"
home="/home/domenus"
client="$home/.local/opt/openremote-tunnel-client-v0.0.20/tunnel-client"
key_file="$home/.config/openremote-agent/runtime-key"
agent="$home/Projects/OpenRemote/.venv/bin/python"
agent_config="$home/.config/openremote-agent/terra.json"
profile="$home/.config/tunnel-client/openremote-agent.yaml"
wrapper="$home/.local/libexec/openremote-agent-tunnel-start"
unit="$home/.config/systemd/user/openremote-agent-tunnel.service"

for path in "$client" "$key_file" "$agent" "$agent_config"; do
  if [[ ! -f "$path" ]]; then echo "Missing required path: $path" >&2; exit 2; fi
done
if [[ -e "$profile" || -e "$wrapper" || -e "$unit" ]]; then
  echo "Refusing to overwrite an existing agent tunnel profile/service" >&2
  exit 2
fi

export CONTROL_PLANE_API_KEY
CONTROL_PLANE_API_KEY="$(cat "$key_file")"
"$client" init \
  --sample sample_mcp_stdio_local \
  --profile openremote-agent \
  --tunnel-id "$new_tunnel_id" \
  --health-listen-addr 127.0.0.1:18767 \
  --mcp-command "$agent -m openremote_agent.server --transport stdio --config $agent_config"
chmod 600 "$profile"
"$client" doctor --profile openremote-agent
mkdir -p "$home/.local/libexec" "$home/.config/systemd/user"
cat > "$wrapper" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
umask 077
key=/home/domenus/.config/openremote-agent/runtime-key
if [[ ! -r "$key" ]]; then echo "Missing OpenRemote runtime key" >&2; exit 1; fi
CONTROL_PLANE_API_KEY="$(cat "$key")"
export CONTROL_PLANE_API_KEY
exec /home/domenus/.local/opt/openremote-tunnel-client-v0.0.20/tunnel-client run --profile openremote-agent
SH
chmod 700 "$wrapper"
cat > "$unit" <<'UNIT'
[Unit]
Description=OpenRemote Agent private MCP tunnel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart=/home/domenus/.local/libexec/openremote-agent-tunnel-start
WorkingDirectory=/home/domenus/Projects/OpenRemote
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
echo "Agent tunnel installed and started; check /readyz on 127.0.0.1:18767"
echo "Then connect it in ChatGPT Plugins -> + -> Add custom MCP server -> Tunnel."
