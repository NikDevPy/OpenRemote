"""Single binary entrypoint for PyInstaller Linux distributions.

GUI mode launches Qt. --agent-stdio starts the MCP agent without Qt, so the
official tunnel-client can invoke the exact same packaged executable.
"""
from __future__ import annotations
import sys


def main():
    if "--agent-stdio" in sys.argv:
        sys.argv.remove("--agent-stdio")
        from openremote_agent.server import main as agent_main
        return agent_main()
    from .app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
