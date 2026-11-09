# Troubleshooting OpenRemote

These commands assume Linux, a cloned OpenRemote checkout and a private config at ~/.config/openremote-agent/agent.json. Replace locations if your installation differs.

## Start with local checks

~~~bash
cd OpenRemote
.venv/bin/python -m openremote_agent.server \
  --config "$HOME/.config/openremote-agent/agent.json" --doctor
.venv/bin/python -m unittest discover -s tests -q
tunnel-client help quickstart
tunnel-client doctor --profile openremote-agent --explain
~~~

If a local agent does not load, do not start by debugging ChatGPT. Check Python venv/dependencies, file ownership/mode, device_id, absolute workspace paths and that state is outside every workspace.

## Tunnel not listed in ChatGPT

Check that the tunnel exists in [Platform tunnels](https://platform.openai.com/settings/organization/tunnels) and is associated with **both the intended Platform organization and ChatGPT workspace**. The account/runtime key needs Tunnels Read + Use. The ChatGPT account/workspace must be allowed to add custom MCP servers. The service must be running and ready when ChatGPT discovers the tools.

~~~bash
curl -fsS http://127.0.0.1:18767/healthz
curl -fsS http://127.0.0.1:18767/readyz
~~~

A tunnel's availability does not itself prove that ChatGPT has successfully discovered the agent's tools; perform a real read-only tool call.

## 401, 403, 429 or client disconnected

- Re-run tunnel-client doctor using the same profile and correct runtime key.
- Confirm CONTROL_PLANE_API_KEY is provided to the **running** client, not only to the shell where the profile was initially created.
- Confirm the runtime key is valid and associated with the right Platform organization. Never paste the key in an issue or chat.
- Inspect local /healthz and /readyz and the user-service journal.
- Check the active ChatGPT workspace and tunnel associations/permissions.
- A 429 can be related to rate limiting or service conditions; do not assume it proves a Python server bug. Consult official error details and current usage limits.

## Python/stdio server failing

If tool discovery says the MCP subprocess exited:

- Use an absolute Python path pointing to an environment with the openremote_agent package installed.
- Run the agent's --doctor with the **same private config**.
- Verify the config is owned by the current Linux user and not group/world-writable; do not use a symlink for the config.
- Check whether the Python interpreter in the tunnel profile is from an old checkout or deleted venv.
- Do not print secrets in debug logs or pass a runtime API key through the MCP command line.

## SSH peer unavailable

From the gateway Linux account test:

~~~bash
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes my-remote 'hostname'
~~~

Confirm the peer is online and its configured host key matches the expected identity. Verify the peer's Python path, private JSON path and device_id. The peer must allow the requested operation with its **own** role/workspace settings. Check the Tailscale or trusted network route if applicable. Never disable host key checking as a workaround.

## Workspace or file errors

- Workspaces must be pre-created, owned by the agent account and non-world-writable.
- The audit/state directory must be separate from workspace roots.
- Paths in MCP file calls are **relative to the selected workspace**; absolute paths and parent-directory traversal are refused.
- read_text is UTF-8 only, with a 64 KiB limit.
- write_text only creates new files; use replace_text with the latest expected_sha256 to edit existing files.
- A stale hash means another edit occurred: re-read and reconcile instead of forcing overwrite.

## Command permission denied

- execute_command requires a known command_id from the target config. A privileged command requires admin and a local one-time approval on the **target**.
- run_argv requires admin **and** ad_hoc_enabled=true in the target's private config; it does not provide extra Linux privileges.
- Use a harmless /usr/bin/id or /usr/bin/uname verification first. Never loosen roles or disable safety checks merely to pass a test.

## systemd user service

~~~bash
systemctl --user is-active openremote-agent-tunnel.service
systemctl --user status openremote-agent-tunnel.service --no-pager
journalctl --user -u openremote-agent-tunnel.service -n 30 --no-pager
~~~

For a headless host, confirm its user manager continues running after logout (linger is a local OS administrator setting). The tunnel must have a reachable Python interpreter and a valid, private runtime key file. Do not start duplicate tunnel-client processes pointing at the same profile without a reason.

## Profile already exists

The provisioning helper intentionally refuses to overwrite existing profiles or units. If you first configured **Option A** in QUICKSTART.md and want systemd, do one of the following:

1. Keep using the manual profile and run tunnel-client run in a persistent terminal session; or
2. Back up the profile, use a separate profile name/port supported by your installed tunnel-client, and create a verified user service manually; or
3. After a deliberate rollback/backup and when the tunnel is not running, remove the **specific** profile created by your own installation, then rerun the helper. Do not delete any unrelated tunnels, profiles or systemd units.

The helper never uses --force, and it does not replace an existing service. A partial installation failure may leave a profile or wrapper that you must inspect and recover deliberately.

## Public repository vs public plugin

Anyone can clone a **public GitHub repository**. Each person must still create their **own private ChatGPT MCP connection** and install the plugin within their own account/workspace. Secure MCP Tunnel is not a route for distributing a shared public plugin in the ChatGPT directory. Public plugin submissions require a separately secured public HTTPS MCP endpoint and review.

Official documentation: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server).
