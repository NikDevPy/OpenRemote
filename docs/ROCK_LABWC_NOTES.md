# Rock labwc deployment notes

The Rock 5B+ station uses labwc and runs its MCP gateway and graphical session
under different local users. The gateway forwards the explicitly registered
`rock-master` peer over SSH. Screen access runs as the graphical owner, not as
the gateway user or root. Desktop access is enabled on that peer for viewing;
input remains disabled. The old gateway and peer environments are retained.

The station's graphical session and systemd user manager have separate D-Bus
session addresses. Before this setup, the user manager's activation
environment contained the graphical session bus; user-systemd portal services
could not acquire their expected names and stalled. Scoped drop-ins for the
portal, its wlroots and GTK backends, permission store, and document portal
direct those services to the user-manager bus. Do not rewrite the global user
manager environment or unrelated station services to work around this.

The labwc portal configuration chooses wlroots for ScreenCast and Screenshot,
with an interactive slurp output selector. Portal service introspection and a
gateway-to-peer MCP schema/status exchange passed on the device. A real portal
consent session, PipeWire frame, and plugin-host tool refresh have **not** been
verified. The station owner must confirm the output selection on its display.

Rollback: restore the gateway's saved tunnel profile and restart only its
OpenRemote tunnel; the previous agent and peer configs remain untouched. To
undo the portal-specific setup, remove only the drop-ins created for OpenRemote
and the two newly created labwc portal configuration files, then reload the
user systemd manager. Review local changes before removing anything.
