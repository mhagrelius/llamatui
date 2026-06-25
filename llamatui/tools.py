"""Remote tools exposed to the agent.

Currently just Exa's hosted web-search MCP server, so the model can look up current
information. Auth is via the ``EXA_API_KEY`` environment variable (sent as the ``x-api-key``
header); without it the public endpoint still works but is rate-limited.
"""

from __future__ import annotations

import os

from agent_framework import MCPStreamableHTTPTool

EXA_MCP_URL = "https://mcp.exa.ai/mcp"

# The when-to-use note spliced into the system prompt's "Your tools" section (assembled by
# AgentBuilder). Lives with the tool it describes; becomes this capability's description when web
# search moves to an agent-framework skill. Policy only — what the tool *does* is in its own
# description above, which the model also sees.
WEB_SEARCH_GUIDANCE = (
    "Web search (Exa): reach for it to find sources when the answer depends on current or "
    "fast-changing facts (news, prices, releases and versions, dates, people, ongoing events), "
    "or when you are not sure a fact is still true. Use focused queries, corroborate what "
    "matters, and cite the URLs. To read a specific result in full, fetch it with fetch_url. "
    "Do not search for stable knowledge or your own reasoning."
)


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
        description="Web search via Exa. Use for finding current/online sources to read.",
        approval_mode="never_require",
        request_timeout=45,
        allowed_tools=["web_search_exa"],
    )


def exa_key_present() -> bool:
    return bool(os.environ.get("EXA_API_KEY"))
