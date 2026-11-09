# Releases and platform compatibility

OpenRemote is currently a Python MCP agent for Linux, not a maintained native
Windows or macOS application.

## Compatibility matrix for v0.3.1

| OS | Architecture | Status | Evidence |
| --- | --- | --- | --- |
| Linux | x86_64 | **Tested** | Automated tests on terra; direct ChatGPT MCP calls |
| Linux | aarch64 / ARM64 | **Tested (limited)** | Real remote MCP operations through SSH/Tailscale on rock |
| Windows | x64 / ARM64 | **Not tested / Unsupported** | Linux-specific filesystem and process logic must be ported |
| macOS | Intel / Apple Silicon | **Not tested / Unsupported** | Requires platform-specific filesystem, permissions and service tests |

A Python wheel may be downloadable on any OS. That does NOT establish that
OpenRemote operates securely or successfully on that OS. In v0.3.1, the
Python distribution is still named openremote-probe for historical reasons;
it includes the openremote-agent CLI. A cleaner package rename can be planned
as a separate versioned migration.

## Recommended contents of the first GitHub release

- GitHub source archive attached automatically to each Git tag.
- Linux-tested Python source distribution (.tar.gz) and wheel (.whl).
- SHA-256 checksums for published archives.
- Installation and compatibility guidance, security notes and known limitations.
- No embedded OpenAI API keys, tunnel IDs, SSH credentials, private configs,
  audit SQLite files, generated receipts or real device addresses.

OpenAI tunnel-client is a separate official executable, not included in the
OpenRemote Python package.

## Releasing checklist

1. Verify the chosen commit is on main and scan the changed files for secrets.
2. Run the complete unit suite on Linux.
3. Build with python -m build in an isolated environment.
4. Inspect distribution contents and package metadata for the MIT license.
5. Install the wheel into a fresh Linux venv; validate doctor and safe MCP tools.
6. Test SSH gateway-to-peer behavior with a real authorized Linux peer.
7. Upload tested wheel and sdist alongside release notes and checksums.
8. Mark the initial release as prerelease while authentication, sandboxing and
   cross-platform support remain in development.

## Windows and macOS plans

Windows needs its own secure filesystem implementation (no Linux openat flags),
ACL and symlink validation, subprocess management, SSH integration and real tests.

macOS needs permissions/ACL review, subprocess/process-group and launchd support,
and tests on actual Intel/Apple Silicon machines.

**Do not publish Windows/macOS binaries as working or supported before testing.**
If experimental packages are ever uploaded, label them explicitly as
Not tested / Experimental / No support guarantee, not as verified releases.

See [draft v0.3.1 notes](releases/v0.3.1.md).
