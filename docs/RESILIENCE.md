# OpenRemote 0.7.1: reconnect-safe jobs and tunnel monitoring

This release works with a local Linux OpenRemote agent and any registered
Linux SSH peer (for example \`rock\` or \`terra\`). **Both endpoints must run
0.7.1** before the new remote tools work. Existing 0.7.0 tools are unchanged.

OpenRemote cannot prevent the ChatGPT client/UI, Secure MCP Tunnel or network
from disconnecting. These features reduce the work lost when they do.

## Start durable jobs, then use one short status request

\`start_job(device_id="rock", ..., idempotency_key="unique-operation-001")\`
starts a detached worker. Reuse the *same* idempotency key only when retrying
the *same* operation after a disconnect; it returns the original job ID.

\`job_snapshot(job_id, device_id="rock", tail_bytes=1024)\` returns, together:

- job state, code, start time and elapsed seconds;
- stdout/stderr total sizes and truncation flags;
- at most 1024 bytes from **each** stream by default (up to 4096 per stream);
- the tail byte offsets for reading earlier output with \`job_output\`.

No command is started by \`job_snapshot\`. It needs the **admin** role.
Output is untrusted and may contain sensitive data, so do not echo secrets
to ChatGPT or store them in checkpoints.

## Checkpoints: survive a new MCP process and a new chat

Call \`task_checkpoint\` after a significant stage:

~~~text
task_checkpoint(
  task_id="smartstation-graphics-20261113",
  stage="nora-baseline",
  summary="Read-only comparison completed",
  job_id="<valid local job ID>",
  expected_revision=0,
  device_id="rock"
)
~~~

It returns \`revision=1\`. For the next stage provide
\`expected_revision=1\`. If another conversation updated the task first,
a stale revision fails rather than silently overwriting progress.
Repeating the same request after losing its response is idempotent.

Recover after reconnection with:

~~~text
task_resume(task_id="smartstation-graphics-20261113",
            device_id="rock", tail_bytes=1024)
~~~

It returns the checkpoint, associated job snapshot and \`job_missing\` if the
old job was purged. \`list_tasks(device_id="rock")\` finds recent checkpoints.

Checkpoints are local to the **target device**. Keep one task ID per
independent workflow; a ChatGPT chat ID or identity is **not** provided to
OpenRemote and is not an authentication boundary. State lives under
\`<agent state>/checkpoints/\`, owned by the agent's OS user and mode 0700,
with atomic replacement and revision-checked cross-process locking.
Maximum 250 records; task ID and stage are restricted ASCII identifiers,
and \`summary\` is at most 240 printable characters.

**Never place secrets, addresses, tokens, raw dialogue, microphone audio,
log bodies or full prompts in a checkpoint.** Use stage labels and concise
operational descriptions only.

## Optional tunnel monitor

The tunnel monitor is intentionally **not enabled automatically**. Run it on
the machine that hosts \`tunnel-client\`, not necessarily the SSH target.

~~~bash
/path/to/venv/bin/python -m openremote_agent.tunnel_health \
  --url http://127.0.0.1:18767/readyz
~~~

It verifies the configured local HTTP readiness endpoint, records consecutive
failures under \`~/.local/state/openremote-tunnel/status.json\` (mode 0600),
and prints compact JSON containing the last health result. It never sends
API keys, logs or request content and it never contacts arbitrary URLs.
The URL must point to loopback HTTP \`/readyz\`.

If the owner explicitly opts in, a missed-health threshold triggers a
**systemd user-service restart** with a cooldown:

~~~bash
/path/to/venv/bin/python -m openremote_agent.tunnel_health \
  --url http://127.0.0.1:18767/readyz \
  --service openremote-agent-tunnel.service \
  --failures 3 --cooldown-seconds 300 --allow-restart
~~~

Use \`scripts/install_tunnel_monitor.sh\` to install a once-per-minute user
timer. It defaults to monitoring only; automatic restart is opt-in.
If the machine is offline, this local monitor can help recover its tunnel
but cannot repair the network or a broken ChatGPT session. A wrong port
will be reported as offline; verify the actual \`--health-listen-addr\` first.

For Rock and terra, install the same OpenRemote release on **both** agents;
keep device-specific configs, runtime keys, SSH host pinning, service names,
and permissions unchanged. Update the gateway/plug-in definition last,
after local self-tests and rollback preparation.

## Verification

~~~bash
python -m unittest discover -s tests -q
python -m openremote_agent.server --config ~/.config/openremote-agent/agent.json --doctor
~~~

Test \`job_snapshot\`, \`task_checkpoint\`, \`task_resume\` and \`list_tasks\`
locally before testing them through an SSH peer and the hosted tunnel.
Do not restart a running tunnel merely to test a connection.
