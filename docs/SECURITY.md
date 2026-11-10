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

**OpenRemote 0.5.0 does not authenticate individual callers at the agent layer.** The role is set globally in the private local configuration. Anyone who gains authorized access through the associated private MCP connection receives that server's configured capabilities, subject to ChatGPT and OS restrictions.

This is not safe for multiple unrelated users, arbitrary shared workspaces, or public plugin submissions. A production multi-user deployment needs proper OAuth, subject-bound roles, device-specific authorization, approvals, revocation, and an auditable action identity. A public GitHub repository is fine; a publicly accessible unrestricted MCP server is not.

## Multi-chat coordination

Independent ChatGPT conversations can run through separate agent processes.
OpenRemote serializes schema initialization, uses SQLite WAL, applies
advisory file locks across cooperating text/binary writes and caps direct
OS command execution at four concurrent processes per machine.
Background job admission is guarded by a cross-process lock.

These mechanisms prevent common accidental conflicts. They do **not**
authenticate the originating ChatGPT conversation, prevent arbitrary
commands from editing the same files, or isolate unrelated OS applications.
Task labels are user-provided strings, not verified chat identities.

## Persistent jobs and binary-transfer risks

- Persistent jobs are executed as the agent's Linux account and continue after
  the initiating MCP/SSH request ends. They require admin plus
  `ad_hoc_enabled=true`. A private job state directory stores stdout/stderr
  and results, which may contain credentials or sensitive command output.
  Do not put secrets in command arguments, which may be observable via
  process inspection while running.
- Jobs are not interactive PTY sessions, are not isolated containers,
  and do not automatically restart after reboot. The per-device safeguards
  limit active jobs (8), stored jobs (250), runtime (6 hours) and output
  (8 MiB per stream).
- Binary uploads are staged within the approved workspace. Up to eight
  pending sessions may remain on disk for 24 hours, using the configured OS
  user's storage quota. The caller supplies a whole-file SHA-256; mismatches
  are rejected at commit. Base64 request/response payloads can expose file
  contents to the ChatGPT session and associated tool permissions.
- For very large transfers between already trusted Linux machines, use
  a direct verified SSH/SFTP transfer instead of passing file bytes through
  the model context.

## File operation limits

- Only approved workspace roots, owned by the configured OS user; no symlink path traversal.
- read_text and replace_text use UTF-8 text; per-call text limit is 64 KiB.
- read_binary_chunk and upload_chunk support at most 128 KiB raw bytes per
  call; the binary transfer limit is 1 GiB per file, with offset and
  SHA-256 checks.
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

## Remote desktop (0.7.0, opt-in)

Desktop capture and pointer/keyboard input expose **private on-screen data** and
may operate real applications. The new desktop tools require role=admin and
explicit local configuration flags `desktop_enabled=true` and, for input,
`desktop_input_enabled=true`. Both default to false.

On GNOME Wayland, screen capture and input use the XDG RemoteDesktop/ScreenCast
portal and its **native consent dialog**, not unrestricted X11 APIs, root/uinput,
or an unprotected network service. A system-Python worker (required for
distribution-native D-Bus and PipeWire bindings) listens only on a
mode-0600 Unix socket owned by the logged-in user in /run/user/$UID.
One user-approved session expires after 15 minutes idle and can be explicitly
disconnected. A GNOME session prompt and actual PipeWire stream are mandatory.

The screenshot result contains JPEG pixels encoded as base64. It may contain
credentials, chats, files, notifications and personal content. Avoid storing
screenshots in Git, traces, automation logs, or issue reports. Clients must
not invoke input unless the user requested it, and should confirm before
destructive actions even in an approved session. See docs/DESKTOP_ACCESS.md.
