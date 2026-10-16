"""CLI approval gate and publish (implementation.md §6).

    python publish.py <run_id>        preview, then type APPROVE or REJECT
    python publish.py --list-tools    show the tools the n8n MCP server offers (writes nothing)
"""
import argparse
import asyncio
import json
import sys

from pulse import approval
from pulse.mcp_publish import PublishError, describe_tools, publish


def show_result(record):
    for tool, result in record.get("tool_results", {}).items():
        print(f"  {tool}: {result['content']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--out", default="outputs")
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args()

    if args.list_tools:
        try:
            tools = asyncio.run(describe_tools())
        except PublishError as err:
            sys.exit(str(err))
        for name, schema in tools:
            print(f"{name}\n  input: {json.dumps(schema.get('properties', {}))}")
        return
    if not args.run_id:
        parser.error("run_id is required")

    record = approval.load_record(args.run_id, args.out)
    status = record["status"]

    if status == "rejected":
        sys.exit(f"Run {args.run_id} was rejected by {record.get('approver')}; nothing will be written.")
    if status == "pending":
        print(approval.format_preview(args.run_id, *approval.load_run(args.run_id, args.out)))
        print("\nType APPROVE to publish, REJECT to reject, anything else to leave it pending.")
        answer = input("> ").strip()
        if answer == "REJECT":
            name = input("Your name: ")
            reason = input("Reason (optional): ")
            approval.reject(args.run_id, name, args.out, reason)
            print("Rejected. Nothing was written to Google Docs or Gmail.")
            return
        if answer != "APPROVE":
            print("Left pending. Nothing was written.")
            return
        approval.approve(args.run_id, input("Your name: "), args.out)
        print("Approved.")
    else:
        print(f"Run {args.run_id} is already approved; continuing to publish.")

    try:
        record = asyncio.run(publish(args.run_id, args.out))
    except (PublishError, approval.ApprovalError) as err:
        sys.exit(f"Publish failed: {err}\nRe-run this command to resume; finished steps are not repeated.")
    print(f"Published run {args.run_id}:")
    show_result(record)


if __name__ == "__main__":
    main()
