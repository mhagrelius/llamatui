"""Remote tools exposed to the agent.

Currently just Exa's hosted web-search MCP server, so the model can look up current
information. Auth is via the ``EXA_API_KEY`` environment variable (sent as the ``x-api-key``
header); without it the public endpoint still works but is rate-limited.
"""

from __future__ import annotations

import os

from agent_framework import MCPStreamableHTTPTool

EXA_MCP_URL = "https://mcp.exa.ai/mcp"


def build_exa_tool(url: str = EXA_MCP_URL) -> MCPStreamableHTTPTool:
    """Create (but don't yet connect) the Exa web-search MCP tool.

    Searches run automatically — no per-call approval — which suits a local single-user
    setup. Call ``await tool.connect()`` once before use and close it on shutdown.
    """
    headers: dict[str, str] = {}
    key = os.environ.get("EXA_API_KEY")
    if key:
        headers["x-api-key"] = key
    return MCPStreamableHTTPTool(
        name="exa",
        url=url,
        headers=headers or None,
        description="Web search and page retrieval via Exa. Use for current/online information.",
        approval_mode="never_require",
        request_timeout=45,
    )


def exa_key_present() -> bool:
    return bool(os.environ.get("EXA_API_KEY"))
