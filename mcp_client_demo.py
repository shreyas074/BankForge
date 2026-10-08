"""
mcp_client_demo.py — drive a live BankForge MCP server with a real Claude
API call, using your own Anthropic API key.

This is the strongest verification available for brief §6's "Real
@mcp.tool() / FastMCP registration ... verify against the real mcp
package" requirement: it doesn't just import the server module, it spawns
it as a real subprocess over the real MCP stdio transport, asks it for its
real registered tool list (`list_tools`), hands that list to Claude via
the Messages API, and lets Claude decide which tool(s) to call — then
actually calls them against the live server and feeds the real results
back. If this runs end-to-end, your servers are provably speaking real
MCP, not just executing Python functions.

Requires network access (this sandbox has none, so it can't be run here):

    pip install anthropic mcp
    export ANTHROPIC_API_KEY=sk-ant-...

Usage:
    python3 mcp_client_demo.py accounts_server \\
        "What's the account summary for ACC-10042, viewed as a teller?"

    python3 mcp_client_demo.py products_server \\
        "Check whether CUS-10042's risk rating makes them eligible for \\
         PROD-PL-01, then submit a loan application for 2,50,000 if so."

    python3 mcp_client_demo.py compliance_comms_server \\
        "What's the KYC status of CUS-10042, and are there any fraud flags?"

Written against the documented `mcp` and `anthropic` SDK APIs — if a
method signature has shifted slightly in the version pip installs for
you, the fix is almost always a one-line adjustment (e.g. list_tools()'s
exact return shape), not a redesign.
"""
from __future__ import annotations

import asyncio
import sys

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-sonnet-5"  # swap for "claude-haiku-4-5-20251001" for a cheaper/faster run


def _mcp_tool_to_anthropic_schema(tool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


async def run(server_module: str, user_message: str) -> None:
    server_params = StdioServerParameters(command="python3", args=["-m", server_module])
    client = anthropic.Anthropic()

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_response = await session.list_tools()
            anthropic_tools = [_mcp_tool_to_anthropic_schema(t) for t in tools_response.tools]

            print(f"Connected to {server_module} over real MCP stdio transport.")
            print(f"Discovered {len(anthropic_tools)} real registered tools:")
            for t in anthropic_tools:
                print(f"  - {t['name']}")
            print()

            messages = [{"role": "user", "content": user_message}]

            while True:
                response = client.messages.create(
                    model=MODEL, max_tokens=1024, tools=anthropic_tools, messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    for block in response.content:
                        if block.type == "text":
                            print("Claude:", block.text)
                    break

                tool_results = []
                for tool_use in tool_uses:
                    print(f"[Claude is calling: {tool_use.name}({tool_use.input})]")
                    result = await session.call_tool(tool_use.name, arguments=tool_use.input)
                    result_text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
                    print(f"[Server responded: {result_text}]\n")
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tool_use.id, "content": result_text,
                    })

                messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 mcp_client_demo.py <accounts_server|products_server|compliance_comms_server> <message>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], " ".join(sys.argv[2:])))
