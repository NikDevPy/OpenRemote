# OpenRemote 0.3.1 — owner-operated remote access

Project goal: perform legitimate administration and development on personally
authorized terra and rock computers, with more flexible tools than RDC's
static name-based command block list. This does not waive ChatGPT rules,
confirmation controls, OS permissions or network service limits.

## Workspaces

The owner enables explicit filesystem workspaces in a private device config:

    "workspaces": {
      "projects": {"path": "/home/USER/Projects", "access": "write"}
    }

Tools: list_workspaces; list_files; read_text; write_text (new files only);
replace_text (existing files, with required expected_sha256). The optional
workspace argument defaults to sandbox for backward compatibility.
The workspace root must be owned by the agent user, non-world-writable and
not a symbolic link. Relative-path traversal and symlink hops are refused.
Audit state must be outside all workspaces.

replace_text first checks the current file hash, creates a temporary file in
the same directory, then uses an atomic rename. This is optimistic concurrency
control, not a race-free compare-and-swap against unrelated external editors.
Text calls remain capped at 64 KiB; large-file streaming is a later milestone.

## Ad hoc terminal

The owner enables dynamic commands in the local config:

    "role": "admin",
    "ad_hoc_enabled": true

run_argv accepts a list of executable and arguments, optional workspace,
and timeout. It launches as the OS account running the agent, without shell=True,
interactive stdin or automatic privilege escalation. Unlike RDC it does not
block executables just because of their name. It is deliberately marked as
destructive, write-capable and open-world in the MCP tool metadata. The client
may request confirmation, or refuse individual requests. OS sudoers and PAM
policies still apply; system privileges are not bypassed.

High-risk warning: running a command interpreter is possible when requested
as a program argument, so grant this capability only to a private owner
connection. This MVP still has a global server role, not per-user identity:
do not distribute the new connector or share its tunnel with other users.

Direct run_argv calls are bounded to 60 seconds, 8 KiB each stdout/stderr and
one concurrent process per server instance. Secure MCP Tunnel has independent
limits, including a 120-second request deadline. Durable process jobs will
be developed separately.

## Live acceptance checks on 2026-11-09

Staged terra -> SSH over Tailscale -> rock MCP verified workspace discovery
for both machines, test-file creation and hash-checked replacement on rock,
refusal of stale versions, and blocking of path traversal. Test file was
removed after verification. SmartStation files/services were untouched.

Relevant tests: tests/test_workspace.py and tests/test_ad_hoc.py;
live probes: scripts/live_workspace_smoke.py and scripts/live_ssh_smoke.py.
No privileged sudo operation has been executed on production machines.

## Plugin connection

Keep existing ChatGPT OpenRemote test tunnel unchanged as fallback.
A separate private Secure MCP Tunnel and a ChatGPT custom MCP connection
will be needed to expose the new agent. The code deployed to machines
does not itself register the tool list with ChatGPT.

Next: owner-only authorization, durable process management, incremental
outputs, cancellation, resumable file transfers, Windows/macOS backends.
