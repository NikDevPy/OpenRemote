# Install OpenRemote and connect your own ChatGPT plugin

This is a **self-hosted** project. You set up an agent on a Linux computer you own, create your own Secure MCP Tunnel, and install a separate private ChatGPT plugin. No developer-owned tunnel, account or SSH credentials are required.

**Requirements:** Linux, Python 3.11+ (including venv), Git, internet access for package installation and OpenAI outbound HTTPS, a supported ChatGPT account/workspace and an OpenAI Platform organization with tunnel permissions. A second Linux computer and Tailscale are optional.

## 1. Install the agent

~~~bash
git clone https://github.com/NikDevPy/OpenRemote.git
cd OpenRemote
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
~~~

Create the private agent directories:

~~~bash
mkdir -p "$HOME/.config/openremote-agent" \
  "$HOME/.local/share/openremote-agent/sandbox" \
  "$HOME/.local/state/openremote-agent"
chmod 700 "$HOME/.config/openremote-agent" \
  "$HOME/.local/share/openremote-agent/sandbox" \
  "$HOME/.local/state/openremote-agent"
cp examples/local-agent.example.json \
  "$HOME/.config/openremote-agent/agent.json"
chmod 600 "$HOME/.config/openremote-agent/agent.json"
~~~

Open agent.json in your text editor, replace **YOUR_USER** with your Linux username, and give device_id a unique name. All file paths must be absolute. Keep the audit state directory separate from any project workspace.

~~~bash
.venv/bin/python -m openremote_agent.server \
  --config "$HOME/.config/openremote-agent/agent.json" --doctor
.venv/bin/python -m unittest discover -s tests -q
~~~

The sample starts as a read-only viewer with ad_hoc_enabled=false. A successful doctor reports ready=true. An installation does not automatically install a ChatGPT plugin.

## 2. Give the agent access to projects (optional)

A private agent.json can include these keys (merge into the existing JSON):

~~~json
{
  "role": "operator",
  "workspaces": {
    "projects": {
      "path": "/home/YOUR_USER/Projects",
      "access": "write"
    }
  }
}
~~~

The workspace directory must exist, belong to the agent's Linux user and not be world-writable. The private sandbox, workspace and audit state must be configured consistently.

Roles: **viewer** can inspect/read; **operator** can also create and hash-check-replace files and run allowlisted commands; **admin** can inspect audit and optionally run arbitrary argv. The default ad_hoc_enabled=false must be explicitly changed to true for dynamic command execution, and the role must be admin.

**Important:** a workspace is a file-tool boundary, NOT a sandbox for run_argv subprocesses. Subprocesses can access anything permitted by the operating-system account. See [SECURITY](SECURITY.md).

Run doctor again after every config change.

## 3. Connect a second machine over SSH (optional)

Repeat step 1 on the second Linux computer, setting its device_id to something different, such as buildbox. The second computer needs its own Python environment and private config.

Set up key-based SSH access from the **gateway host** to the peer. Optional: [install Tailscale](https://tailscale.com/download) on both machines. Verify the SSH host's identity via a trusted channel, then ensure noninteractive login works:

~~~bash
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes my-remote 'hostname'
~~~

Here my-remote is an alias from the gateway user's ~/.ssh/config. The gateway will never accept a hostname supplied by an MCP caller.

Add this fragment to the gateway's private agent.json:

~~~json
{
  "peers": {
    "buildbox": {
      "target": "my-remote",
      "host_name": "100.64.1.23",
      "python": "/home/REMOTE_USER/OpenRemote/.venv/bin/python",
      "config_path": "/home/REMOTE_USER/.config/openremote-agent/agent.json"
    }
  }
}
~~~

**All addresses and paths above are examples. Replace them.** The remote agent's device_id must be buildbox. Hostname verification is mandatory; do not bypass it using StrictHostKeyChecking=no. Each remote agent checks its own roles and filesystem settings; being configured does not prove that a peer is online.

See [SSH design](ssh-routing.md) and [TROUBLESHOOTING](TROUBLESHOOTING.md).

## 4. Create an OpenAI Secure MCP Tunnel

Follow the official [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). The tunnel is outbound-only and keeps your MCP server private.

1. Open [Platform tunnels](https://platform.openai.com/settings/organization/tunnels). Create **your own** tunnel; associate it with the right Platform organization and **ChatGPT workspace**.
2. Ensure the account creating the tunnel has **Tunnels Read + Manage**, and the runtime/connector accounts have **Tunnels Read + Use**, as applicable.
3. Create your own [runtime API key](https://platform.openai.com/settings/organization/api-keys) with appropriate permissions. **Never commit it or send it through chat.**
4. Download tunnel-client from the [official latest release](https://github.com/openai/tunnel-client/releases/latest) for your OS/CPU and make it available on PATH. Check it:

~~~bash
tunnel-client --version
tunnel-client help quickstart
~~~

Store the runtime key in a protected local file. Enter it interactively to avoid shell history:

~~~bash
umask 077
install -m 600 /dev/null "$HOME/.config/openremote-agent/runtime-key"
read -r -s -p "Paste runtime API key: " OR_RUNTIME_KEY
printf '\n'
printf '%s\n' "$OR_RUNTIME_KEY" \
  > "$HOME/.config/openremote-agent/runtime-key"
unset OR_RUNTIME_KEY
chmod 600 "$HOME/.config/openremote-agent/runtime-key"
~~~

### Option A — run the tunnel interactively

From the cloned repository directory, with your real tunnel ID:

~~~bash
export CONTROL_PLANE_API_KEY="$(
  cat "$HOME/.config/openremote-agent/runtime-key"
)"
tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile openremote-agent \
  --tunnel-id tunnel_REPLACE_WITH_YOUR_ID \
  --health-listen-addr 127.0.0.1:18767 \
  --mcp-command "$PWD/.venv/bin/python -m openremote_agent.server --transport stdio --config $HOME/.config/openremote-agent/agent.json"
tunnel-client doctor --profile openremote-agent --explain
tunnel-client run --profile openremote-agent
~~~

Keep this terminal running when you create or use your ChatGPT plugin. You only need to initialize each named tunnel-client profile once. Check local health in another terminal:

~~~bash
curl -fsS http://127.0.0.1:18767/healthz
curl -fsS http://127.0.0.1:18767/readyz
~~~

### Option B — automated systemd user service

You can use the optional generic Linux setup helper **instead of Option A**, not after initializing the same profile:

~~~bash
./scripts/setup_agent_tunnel.sh tunnel_REPLACE_WITH_YOUR_ID
systemctl --user status openremote-agent-tunnel.service --no-pager
curl -fsS http://127.0.0.1:18767/readyz
~~~

The helper expects the private config at ~/.config/openremote-agent/agent.json and key at ~/.config/openremote-agent/runtime-key; it finds the local .venv next to the repository. It requires tunnel-client on PATH and a running systemd user manager. For alternate locations, use the environment variables described in the script's header. It refuses to overwrite existing profiles/services. For Option A to Option B migration, see [TROUBLESHOOTING](TROUBLESHOOTING.md#profile-already-exists).

Headless boot may require enabling user-service linger by an authorized local administrator. Do not grant root or passwordless sudo just for this.

## 5. Install your private MCP plugin in ChatGPT

While the tunnel is **ready**:

1. Open [ChatGPT Plugins](https://chatgpt.com/#settings/Connectors) on the web.
2. Select **+ → Add custom MCP server**.
3. Under Connection select **Tunnel**, and choose or paste your tunnel ID.
4. Configure authentication. For this private stdio server, there is no separate OAuth implementation; the OpenAI tunnel and workspace association provide the connection boundary. If the UI permits it, select **No authentication**. Do not offer this arrangement as a public multi-user service.
5. Review the risk warning, select **I understand and want to continue**, then **Create as a plugin**.
6. Install the new plugin, start a conversation and invoke it by typing @ and selecting its name.

Whether this feature is enabled depends on your ChatGPT workspace policies and Platform organization access. Installing an agent locally does not guarantee your ChatGPT account can create a custom plugin. This is **private plugin installation**, not submission to a public plugin directory.

## 6. Safe verification

Ask ChatGPT to use your plugin for the following checks in order:

- list_devices: verify your local gateway appears; configured remote peers may not be online.
- system_info on each device, specifying device_id for a remote peer.
- list_workspaces followed by list_files inside a deliberately non-sensitive workspace.
- When operator is configured, create a disposable file with write_text, read its SHA-256, and edit it with replace_text using expected_sha256.
- Optionally inspect a harmless predefined command with preview_command before execute_command.
- Only if deliberately enabled, test admin run_argv with /usr/bin/id. **Do not use privileged/destructive commands for initial testing.**

For details about write confirmations and the command model, read [SECURITY](SECURITY.md).

## 7. Operating limits and help

The current agent has **11 tools** but no multi-user authentication, robust subprocess sandboxing, interactive PTY or resumable jobs. File calls are limited to 64 KiB of UTF-8 text. run_argv is limited to 60 seconds and 8 KiB per stdout/stderr stream. OpenAI Secure MCP Tunnel independently imposes rate, message-size and request-deadline limits; check its current documentation.

If tool discovery fails or the tunnel shows offline, see [TROUBLESHOOTING](TROUBLESHOOTING.md). The GitHub repository does not have access to your machines; every deployment is yours to configure and operate.
