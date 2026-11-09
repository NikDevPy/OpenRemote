# Parallel ChatGPT chats, admission limits and task isolation

OpenRemote is designed to accept independent MCP requests from multiple
conversations. Its **device state** is shared; ChatGPT chat identity is
**not** automatically supplied to the private MCP server. Task labels are
therefore organizational names, not authenticated user identities.

## What prevents conflicts

- SQLite audit uses WAL and serialized first-time schema initialization,
  so separate agent processes can write concurrently.
- Text creation uses exclusive file creation; text replacement requires
  the previously observed SHA-256.
- Cooperating OpenRemote text and binary file replacements acquire a
  cross-process per-target advisory lock, including when two workspace
  aliases identify the same root. Only one conflicting writer may commit
  the same old hash; others must re-read and reconcile.
- The optional `probe_device` performs a real read-only SSH/MCP check, with a
  short **20-second cache** shared across chats. Configured peers are not
  assumed online without a successful probe.
- Direct `run_argv` and `execute_command` share a **four-slot**,
  cross-process local admission limit. A saturated gateway refuses
  additional work promptly instead of spawning unlimited processes.
- Background jobs use a locked admission path with **eight active jobs**
  and **250 retained jobs** per device. Each job gets a random ID.
- `start_job` accepts optional `task_label` and `idempotency_key`.
  Submitting an identical request with the same key returns the prior
  job ID, preventing unintended duplicate job starts during retries.
  Reusing the key for different commands is rejected.

The same device can run distinct jobs with different IDs in parallel.
These limits apply separately on each machine. Do not assume that a chat
has exclusive possession of the filesystem or any external program.

## Example

Conversation A launches a build with:

~~~text
start_job(
  device_id="gateway",
  workspace="projects",
  argv=["/usr/bin/python3", "-m", "compileall", "project-a"],
  timeout_seconds=1800,
  task_label="project-a-build",
  idempotency_key="project-a-build-request-001"
)
~~~

Conversation B can launch a different build on the same gateway using
a different label and idempotency key, as long as free job slots exist.

Both chats can independently ask for `job_status(job_id)` or
`job_output(job_id, offset=...)` when they know the job ID.
The idempotency key is scoped to the **target device state**, not to
a particular ChatGPT conversation. It must be unique per intended
logical operation; repeated submissions of the same key mean **retry**.

Avoid running two deployments or firmware flashing jobs against the same
physical device unless the external tool offers its own safe locking.
An OpenRemote file lock does not constrain a command started by
`run_argv`, scripts, editors or other processes that ignore advisory locks.

## Reproducible performance experiment

~~~bash
python scripts/benchmark_concurrent_mcp.py \
  --config ~/.config/openremote-agent/agent.json \
  --target gateway --sessions 8
~~~

This creates temporary per-test state and runs independent local stdio MCP
sessions, with three read-only requests per simulated client.
It does **not** send requests through ChatGPT or OpenAI's hosted tunnel.
Use at most 12 test sessions; do not benchmark an active production
device under heavy loads without capacity planning.

### Verified test environment (2026-11-09)

| Target | Simulated sessions | Calls | Successful | Wall time |
| --- | ---: | ---: | ---: | ---: |
| Linux x86_64 gateway | 8 | 24 | 24 | 0.803 s |
| Linux ARM64 via SSH | 4 | 12 | 12 | 5.769 s |

These are observations on one private development setup, not guaranteed
performance or hosted ChatGPT latencies. Peer RPC sessions remain the
more expensive part and may be optimized with a bounded persistent
connection design in future releases.

## Platform limits and security

The OpenAI Secure MCP Tunnel currently enforces a shared default rate
of 600 requests/minute (10 sustained per second), 10 MiB serialized
messages and a 120-second request deadline. Multiple chats and
tunnel-client processes do **not** multiply those limits.
See the [official documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

There is **no trustworthy per-chat or per-user identity** supplied to this
agent. The global Linux agent role applies to all users authorized to invoke
this private plugin. Use separate OS users, tunnels, agents, workspaces and
per-user OAuth where truly separate human trust boundaries are required.

See [SECURITY](SECURITY.md) and [JOBS](JOBS.md).
