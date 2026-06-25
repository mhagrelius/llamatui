"""build_exa_tool wiring — assert the Exa MCP is restricted to search so fetch_url is the
sole retrieval path. No network: we only inspect the constructed tool object."""

from __future__ import annotations

from llamatui.tools import build_exa_tool


def test_exa_tool_is_restricted_to_web_search():
    tool = build_exa_tool()
    assert list(tool.allowed_tools) == ["web_search_exa"]
