# Trusted SSH routing between OpenRemote agents

An OpenRemote gateway connects to additional Linux agents through **SSH over stdio**. Tailscale is optional: any trusted network with properly verified SSH host keys can be used.

~~~text
Your ChatGPT private MCP plugin
           |
    Secure MCP Tunnel
           |
    OpenRemote gateway (local MCP agent)
           |
       SSH/Tailscale
           |
    OpenRemote peer (separate policy and workspace)
~~~

Every peer must be explicitly registered by the gateway owner. The gateway refuses arbitrary target device IDs, and does not accept SSH hostnames or remote shell code from MCP tool arguments.

## 1. Prepare the remote agent

Install OpenRemote on each remote Linux machine following [QUICKSTART](QUICKSTART.md). The remote machine must have a unique device_id, its own absolute Python/config paths and permissions, and its own workspace and role settings.

A peer agent need not expose any public HTTP port: the gateway launches the remote agent over SSH and uses its stdio MCP interface.

## 2. Confirm trusted SSH access

From the gateway's operating-system user, configure your own SSH alias, user and identity. For example, in ~/.ssh/config (replace all placeholders):

~~~sshconfig
Host my-remote
    HostName 100.64.1.23
    User REMOTE_USER
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    BatchMode yes
~~~

This address is a **placeholder**; replace it with the peer's actual Tailscale/private address. Verify the host key through a trusted out-of-band method before accepting it.

~~~bash
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes my-remote 'hostname'
~~~

The gateway uses noninteractive SSH and disables forwarding and agent forwarding. If SSH asks for a password or rejects a host key, repair the trust configuration rather than disabling security checks.

## 3. Add the peer to your gateway's private agent.json

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

Merge this section into the gateway configuration; it is not a complete agent.json. The key buildbox must match the remote agent's device_id. The gateway's target alias and host_name must identify the same verified machine; the remote Python and config must be absolute paths.

Run --doctor on the gateway and remote. Confirm that each SSH hop launches the correct agent account. ChatGPT can request list_devices on the gateway and system_info(device_id="buildbox") once the private plugin is connected.

## Authorization and limitations

- A peer registered in the config is reported as **configured**, not guaranteed online.
- The gateway enforces its local permission to use the tool; the peer separately enforces its own role, workspace permissions and command policy.
- MCP tool arguments never become the SSH hostname or SSH program invocation.
- A failed private SSH connection must not be silently rerouted through an unapproved public endpoint.
- Per-call SSH sessions are currently established on demand. Heartbeats, automatic enrollment, long-running remote PTY sessions and automatic reconnect are not yet provided.
- Sensitive credentials, real Tailscale IPs, hostnames and private config files must never be committed to this repository.

See [SECURITY](SECURITY.md) and [TROUBLESHOOTING](TROUBLESHOOTING.md) for important limitations and failure recovery.
