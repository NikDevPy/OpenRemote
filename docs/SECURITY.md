# OpenRemote security model

OpenRemote is **remote administration software**, not a harmless chat extension. Even a private ChatGPT plugin connected through a tunnel can perform real file and process operations on the computer hosting the agent.

## Trust boundaries

1. **GitHub** distributes the source code only. Cloning this repository does not connect a machine or create a plugin.
2. **OpenAI Platform / ChatGPT** controls tunnel associations, workspace permissions, plugin installation and tool-call confirmations. Secure MCP Tunnel is an outbound private connection, **not general multi-user authentication for OpenRemote**.
3. **OpenRemote gateway** validates the local server configuration, roles, registered devices and workspace names.
4. **SSH peer** independently validates its local config and permissions. The gateway requires a known host key and noninteractive SSH; user input does not select an arbitrary SSH host.
5. **Linux kernel and user account** determine what any subprocess can actually read, modify or execute. OpenRemote does not create Linux privilege separation on its own.

## Safe defaults

- Use a dedicated unprivileged operating-system account, especially for a server.
- Keep role=viewer and ad_hoc_enabled=false for the first connection.
- Use owner-approved, narrow read-only workspaces where possible.
- Create local config and runtime key files with mode 0600, and agent sandbox/audit folders with mode 0700. Keep private runtime keys and SSH keys **outside** every workspace shared with file tools.
- Keep network access private; do not open an unauthenticated MCP endpoint on the public internet.
- Prefer direct local secure connections and short-lived credentials, and revoke keys/tunnels after a suspected compromise.

## Command execution

Two command models are available:

**execute_command** accepts only a predefined command_id from the target machine's local allowlist. Sensitive executables require risk=privileged, target role=admin, and a one-use approval recorded through a local interactive terminal. The approval is tied to a digest of its precise command/host/config and expires after 1–300 seconds. It does not bypass OS authorization.

**run_argv** accepts an arbitrary executable path and fixed argument array supplied to the tool. It requires BOTH role=admin and ad_hoc_enabled=true **on the target device**. This mode is intentionally capable of running a command interpreter if the caller supplies one. The tool is annotated readOnlyHint=false, destructiveHint=true and openWorldHint=true so a client can treat it as risky. Its OS process receives noninteractive stdin, a bounded timeout and bounded output. Those constraints are **not** a sandbox.

A process started in a workspace can still access files, processes and networks outside that workspace if the OS account has permission. Likewise, a process started through run_argv could modify SSH keys, configuration or project files that the file tools would refuse to access. **Do not enable it merely to make the MCP file sandbox broader.**

Tool annotations and a user's broad consent are not a substitute for specific authorization, threat assessment or platform safety requirements. ChatGPT may request confirmation or decline a particular operation regardless of the tool's availability.

**Note:** The example systemd tunnel service uses NoNewPrivileges=true. This intentionally blocks setuid-based privilege elevation from its service context. Do not remove it or grant blanket passwordless sudo without understanding the consequences. System management beyond the Linux service user's access requires a separately reviewed, narrow authorization design.

## Identity and multi-user limitations

**OpenRemote 0.3.1 does not authenticate individual callers at the agent layer.** The role is set globally in the private local configuration. Anyone who gains authorized access through the associated private MCP connection receives that server's configured capabilities, subject to ChatGPT and OS restrictions.

This is not safe for multiple unrelated users, arbitrary shared workspaces, or public plugin submissions. A production multi-user deployment needs proper OAuth, subject-bound roles, device-specific authorization, approvals, revocation, and an auditable action identity. A public GitHub repository is fine; a publicly accessible unrestricted MCP server is not.

## File operation limits

- Only approved workspace roots, owned by the configured OS user; no symlink path traversal.
- read_text and replace_text use UTF-8 text; per-call file size limit is 64 KiB.
- write_text creates new files but cannot overwrite existing files.
- replace_text requires a SHA-256 of the content last read and uses a same-directory temporary file/atomic rename. This is **optimistic conflict detection**, not a fully atomic compare-and-swap against unrelated processes.
- Some metadata, symlink/hard-link, race, ACL, ownership and filesystem-specific behaviors require further hardening for untrusted inputs. Do not expose host system directories as editable workspaces.
- Private configuration/state files and credentials must never be included in repository submissions or model-visible diagnostic logs.

## Transport and resource limits

Direct process calls are limited to 60 seconds and 8 KiB each on stdout/stderr. The current implementation has no durable terminal jobs or interactive PTY. OpenAI Secure MCP Tunnel additionally has independent rate limits, serialized message limits and a 120-second request deadline; see the current [OpenAI tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

## Threats to consider

- **Prompt injection:** Files, terminal output, issue content and other external data can contain instructions intended to hijack the model. Treat that content as data; independently confirm unexpected actions.
- **Secrets exposure:** A workspace with credentials or a subprocess with broad OS privileges may disclose tokens, SSH keys or sensitive local files.
- **Supply chain:** Install Python dependencies and tunnel-client only from verified sources; review releases and pinned dependencies.
- **Privilege misuse:** Do not grant the agent root or broad sudoers privileges. Use least privilege and an isolated Linux account.
- **Replay and persistence:** A stolen runtime credential or lingering live service may allow continued authorized access until the key/tunnel is revoked.
- **Untrusted multi-user access:** Private tunnel association is not a replacement for per-user authorization.

**Recommended rollout:** start with read-only local checks; add read/write project workspaces; then one trusted SSH peer; only then consider enabling arbitrary commands on a dedicated unprivileged account.
