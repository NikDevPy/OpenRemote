# OpenRemote vs Remote Desktop Commander: capability gaps

Date: 2026-11-09. The comparison concerns the **current installed RDC configuration on terra, v0.3.1**, not a claim that every RDC deployment blocks the same operations.

## RDC block list observed

`mkfs`, `format`, `mount`, `umount`, `fdisk`, `dd`, `parted`, `diskpart`, `sudo`, `su`, `passwd`, `adduser`, `useradd`, `usermod`, `groupadd`, `chsh`, `visudo`, `shutdown`, `reboot`, `halt`, `poweroff`, `init`, `iptables`, `firewall`, `netsh`, `sfc`, `bcdedit`, `reg`, `net`, `sc`, `runas`, `cipher`, `takeown`.

This is the **plugin's own configuration**, not a list of actions approved or prohibited by ChatGPT.

## OpenRemote architecture

- No command-name global blacklist inherited from RDC. Privileged actions can be declared in the owner's private policy as a precise executable and fixed arguments.
- Linux-sensitive binaries require `risk=privileged`. A sudo policy must use `sudo -n` and still relies on a narrow OS sudoers rule; it cannot grant rights it does not possess.
- `preview_command` is read-only; `execute_command` is honestly annotated as potentially destructive and open-world.
- `privileged` requires server role admin and one-shot approval in the **target device's interactive terminal** (1–300 seconds). The approval is consumed before execution and bound to a SHA-256 digest of command arguments, target identity and sandbox.
- Route through a fixed registry of SSH devices; no user-supplied hostnames or remote shell commands.
- OpenRemote deliberately does not attempt to bypass platform-level denials, user-confirmation prompts or OS permissions.

## Limits and priority roadmap

| Priority | Next capability | Gate before rollout |
|---|---|---|
| P0 | Connect **new** eight-tool OpenRemote Agent as a separate private ChatGPT plugin; preserve existing OpenRemote test | Full tool discovery, read/write confirmation and auth checks |
| P0 | Per-user identity and authorization, separate local device approvals | No global admin role accessible to arbitrary plugin users |
| P1 | Concrete administrative tools: service inspection/restart, logs, network diagnostics, power operations | Explicit schemas, least privilege and destructive annotations |
| P1 | Durable command jobs: PID, output pagination, cancel, bounded PTY sessions | Session limits, kill-group behavior, bounded logs and user consent |
| P1 | Reliable file operations: atomic edits with expected hashes, patches, rollback and binary transfer | No symlink traversal, conflict detection and limits |
| P2 | Background status/heartbeats, SSH reconnect, Tailscale onboarding, OS-specific adapters | Identity binding, host-key verification and no implicit trust |
| P2 | Windows/macOS support and GUI integration | OS-specific security review and integration tests |

**Presently proven:** 33 automated tests on terra; actual SSH/MCP terra→rock, named standard command, file create/read/list, path traversal rejection. Elevated sudo execution has *not* been tested in production and is not enabled in active profiles.

A public plugin submission would need action-specific reviewable tools and may be rejected for a generic remote executor. Private testing does not establish publication approval.
