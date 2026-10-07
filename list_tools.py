#!/usr/bin/env python3
"""List tools and selected schemas exposed by a Streamable HTTP MCP server."""

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlsplit


DEFAULT_SCHEMAS = ("read_file", "list_directory", "apply_patch")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://HOST/mcp TOKEN\n"
            "  %(prog)s https://HOST/mcp --token TOKEN\n"
            "  %(prog)s https://HOST/mcp  # token from MCP_TOKEN\n\n"
            "Prefer MCP_TOKEN to avoid exposing a token in command-line arguments.\n"
            "Use the complete endpoint URL, including its path."
        ),
    )
    parser.add_argument("url", help="Streamable HTTP MCP endpoint URL")
    parser.add_argument("positional_token", nargs="?", metavar="TOKEN",
                        help="Bearer token (legacy positional form)")
    parser.add_argument("--token", dest="option_token", metavar="TOKEN",
                        help="Bearer token; otherwise use positional TOKEN or MCP_TOKEN")
    parser.add_argument("--schema", action="append", metavar="TOOL",
                        help="Print this tool's schema; repeat for multiple tools (default: read_file, list_directory, apply_patch)")
    parser.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS",
                        help="Overall discovery timeout (default: 30 seconds)")
    args = parser.parse_args(argv)
    try:
        parsed = urlsplit(args.url)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(c.isspace() for c in args.url):
        parser.error("URL must be a valid http:// or https:// endpoint")
    if parsed.username is not None or parsed.password is not None:
        parser.error("do not embed credentials in the URL")
    if parsed.fragment:
        parser.error("URL must not contain a fragment")
    if args.positional_token is not None and args.option_token is not None:
        parser.error("use either positional TOKEN or --token, not both")
    args.token = (args.option_token if args.option_token is not None else
                  args.positional_token if args.positional_token is not None else
                  os.environ.get("MCP_TOKEN"))
    if not args.token or not args.token.strip():
        parser.error("a bearer token is required: supply TOKEN, --token TOKEN, or MCP_TOKEN")
    if any(c.isspace() for c in args.token):
        parser.error("bearer token must not contain whitespace")
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a finite positive number")
    args.schemas = DEFAULT_SCHEMAS if args.schema is None else tuple(args.schema)
    return args


async def list_tools(args):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {args.token}"}
    async with streamablehttp_client(args.url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools, cursor, seen_cursors = [], None, set()
            while True:
                page = await session.list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise RuntimeError("server returned a repeated pagination cursor")
                seen_cursors.add(cursor)

    print(f"Tool count: {len(tools)}")
    for tool in sorted(tools, key=lambda item: item.name):
        print(tool.name)
    by_name = {tool.name: tool for tool in tools}
    for name in args.schemas:
        if name not in by_name:
            print(f"Warning: schema not found for {name}", file=sys.stderr)
            continue
        print(f"\n{name} schema:")
        print(json.dumps(by_name[name].inputSchema, indent=2))


async def run(args):
    await asyncio.wait_for(list_tools(args), timeout=args.timeout)


def main(argv=None):
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except asyncio.TimeoutError:
        print(f"Error: discovery exceeded {args.timeout:g} seconds.", file=sys.stderr)
        return 1
    except Exception as exc:
        # Suppress exception details that might include credentials or request URLs.
        print(f"Error: MCP discovery failed ({type(exc).__name__}). Check SDK installation, endpoint, authentication and server logs.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
