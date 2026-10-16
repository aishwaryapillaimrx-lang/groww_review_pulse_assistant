"""Approval record (implementation.md §6). Step 6 creates it as pending; Step 7 adds approve/reject/guard."""
import hashlib
import json
from pathlib import Path


def payload_hash(payload, subject, body):
    """Hash of exactly what will be written: the doc payload plus the email subject and body."""
    canonical = json.dumps({"payload": payload, "subject": subject, "body": body}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_pending(run_id, digest, out_dir):
    path = Path(out_dir) / f"approval_{run_id}.json"
    path.write_text(json.dumps({"run_id": run_id, "status": "pending", "payload_sha256": digest}, indent=2), encoding="utf-8")
    return path
