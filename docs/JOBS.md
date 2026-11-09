# Persistent process jobs (Linux, v0.4.0)

OpenRemote can launch long-running non-interactive commands on a local
machine or on a registered SSH peer. The process and its logs live on the
**target device**, not in a ChatGPT response or a single SSH session.

## Example workflow in ChatGPT

Ask your private OpenRemote plugin to start a build on a registered device:

- Tool: `start_job`
- Device: `rock` (or your own device ID)
- Workspace: `projects`
- argv: `["/usr/bin/python3", "-m", "compileall", "-q", "some-project"]`
- timeout_seconds: `1800`

The response contains a unique `job_id`. Save that ID. Later, even after
reopening the ChatGPT conversation or losing SSH connectivity temporarily:

1. `job_status(job_id, device_id)` reports running/completed/failed/cancelled/
   timed_out/interrupted and output byte counts.
2. `job_output(job_id, stream="stdout", offset=0, limit_bytes=8192,
   device_id)` reads one chunk. Use returned `next_offset` to continue.
3. `list_jobs(device_id, limit=20)` shows recent operations.
4. `cancel_job(job_id, device_id)` requests termination of the command and
   child process group.
5. `purge_job(job_id, device_id)` deletes persisted logs/metadata only
   after the job is no longer running.

**Important:** Background jobs require both `role=admin` and
`ad_hoc_enabled=true` in the **target device's** private config. They run
as the configured Linux user and are high-risk/destructive-capable. A workspace
is only the working directory, not a subprocess filesystem sandbox.

## Multi-chat task labels and idempotency

`start_job` accepts optional `task_label` (1–64 printable characters) and
`idempotency_key` (8–128 safe characters). Two conversations can launch
different jobs independently, while a retry with the **same** idempotency key
and identical arguments returns the existing job ID. Reusing a key with
different commands is rejected.

The idempotency key is scoped to the target machine and is stored only
as a SHA-256 digest in private state. It is **not** an authenticated ChatGPT
conversation ID. Choose a fresh key for each new logical operation.
See [MULTI_CHAT](MULTI_CHAT.md).

## Durability, limits and lifecycle

- A detached per-job Python worker survives normal MCP stdio or SSH session
  closure. The job's stdout/stderr and result are persisted in the target
  device's private state/jobs directory (mode 0700).
- Maximum runtime: **6 hours** per job. Default: 1 hour.
- Logs: up to **8 MiB per stream**; excess output is read and discarded
  to prevent a blocked child or unbounded disk growth. The status reports
  truncation.
- Up to **8 active jobs** and **250 stored job entries** per device. Use
  `purge_job` to release old metadata and log storage.
- Logs are read in up to **16 KiB** chunks and may contain sensitive text.
  Do not expose them to untrusted users or copy secrets into a public issue.
- `cancel_job` uses Linux pidfds when available and signals the worker,
  which stops the child's process group. Cancellation can take several
  seconds. Jobs do not automatically resume after a reboot.
- A sudden crash, kill -9, reboot, or OS session manager policy may produce
  `interrupted`. Test on your own system before using for critical operations.

## Not an interactive PTY yet

`start_job` has **no interactive stdin** and is not a persistent terminal
emulator. Interactive PTY/tmux-like sessions and streaming keyboard input
are a separate planned feature. Use non-interactive commands for now.

A job cannot grant additional root privileges. ChatGPT tool confirmations,
operating-system permissions, and security policy still apply.

See [SECURITY](SECURITY.md) and [TROUBLESHOOTING](TROUBLESHOOTING.md).
