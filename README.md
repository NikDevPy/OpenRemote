# OpenRemote

**A self-hosted MCP remote-control agent for your own Linux computers.** Connect a local machine to ChatGPT through an OpenAI Secure MCP Tunnel, and optionally route requests to other machines over SSH (including Tailscale). Each user deploys **their own private instance** with their own credentials, devices, and permissions.

> **Security warning:** OpenRemote can read and modify files and, if explicitly enabled, run arbitrary programs as the account hosting the agent. Treat its private ChatGPT connection as privileged access to your computer. Never publish a tunnel credential, share a private connector, or expose an unrestricted instance to strangers.

## What it does

- **Multiple machines:** select a registered device by its device ID. A gateway agent forwards to trusted peers using SSH with host-key verification.
- **Projects and files:** list owner-approved workspaces, read files, create new text files, and replace existing text only when its SHA-256 checksum matches.
- **Commands:** run named commands from a local allowlist. An opt-in advanced mode runs an executable and arguments without a fixed command-name blacklist.
- **Approvals and audit:** role checks (viewer/operator/admin), one-time local approval for sensitive predefined commands, command timeouts, and an SQLite audit log.
- **Private ChatGPT integration:** an outbound Secure MCP Tunnel is used instead of opening an inbound port.

**Current release:** 0.3.1, Linux only, Python 3.11+, MCP SDK 1.29.0. This project is under active development and is not a production-grade multi-user remote-administration service.

## Start here

**[Step-by-step installation and ChatGPT setup →](docs/QUICKSTART.md)**

The guide covers:

1. Installing OpenRemote on a Linux computer.
2. Creating a private configuration and selecting permitted workspaces.
3. Adding a second computer using SSH or Tailscale (optional).
4. Creating your own OpenAI Secure MCP Tunnel and running tunnel-client.
5. Installing your own private custom MCP plugin in ChatGPT.
6. Checking tools, logs, access controls, and common connection failures.

A minimal local installation (read-only by default):

~~~bash
git clone https://github.com/NikDevPy/OpenRemote.git
cd OpenRemote
python3 -m venv .venv
.venv/bin/python -m pip install .
mkdir -p "$HOME/.config/openremote-agent"
cp examples/local-agent.example.json "$HOME/.config/openremote-agent/agent.json"
chmod 600 "$HOME/.config/openremote-agent/agent.json"
~~~

**Before running the agent**, edit the JSON file: replace YOUR_USER and create the indicated private directories (0700). Then:

~~~bash
.venv/bin/python -m openremote_agent.server \
  --config "$HOME/.config/openremote-agent/agent.json" --doctor
.venv/bin/python -m unittest discover -s tests -q
~~~

See the [quickstart](docs/QUICKSTART.md) for the remaining steps. Never copy somebody else's real tunnel ID, runtime API key, SSH credentials, or machine addresses into your configuration.

## MCP tools

| Tool | What it does | Access |
| --- | --- | --- |
| system_info | OS, architecture, and device ID | viewer |
| list_devices | Local and configured SSH peers | viewer |
| list_workspaces | Available file roots and read/write settings | viewer |
| list_files | List up to 100 entries in a workspace | viewer |
| read_text | Read UTF-8 text, up to 64 KiB, with SHA-256 | viewer |
| write_text | Create a new UTF-8 file; never overwrite | operator |
| replace_text | Replace existing text using expected SHA-256 | operator |
| preview_command | Inspect a predefined command's policy | viewer |
| execute_command | Execute a predefined command ID | operator / admin + local approval |
| run_argv | Opt-in arbitrary executable + arguments | admin + ad_hoc_enabled |
| audit_tail | Recent local audit events | admin |

The **workspace restriction applies to file tools only**. It does **not** sandbox processes started with run_argv; such processes retain all permissions of their operating-system account. Avoid enabling run_argv unless you understand this distinction. The role in the current version is a server-level role, **not an identity verified independently for each ChatGPT user**.

## Architecture

~~~text
Your ChatGPT account (private custom MCP plugin)
                     |
          OpenAI Secure MCP Tunnel
          (outbound tunnel-client)
                     |
             OpenRemote gateway
               on your Linux PC
                     |
        +------------+-------------+
        |                          |
   Local workspaces            SSH / Tailscale
                                   |
                            OpenRemote peer
                              on your server
~~~

A public **GitHub repository** does not create a public **plugin**. Every person who uses OpenRemote must operate their own server, tunnel, and private ChatGPT connection. Secure MCP Tunnel is for private MCP integrations; public plugin submissions have different HTTPS and authentication requirements.

## Limitations

- Linux-focused; no Windows/macOS native agent or GUI yet.
- Text file calls are limited to 64 KiB; direct command calls are limited to 60 seconds and 8 KiB per output stream.
- The OpenAI tunnel has separate request, message-size, and rate limits. This is not an unlimited-throughput connection.
- Configured SSH peers are reported as configured, not automatically proven online.
- No per-user OAuth, hard isolation for run_argv, streaming file transfers, or durable PTY/job management yet.
- No guarantee that custom MCP plugins or Secure MCP Tunnel are enabled in every ChatGPT account/workspace.

## Documentation

- **[QUICKSTART — installation, SSH, tunnel and ChatGPT](docs/QUICKSTART.md)**
- **[SECURITY — privilege model, approvals, risk and isolation](docs/SECURITY.md)**
- **[TROUBLESHOOTING — tunnel, SSH, permissions, common errors](docs/TROUBLESHOOTING.md)**
- [Owner-control technical notes](docs/owner-control.md)
- [SSH routing design notes](docs/ssh-routing.md)
- [Historical probe and compatibility research](docs/compatibility.md)

Some older engineering notes in docs/ are in Russian. The three user-facing guides above are in English.

## Development and contributing

~~~bash
.venv/bin/python -m unittest discover -s tests -v
~~~

Please do not submit runtime keys, tunnel IDs from your account, personal SSH hostnames/IP addresses, generated receipts, SQLite state, or private configurations in issues or pull requests.

See the [OpenAI Secure MCP Tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server) for the latest service requirements. Platform features and UI labels can change.

## License

A public GitHub repository is not automatically open-source licensed. The repository owner must choose and add an explicit license before granting general reuse and redistribution rights.
