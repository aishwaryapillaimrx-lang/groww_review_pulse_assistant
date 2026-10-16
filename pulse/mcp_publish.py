"""Publish an approved run to Google Docs and Gmail drafts through the n8n MCP server.

Nothing here can send email: only append_to_doc and create_email_draft are ever called.
"""
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

from pulse import approval
from pulse.render import render_doc_entry  # noqa: F401  (re-exported)

load_dotenv()

REQUIRED_TOOLS = ("append_to_doc", "create_email_draft")


class PublishError(Exception):
    pass


def uses_sse(url):
    """n8n's MCP Server Trigger shows an '.../sse' production URL when it speaks the older SSE transport."""
    return url.rstrip("/").endswith("/sse")


@asynccontextmanager
async def n8n_session():
    """Open an MCP session to the n8n MCP Server Trigger (SSE or streamable HTTP by URL, bearer auth)."""
    from mcp import ClientSession

    url, token = os.getenv("N8N_MCP_URL"), os.getenv("N8N_MCP_TOKEN")
    if not url or not token or "<your-n8n>" in url:
        raise PublishError("Set N8N_MCP_URL and N8N_MCP_TOKEN in .env first")
    headers = {"Authorization": f"Bearer {token}"}

    if uses_sse(url):
        from mcp.client.sse import sse_client

        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        http_client = create_mcp_http_client(headers=headers)
        async with http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


def _summarise(result):
    return {
        "is_error": bool(result.is_error),
        "content": [c.text for c in result.content if getattr(c, "text", None)],
    }


def _first_leaf(group):
    """Innermost error message of a (possibly nested) exception or ExceptionGroup, for a readable report."""
    while isinstance(group, BaseExceptionGroup):
        group = group.exceptions[0]
    return f"{type(group).__name__}: {group}"


async def publish(run_id, out_dir="outputs", session_factory=None):
    """Publish one approved run. Safe to call again: tools that already succeeded are skipped."""
    record = approval.assert_publishable(run_id, out_dir)  # raises unless approved and unchanged
    done = record.get("tool_results", {})
    if all(tool in done for tool in REQUIRED_TOOLS):
        return record  # already published; open no connection

    payload, subject, body = approval.load_run(run_id, out_dir)
    calls = {
        "append_to_doc": {"entry_text": render_doc_entry(run_id, payload)},
        "create_email_draft": {"subject": subject, "body": body},
    }
    failure = None
    try:
        async with (session_factory or n8n_session)() as session:
            available = {t.name for t in (await session.list_tools()).tools}
            missing = [t for t in REQUIRED_TOOLS if t not in available]
            if missing:
                failure = f"n8n MCP server is missing tool(s) {missing}; it offers {sorted(available)}"
            else:
                for tool in REQUIRED_TOOLS:
                    if tool in done:
                        continue
                    summary = _summarise(await session.call_tool(tool, calls[tool]))
                    if summary["is_error"]:
                        failure = f"{tool} failed: {summary['content']}"
                        break
                    approval.record_tool_result(run_id, out_dir, tool, summary)
    except PublishError:
        raise
    except Exception as err:  # transport/auth failures arrive as plain errors (SSE) or exception groups (HTTP)
        raise PublishError(f"Could not use the n8n MCP server: {_first_leaf(err)}") from None
    if failure:
        raise PublishError(failure)
    return approval.mark_published(run_id, out_dir)


async def describe_tools(session_factory=None):
    """List the server's tools and their input schemas (used to verify names and arguments)."""
    try:
        async with (session_factory or n8n_session)() as session:
            return [(t.name, t.input_schema) for t in (await session.list_tools()).tools]
    except PublishError:
        raise
    except Exception as err:
        raise PublishError(f"Could not use the n8n MCP server: {_first_leaf(err)}") from None
