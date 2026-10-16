"""Offline tests for the approval gate and MCP publish with an in-memory stub session. SYNTHETIC data."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from pulse import approval
from pulse.approval import ApprovalError
from pulse.mcp_publish import PublishError, publish, render_doc_entry

RUN = "abc12345"
PAYLOAD = {
    "date": "2026-10-16",
    "top_themes": [{"name": "App bugs", "count": 554, "share": 0.1542}],
    "weekly_pulse": "# Pulse\nBody",
    "identified_fee_issue": {"fee_key": "brokerage", "label": "Equity brokerage", "mentions": 96},
    "explanation_bullets": ["Brokerage is ₹20 or 0.1% per order."],
    "source_links": ["https://groww.in/pricing/stocks", "https://example.org/help"],
}
SUBJECT = "Weekly Product Pulse + Customer Clarification — 2026-10-16"
BODY = "# Pulse\nBody\n\n--- Support snippet: Equity brokerage ---\n- Brokerage is ₹20 or 0.1% per order.\n"


class FakeSession:
    def __init__(self, tools=("append_to_doc", "create_email_draft"), fail_once=()):
        self.tools, self.fail_once, self.calls = tools, set(fail_once), []

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in self.tools])

    async def call_tool(self, name, args):
        if name in self.fail_once:
            self.fail_once.discard(name)
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="boom")])
        self.calls.append((name, args))
        return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=f"{name} ok")])


def factory_for(session):
    opened = []

    @asynccontextmanager
    async def factory():
        opened.append(1)
        yield session

    return factory, opened


@pytest.fixture
def run_dir(tmp_path):
    approval.write_run_files(RUN, PAYLOAD, SUBJECT, BODY, tmp_path)
    return tmp_path


def publish_sync(run_dir, factory):
    return asyncio.run(publish(RUN, run_dir, session_factory=factory))


def test_approval_required_when_pending(run_dir):
    factory, opened = factory_for(FakeSession())
    with pytest.raises(ApprovalError):
        publish_sync(run_dir, factory)
    assert opened == []  # no connection was even opened


def test_reject_writes_nothing_and_blocks_publish(run_dir):
    record = approval.reject(RUN, "Priya", run_dir, "numbers look off")
    assert record["status"] == "rejected" and record["reason"] == "numbers look off"
    factory, opened = factory_for(FakeSession())
    with pytest.raises(ApprovalError):
        publish_sync(run_dir, factory)
    assert opened == []


def test_payload_tamper_blocked(run_dir):
    approval.approve(RUN, "Priya", run_dir)
    path = run_dir / f"payload_{RUN}.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["weekly_pulse"] += "\nExtra line added after approval"
    path.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    factory, opened = factory_for(FakeSession())
    with pytest.raises(ApprovalError, match="changed after approval"):
        publish_sync(run_dir, factory)
    assert opened == []


def test_email_tamper_blocked(run_dir):
    approval.approve(RUN, "Priya", run_dir)
    path = run_dir / f"email_{RUN}.json"
    path.write_text(json.dumps({"subject": SUBJECT, "body": BODY + "Buy now!"}, ensure_ascii=False), encoding="utf-8")
    factory, opened = factory_for(FakeSession())
    with pytest.raises(ApprovalError):
        publish_sync(run_dir, factory)


def test_approver_name_required_and_decision_is_final(run_dir):
    with pytest.raises(ApprovalError):
        approval.approve(RUN, "  ", run_dir)
    approval.approve(RUN, "Priya", run_dir)
    with pytest.raises(ApprovalError):
        approval.reject(RUN, "Priya", run_dir)  # already approved


def test_publish_calls_both_tools_then_is_idempotent(run_dir):
    approval.approve(RUN, "Priya", run_dir)
    session = FakeSession()
    factory, opened = factory_for(session)
    record = publish_sync(run_dir, factory)

    assert [name for name, _ in session.calls] == ["append_to_doc", "create_email_draft"]
    doc_args, draft_args = session.calls[0][1], session.calls[1][1]
    assert f"run {RUN}" in doc_args["entry_text"] and doc_args["entry_text"].startswith("\n\n====")
    assert draft_args == {"subject": SUBJECT, "body": BODY}
    assert record["published"] and record["approver"] == "Priya"

    publish_sync(run_dir, factory)  # second publish of the same run
    assert len(session.calls) == 2 and len(opened) == 1  # nothing appended, no new connection


def test_partial_failure_resumes_without_duplicating_doc_entry(run_dir):
    approval.approve(RUN, "Priya", run_dir)
    session = FakeSession(fail_once={"create_email_draft"})
    factory, _ = factory_for(session)
    with pytest.raises(PublishError, match="create_email_draft failed"):
        publish_sync(run_dir, factory)
    assert [n for n, _ in session.calls] == ["append_to_doc"]
    assert "append_to_doc" in approval.load_record(RUN, run_dir)["tool_results"]

    publish_sync(run_dir, factory)  # resume
    assert [n for n, _ in session.calls] == ["append_to_doc", "create_email_draft"]  # doc appended once


def test_missing_tool_makes_no_calls(run_dir):
    approval.approve(RUN, "Priya", run_dir)
    session = FakeSession(tools=("append_to_doc",))
    factory, _ = factory_for(session)
    with pytest.raises(PublishError, match="missing"):
        publish_sync(run_dir, factory)
    assert session.calls == []


def test_doc_entry_format():
    entry = render_doc_entry(RUN, PAYLOAD)
    assert "==== Weekly Pulse — 2026-10-16 (run abc12345) ====" in entry
    assert "• Brokerage is ₹20" in entry and "- https://groww.in/pricing/stocks" in entry
    assert "App bugs (554, 15.4%)" in entry


def test_no_send_tool_is_ever_called(run_dir):
    approval.approve(RUN, "Priya", run_dir)
    session = FakeSession(tools=("append_to_doc", "create_email_draft", "send_email"))
    factory, _ = factory_for(session)
    publish_sync(run_dir, factory)
    assert all(name != "send_email" for name, _ in session.calls)


def test_transport_chosen_from_url():
    from pulse.mcp_publish import uses_sse

    assert uses_sse("https://x.app.n8n.cloud/mcp/groww-pulse/sse")
    assert uses_sse("https://x.app.n8n.cloud/mcp/groww-pulse/sse/")
    assert not uses_sse("https://x.app.n8n.cloud/mcp/groww-pulse")
