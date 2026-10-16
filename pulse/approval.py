"""Approval record, hash and publish guard (implementation.md §6, PRD R4.2, R4.6)."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pulse.render import render_doc_entry


class ApprovalError(Exception):
    pass


def payload_hash(payload, subject, body):
    """Hash of exactly what will be written: the doc payload plus the email subject and body."""
    canonical = json.dumps({"payload": payload, "subject": subject, "body": body}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _paths(run_id, out_dir):
    out = Path(out_dir)
    return out / f"approval_{run_id}.json", out / f"payload_{run_id}.json", out / f"email_{run_id}.json"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_run_files(run_id, payload, subject, body, out_dir):
    """Store the exact content to be approved, and a pending approval record for it."""
    approval_path, payload_path, email_path = _paths(run_id, out_dir)
    payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    email_path.write_text(json.dumps({"subject": subject, "body": body}, indent=2, ensure_ascii=False), encoding="utf-8")
    save_record(run_id, out_dir, {"run_id": run_id, "status": "pending", "payload_sha256": payload_hash(payload, subject, body)})
    return {"approval": approval_path, "payload": payload_path, "run_email": email_path}


def load_run(run_id, out_dir):
    _, payload_path, email_path = _paths(run_id, out_dir)
    if not payload_path.exists() or not email_path.exists():
        raise ApprovalError(f"No saved content for run {run_id} in {out_dir}")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    email = json.loads(email_path.read_text(encoding="utf-8"))
    return payload, email["subject"], email["body"]


def current_hash(run_id, out_dir):
    return payload_hash(*load_run(run_id, out_dir))


def load_record(run_id, out_dir):
    path = _paths(run_id, out_dir)[0]
    if not path.exists():
        raise ApprovalError(f"No approval record for run {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_record(run_id, out_dir, record):
    _paths(run_id, out_dir)[0].write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def _decide(run_id, approver, out_dir, status, **extra):
    record = load_record(run_id, out_dir)
    if record["status"] != "pending":
        raise ApprovalError(f"Run {run_id} is already {record['status']}")
    if not approver.strip():
        raise ApprovalError("Approver name is required")
    record.update(status=status, approver=approver.strip(), decided_at=_now(), **extra)
    save_record(run_id, out_dir, record)
    return record


def approve(run_id, approver, out_dir):
    """Record approval of the content as it is on disk right now (the hash pins it)."""
    return _decide(run_id, approver, out_dir, "approved", payload_sha256=current_hash(run_id, out_dir))


def reject(run_id, approver, out_dir, reason=""):
    return _decide(run_id, approver, out_dir, "rejected", reason=reason)


def assert_publishable(run_id, out_dir):
    """The guard: only approved, unchanged content may reach the MCP server."""
    record = load_record(run_id, out_dir)
    if record["status"] != "approved":
        raise ApprovalError(f"Run {run_id} is {record['status']}, not approved")
    if current_hash(run_id, out_dir) != record["payload_sha256"]:
        raise ApprovalError(f"Run {run_id}: content changed after approval; publishing blocked")
    return record


def record_tool_result(run_id, out_dir, tool, result):
    record = load_record(run_id, out_dir)
    record.setdefault("tool_results", {})[tool] = {**result, "at": _now()}
    save_record(run_id, out_dir, record)


def mark_published(run_id, out_dir):
    record = load_record(run_id, out_dir)
    record.update(published=True, published_at=_now())
    save_record(run_id, out_dir, record)
    return record


def format_preview(run_id, payload, subject, body):
    """Everything the approver is approving, exactly as it will be written (PRD R4.1)."""
    return (
        "=== GOOGLE DOC: this text is appended to the end of the Doc ===" + render_doc_entry(run_id, payload) + "\n"
        "=== GMAIL DRAFT (a draft only, never sent) ===\n"
        f"Subject: {subject}\n\n{body}"
    )
