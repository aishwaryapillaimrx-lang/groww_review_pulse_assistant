"""Streamlit app: run analysis -> review pulse and explainer -> approve or reject -> publish.

    streamlit run app.py

Writes happen only through pulse.mcp_publish.publish, which checks the stored approval first.
"""
import asyncio
import json
import os
from datetime import date

import pandas as pd
import streamlit as st

from pulse import access, approval
from pulse.generate import generate_run, render_support_snippet
from pulse.mcp_publish import PublishError, publish
from pulse.themes import top_themes
from pulse.validate import PULSE_WORD_LIMIT, word_count
from run_pipeline import OUT, REGISTRY, analyse

st.set_page_config(page_title="Groww Review Pulse", layout="wide")

if access.misconfigured():
    st.error("APP_PASSWORD is not set on this host, so the app refuses to start.")
    st.stop()
if access.password_required() and not st.session_state.get("authed"):
    st.title("Groww Review Pulse & Fee Clarifier")
    entered = st.text_input("Password", type="password")
    if entered:
        if access.password_ok(entered):
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()

access.seed_if_missing()  # fresh host: copy the committed, masked data into data/ and outputs/

st.title("Groww Review Pulse & Fee Clarifier")
st.caption("Reviews → themes → weekly pulse + fee explainer → human approval → Google Doc + Gmail draft")

csv_path = st.sidebar.text_input("Reviews CSV", "data/reviews.csv")
refresh = st.sidebar.checkbox("Re-run the model on all reviews (spends API credit)", value=False)
st.sidebar.write("Without this, cached labels in `outputs/` are reused (free).")

# ---------------------------------------------------------------- 1. analysis
st.header("1. Review analysis")
if st.button("Run analysis", type="primary"):
    with st.spinner("Analysing reviews..." + (" (calling the model, a few minutes)" if refresh else "")):
        st.session_state.analysis = analyse(csv_path, refresh)
        st.session_state.pop("run", None)

if "analysis" not in st.session_state:
    st.info("Click **Run analysis** to start.")
    st.stop()

labelled, ranked, fee_issue, quotes, stats = st.session_state.analysis
top = top_themes(ranked)
names = {t["id"]: t["name"] for t in ranked}

c1, c2, c3, c4 = st.columns(4)
c1.metric("Reviews analysed", f"{len(labelled):,}")
c2.metric("From", labelled["date"].min().date().isoformat())
c3.metric("To", labelled["date"].max().date().isoformat())
c4.metric("Dropped (short/dupe/non-English)", f"{stats['too_short'] + stats['duplicate'] + stats['non_english']:,}")

st.subheader("Themes")
top_ids = {t["id"] for t in top}
st.dataframe(
    pd.DataFrame(
        {
            "Theme": [t["name"] for t in ranked],
            "Reviews": [t["count"] for t in ranked],
            "Share": [f"{t['share'] * 100:.1f}%" for t in ranked],
            "Avg rating": [t["avg_rating"] for t in ranked],
            "In top 3": ["yes" if t["id"] in top_ids else "" for t in ranked],
        }
    ),
    hide_index=True,
    width="stretch",
)
st.caption("'Other / praise' is ranked but excluded from the top 3.")

st.subheader("Quotes (verbatim from the reviews)")
for q in quotes:
    st.markdown(f"> {q['quote']}  \n— *{names[q['theme_id']]}, {q['rating']}/5*")

st.subheader("Identified fee issue")
f1, f2, f3 = st.columns(3)
f1.metric("Fee", fee_issue["label"])
f2.metric("Mentions (code and model agree)", fee_issue["mentions"])
f3.metric("Other / unspecified fee complaints", fee_issue["other_fee_mentions"])
if fee_issue["weak_signal"]:
    st.warning("Weak signal: fewer than 3 reviews mention a fee.")
for e in fee_issue["examples"]:
    st.markdown(f"> {e['text']}  \n— *rated {e['rating']}/5*")

# ------------------------------------------------- 2. pulse and fee explainer
st.header("2. Weekly pulse and fee explainer")
if st.button("Generate pulse and explainer"):
    from pulse.llm import LLM

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    llm = LLM()
    with st.spinner("Writing and validating..."):
        try:
            st.session_state.run = generate_run(
                labelled, top, quotes, names, fee_issue, registry, llm,
                date.today().isoformat(), int(os.getenv("REVIEW_WEEKS", 12)),
            )
            st.session_state.run_cost = llm.cost_usd()
        except Exception as err:  # validation failures are shown, not hidden
            st.error(f"Generation failed: {err}")

run = st.session_state.get("run")
with st.expander("Open an existing run by ID"):
    existing = st.text_input("Run ID")
    if st.button("Open") and existing:
        try:
            payload, subject, body = approval.load_run(existing.strip(), OUT)
            st.session_state.run = run = {"run_id": existing.strip(), "payload": payload, "subject": subject, "body": body,
                                          "pulse": payload["weekly_pulse"], "explainer": None}
        except approval.ApprovalError as err:
            st.error(str(err))

if not run:
    st.info("Click **Generate pulse and explainer**.")
    st.stop()

words = word_count(run["pulse"])
p1, p2 = st.columns([3, 1])
with p1:
    st.subheader("Weekly pulse")
    st.markdown(run["pulse"])
with p2:
    st.metric("Pulse words", f"{words} / {PULSE_WORD_LIMIT}")
    if "run_cost" in st.session_state:
        st.metric("Model cost", f"${st.session_state.run_cost:.3f}")

st.subheader("Fee explainer (for support)")
payload = run["payload"]
for b in payload["explanation_bullets"]:
    st.markdown(f"- {b}")
for u in payload["source_links"]:
    st.markdown(f"- [{u}]({u})")
st.caption(f"Last checked: {json.loads(REGISTRY.read_text(encoding='utf-8'))['last_checked']}")

# ------------------------------------------------------------- 3. approval
st.header("3. Approval")
run_id = run["run_id"]
st.write(f"Run ID: `{run_id}`")
preview = approval.format_preview(run_id, *approval.load_run(run_id, OUT))
st.text_area("Exactly what will be written (Google Doc entry and Gmail draft)", preview, height=320, disabled=True)

record = approval.load_record(run_id, OUT)


def do_publish():
    try:
        asyncio.run(publish(run_id, OUT))
        st.success("Published to the Google Doc and Gmail drafts.")
    except (PublishError, approval.ApprovalError) as err:
        st.error(f"Publish failed: {err}. Click **Retry publish**; finished steps are not repeated.")


if record["status"] == "pending":
    approver = st.text_input("Your name")
    a, r = st.columns(2)
    if a.button("Approve & publish", type="primary"):
        if not approver.strip():
            st.error("Enter your name first.")
        else:
            approval.approve(run_id, approver, OUT)
            do_publish()
    if r.button("Reject"):
        if not approver.strip():
            st.error("Enter your name first.")
        else:
            approval.reject(run_id, approver, OUT, "Rejected in the app")
            st.warning("Rejected. Nothing was written to Google Docs or Gmail.")
elif record["status"] == "approved" and not record.get("published"):
    st.warning(f"Approved by {record['approver']} but not fully published.")
    if st.button("Retry publish"):
        do_publish()

record = approval.load_record(run_id, OUT)
st.write(f"**Status:** {record['status']}" + (" · published" if record.get("published") else ""))
if record["status"] == "rejected":
    st.info(f"Rejected by {record.get('approver')}. Nothing was written.")
if record["status"] != "pending":
    with st.expander("Audit record"):
        st.json(record)
