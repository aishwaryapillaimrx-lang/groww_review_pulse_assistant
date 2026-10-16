# Implementation Plan — Groww Review Pulse & Fee Clarifier

Companion to `PRD.md` (Milestone 5, builds on Milestone 4). This is the build plan, not the build.

---

## 1. Architecture

```
 [Your machine]                   [Python app]                                 [n8n]
 scrape_reviews.py ──► reviews.csv ──► 1. Ingest & clean
                                      2. Theme discovery (Claude)
                                      3. Theme assignment (Claude, batched)
                                      4. Rank top 3 + pick quotes (code)
                                      5. Fee detection (keywords + Claude)
                                      6. Generate pulse (Claude) ── validate ≤250 words
                                      7. Generate explainer (Claude + registry) ── validate
                                      8. Build payload + email ──► outputs/*.md, payload.json
                                                   │
                                      9. APPROVAL GATE (Streamlit button / CLI prompt)
                                                   │ approve only
                                     10. MCP client ──(streamable HTTP + bearer)──► MCP Server Trigger
                                                                                     ├─ append_to_doc      → Google Docs (insert text at end)
                                                                                     └─ create_email_draft → Gmail (create draft, never send)
                                     11. Audit log ◄── tool results (doc id, draft id)
```

**Why this split:** the model does judgement work (grouping themes, wording). Code does everything that has to be exactly right: counts, quotes, links, word limits, numbers. n8n holds the Google credentials and is the only thing that can write, and the Python side reaches it only through MCP after approval.

## 2. Tech stack

| Layer | Choice | Note |
|---|---|---|
| Scraping | `google-play-scraper` (Python) | Runs on your machine |
| Data | `pandas` | |
| LLM | Claude API via `anthropic` SDK, structured outputs (`output_config.format` JSON schema) for JSON — Sonnet 5.5 rejects forced `tool_choice` | Model set by `ANTHROPIC_MODEL` env var |
| Approval UI | Streamlit (`app.py`) + CLI fallback (`publish.py`) | |
| MCP client | `mcp` Python SDK, streamable HTTP transport | |
| MCP server | n8n **MCP Server Trigger** with **Google Docs Tool** and **Gmail Tool** nodes | Bearer auth |
| Tests | `pytest` | Offline, with stub LLM and in-memory stub MCP session |

## 3. Repository layout

```
groww-pulse/
├── PRD.md
├── implementation.md
├── README.md
├── SOURCES.md
├── requirements.txt
├── .env.example
├── config/
│   └── fee_registry.json        # verified fee facts + official links (copy from Appendix A)
├── scripts/
│   └── scrape_reviews.py        # Play Store scraper (spec in §4 and Appendix B)
├── pulse/
│   ├── ingest.py                # load, window, clean, dedupe
│   ├── llm.py                   # Claude client + offline stub
│   ├── themes.py                # discovery + assignment + ranking
│   ├── quotes.py                # quote selection, PII masking, verbatim check
│   ├── fees.py                  # fee detection
│   ├── generate.py              # pulse + explainer + email body
│   ├── validate.py              # word/bullet/number/link checks
│   ├── approval.py              # approval record (pending/approved/rejected)
│   └── mcp_publish.py           # MCP client calls
├── run_pipeline.py              # steps 1–8, writes outputs/
├── publish.py                   # step 9–11, CLI approval
├── app.py                       # Streamlit: run, review, approve, publish
├── n8n/
│   └── groww_pulse_mcp.json     # importable n8n workflow
├── data/
│   ├── reviews.csv              # full scrape (git-ignored)
│   └── reviews_sample.csv       # 50-row sample for submission
├── outputs/                     # generated per run
└── tests/
```

## 4. Data contract

`reviews.csv`

| Column | Type | Note |
|---|---|---|
| review_id | str | Play Store ID, dedupe key |
| date | ISO date | review date |
| rating | int 1–5 | |
| text | str | newlines flattened |
| thumbs_up | int | used to rank quotes |
| app_version | str | may be blank |
| source | str | `google_play` |

## 5. Pipeline stages

### 5.1 Ingest (`ingest.py`)
- Read CSV, parse dates, keep rows inside `[max_date − weeks, max_date]` (default 12 weeks).
- Drop text under 20 characters, exact duplicates, and non-English rows (simple script-ratio check).
- Cap at `MAX_REVIEWS` (default 1,500, newest first) to bound cost. Record how many were dropped and why.

### 5.2 Theme discovery (`themes.py`)
- Sample up to 300 reviews: half 1–2 star, half 3–5 star, longer reviews preferred.
- One Claude call returning JSON in this schema (`propose_themes`):
  ```json
  {"themes": [{"id": "T1", "name": "string ≤5 words", "description": "one line"}]}  // maxItems 5
  ```
- Prompt rules: max 5 themes, mutually exclusive, product-level (e.g. "Withdrawal delays"), one "Other / praise" bucket allowed.

### 5.3 Theme assignment
- Batches of 80 reviews, each sent as `{id, text}`. JSON schema `assign_reviews`:
  ```json
  {"assignments": [{"id": "...", "theme_id": "T1",
                    "fee_key": "dp_charges | exit_load | brokerage | other | none"}]}
  ```
- Validate: every input ID returned once, `theme_id` in the discovered set. Retry the batch once on mismatch; any still missing go to "Other".
- Ranking by code: count, share, average rating per theme. The top 3 are the top 3 **problem** themes: the "Other / praise" bucket is ranked but excluded from the top 3. `fee_key: none` means no fee mentioned.

### 5.4 Quotes (`quotes.py`) — no LLM
- For each top-3 theme: candidates 60–300 characters, prefer rating ≤ 3 for problem themes, sort by `thumbs_up` then recency.
- Mask emails, phone numbers and long digit strings (account/order IDs).
- Check: the unmasked quote is an exact substring of a CSV `text`. Fail the run if not.

### 5.5 Fee detection (`fees.py`)
- Pass A (code, whole-word match): a review matching a fee key's own keywords maps to that key; a review matching only `generic_fee_keywords` maps to `other`; otherwise none.
- Pass B (model): `fee_key` from step 5.3.
- Final fee issue = fee key with the most reviews where A and B agree; fall back to B alone. Winner is chosen among registry fees only (`other` has no explainer). Output `{fee_key, label, mentions, share_of_fee_mentions, examples[3], other_examples[5]}`; examples are 40–300 characters, most-liked first.
- If total fee mentions < 3: `weak_signal = true`, still pick the top one, and the pulse says so.

### 5.6 Weekly pulse (`generate.py`)
- Claude call returning JSON `write_pulse` → `{themes_summary[3], observation, actions[3]}`. The prompt receives only the computed numbers and quotes.
- Code renders the markdown (headings, counts, quotes inserted verbatim).
- `validate.word_count(md) ≤ 250`. On fail, re-ask with "cut to N words" up to 2 times, then fail loudly.

### 5.7 Fee explainer
- Input to Claude: only the `facts` list for the chosen fee key from `fee_registry.json`, plus the review confusion examples (to choose which facts matter).
- JSON schema `write_explainer` → `{bullets: [string], maxItems 6}`.
- Validators:
  - bullet count ≤ 6;
  - every number/₹ amount/percentage in bullets appears in the registry facts text;
  - no advice words ("should", "we recommend", "best");
  - links and `Last checked: <registry.last_checked>` appended by code.

### 5.8 Payload and email
- `outputs/payload_<run_id>.json` matches the PRD doc payload exactly.
- Email subject: `Weekly Product Pulse + Customer Clarification — <date>`.
- Email body: pulse, then a delimited block `--- Support snippet: <fee label> ---` with bullets, links, last-checked line.
- `outputs/approval_<run_id>.json` created with `status: "pending"`.

## 6. Approval gate (where MCP approval happens)

**Streamlit (`app.py`)** — the demo path:
1. "Run analysis" button → stages 5.1–5.8, shows themes table, quotes, fee issue.
2. Preview panel shows the exact doc entry and the exact email draft.
3. Two buttons: **Approve & publish** and **Reject**. Approver name is typed in.
4. Approve → writes `status: approved, approver, ts, payload_sha256` → calls MCP.
5. Reject → writes `status: rejected`, no MCP connection is opened.

**CLI (`publish.py <run_id>`)** — prints the same preview; you must type `APPROVE` exactly.

**Guard in `mcp_publish.py`:** refuses to connect unless the approval file says `approved` and its `payload_sha256` matches the current payload (so nothing can be edited after approval). Stores `published: true` after success to block duplicate appends.

## 7. n8n MCP server

**Workflow `n8n/groww_pulse_mcp.json`:**

| Node | Type | Config |
|---|---|---|
| MCP Server Trigger | `@n8n/n8n-nodes-langchain.mcpTrigger` | Path `groww-pulse`; Bearer auth credential |
| append_to_doc | `n8n-nodes-base.googleDocsTool` | operation `update`; `documentURL` = your Doc ID; `actionsUi.actionFields[0]` = `{action: insert, object: text, insertSegment: body, locationChoice: endOfSegmentLocation, text: {{ $fromAI('entry_text') }}}` |
| create_email_draft | `n8n-nodes-base.gmailTool` | resource `draft`, operation `create`; `subject: {{ $fromAI('subject') }}`, `message: {{ $fromAI('body') }}`, `emailType: text`, options `sendTo` = your email |

[Certain] Parameter names above (`documentURL`, `actionsUi`, `actionFields`, `insertSegment`, `locationChoice`, `subject`, `message`, `emailType`, `sendTo`) are confirmed from the n8n-nodes-base package source, and both nodes are marked usable as tools.
[Likely] The MCP tool name equals the node name, which is why the nodes are named `append_to_doc` and `create_email_draft`. Verify with `list_tools` on first run.
[Likely] `$fromAI` keys become the tool's input arguments, so the Python client passes `entry_text`, `subject`, `body`. Verify with `list_tools`.
No Gmail **send** node exists in the workflow, so sending is impossible from MCP.

**Client call (sketch):**
```python
async with streamablehttp_client(N8N_MCP_URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
    async with ClientSession(r, w) as s:
        await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        assert {"append_to_doc", "create_email_draft"} <= tools
        doc = await s.call_tool("append_to_doc", {"entry_text": render_doc_entry(payload)})
        draft = await s.call_tool("create_email_draft", {"subject": subj, "body": body})
```

**Doc entry format** (appended as plain text, one block per run):
```
==== Weekly Pulse — 2026-10-16 (run abc123) ====
date: ...
top_themes: ...
identified_fee_issue: ...
weekly_pulse:
...
explanation_bullets:
• ...
source_links:
- ...
```

**No mock server in the initial build.** n8n is the only MCP server. Contingency: if `list_tools` doesn't show the expected tool names or arguments, or Google credentials block progress, build a local `FastMCP` stdio server with the same two tools (writing to a local `.md` and `.eml`). `mcp_publish.py` keeps the same interface, so it would be a drop-in. Because there is no fallback on demo day, do one approved run early and keep the Doc and Drafts screenshots plus the n8n execution log as proof.

## 8. Configuration (`.env.example`)

```
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5-5
REVIEW_WEEKS=12
MAX_REVIEWS=1500
N8N_MCP_URL=https://<your-n8n>/mcp/groww-pulse
N8N_MCP_TOKEN=
```

## 9. Testing

| Test | What it proves |
|---|---|
| `test_ingest` | window, dedupe and short-text filters |
| `test_quotes_verbatim` | every quote is a substring of the CSV |
| `test_pulse_word_limit` | validator rejects 251 words |
| `test_explainer_numbers` | a bullet with "₹25" (not in registry) is rejected |
| `test_links_from_registry` | output links equal registry links |
| `test_approval_required` | `mcp_publish` raises when status is pending or rejected |
| `test_payload_tamper` | edited payload after approval is blocked by hash check |
| `test_idempotent_publish` | second publish of same run does nothing |
| `test_end_to_end_stub` | stub LLM + in-memory stub MCP session receive the expected `append_to_doc` and `create_email_draft` calls |

All tests run offline using a stub LLM that returns fixed tool outputs and an in-memory stub of the MCP client session (not a server). The real n8n path is tested by hand. A small **synthetic** fixture CSV is used only for tests and is labelled as synthetic; it is never submitted as the reviews sample.

## 10. Build order

| # | Step | Do | Done when |
|---|---|---|---|
| 0 | Setup | Create venv; `requirements.txt` (`anthropic`, `pandas`, `mcp`, `streamlit`, `google-play-scraper`, `pytest`); `.env` from `.env.example`; `.gitignore` for `.env`, `data/reviews.csv`, `outputs/` | `pip install -r requirements.txt` works |
| 1 | Registry | Create `config/fee_registry.json` from Appendix A; re-check facts and `last_checked` | JSON loads; 3 fee keys, 2 links each |
| 2 | Scraper | Write `scripts/scrape_reviews.py` (Appendix B) | `--help` runs |
| 3 | Data (you) | Run scraper on your machine → `data/reviews.csv` | Row count and date range printed; no `userName` column |
| 4 | No-LLM core | `ingest.py`, `quotes.py`, `validate.py` + `test_ingest`, `test_quotes_verbatim`, `test_pulse_word_limit`, `test_explainer_numbers`, `test_links_from_registry` | Those tests pass |
| 5 | Analysis | `llm.py`, `themes.py`, `fees.py`; run on real CSV | ≤5 themes, top 3 ranked, fee issue with count; inspect by hand |
| 6 | Generation | `generate.py` + validators → pulse, explainer, payload, email, pending approval file | Pulse ≤250 words; explainer passes validators; `outputs/` populated |
| 7 | Approval + publish | `approval.py`, `mcp_publish.py`, `publish.py` + `test_approval_required`, `test_payload_tamper`, `test_idempotent_publish`, `test_end_to_end_stub` (stub MCP session) | All tests pass offline |
| 8 | n8n (early, riskiest) | Create Google Doc; add Docs + Gmail OAuth credentials; create Bearer credential; import `n8n/groww_pulse_mcp.json`; set Doc ID and your email; activate; run `list_tools` | Both tool names and argument names confirmed. If not, build the contingency mock (§7) |
| 9 | Live run | Run pipeline → approve via CLI → publish through n8n | New entry at end of Doc; draft in Gmail Drafts; Sent empty; n8n execution log and screenshots saved |
| 10 | UI + docs | `app.py` (run, preview, approve/reject, publish); README; SOURCES.md; 50-row sample; record demo | Reject writes nothing; Approve publishes; demo ≤3 min |

## 11. Demo script (≤ 3 min)

| Time | Show |
|---|---|
| 0:00–0:20 | Problem + CSV (row count, date range) |
| 0:20–1:00 | Run analysis: themes table, top 3, quotes, identified fee issue with count |
| 1:00–1:40 | Pulse (word count badge) and explainer (bullets, links, last checked) |
| 1:40–2:10 | Approval screen: click Reject once (nothing written), then Approve |
| 2:10–2:45 | Google Doc with new entry at the bottom; Gmail Drafts with the draft; Sent folder empty |
| 2:45–3:00 | n8n execution log showing the two tool calls |

## 12. Effort estimate

[Guessing] About 1.5–2 working days: half a day for the pipeline and validators, half a day for n8n and Google credentials (usually the slowest part), half a day for the Streamlit app, README and demo.

---

## Appendix A — `config/fee_registry.json` (copy as-is)

Facts checked on 16 Oct 2026 against groww.in/pricing/stocks, Groww Help, groww.in/p/exit-load-in-mutual-funds and investor.sebi.gov.in/exit_load.html. Re-verify before the final run and update `last_checked`.

```json
{
  "_comment": "Curated, human-verified fee facts. The explainer generator may ONLY restate these facts; links are attached from here, never invented by the model. Re-verify and bump last_checked before each weekly run.",
  "last_checked": "2026-10-16",
  "fees": {
    "dp_charges": {
      "label": "DP (Depository Participant) charge on selling shares",
      "keywords": ["dp charge", "dp charges", "dp fee", "depository", "cdsl", "charged on sell", "charge on selling", "sell charge", "selling charge", "charged when i sold", "charged for selling", "20 rs", "rs 20", "rs.20", "₹20", "16.5", "18.5", "per sell", "per stock sold"],
      "facts": [
        "A DP charge applies each time delivery shares are sold (debited) from the Groww demat account. Buying shares does not attract it.",
        "Per sell transaction it is the depository charge of ₹3.50 (₹3.25 for women investors) plus Groww's charge of ₹16.50.",
        "If the sell (debit) value is below ₹100, Groww waives its ₹16.50 part and only the depository charge applies.",
        "18% GST is added on top of DP charges.",
        "DP charges are separate from brokerage. Equity brokerage is ₹20 or 0.1% per executed order, whichever is lower, with a minimum of ₹5.",
        "Intraday and F&O trades have ₹0 DP charges.",
        "Account opening and account maintenance charges (AMC) on Groww are ₹0."
      ],
      "sources": [
        {"title": "Groww — Brokerage Charges & Pricing (Stocks)", "url": "https://groww.in/pricing/stocks"},
        {"title": "Groww Help — How much does Groww charge for stocks?", "url": "https://groww.in/help/stocks%2C-f%26o%2C-ipo-%26-mtf/sx-demat-account/how-much-does-groww-charge-for-stocks-1"}
      ]
    },
    "exit_load": {
      "label": "Exit load on mutual fund redemption",
      "keywords": ["exit load", "exit-load", "exitload", "redemption charge", "charged on redemption", "deducted on redemption", "less amount on redeem", "withdrawal charge", "redeem", "redemption amount"],
      "facts": [
        "Exit load is a fee charged by the mutual fund's AMC (fund house), not by Groww, when units are redeemed before a period set by the scheme.",
        "It is a percentage of the redemption value (NAV × units redeemed) and is deducted from the redemption proceeds, so it does not appear as a separate bill.",
        "Each scheme sets its own exit load in its Scheme Information Document (SID); some schemes have no exit load.",
        "Many equity funds charge around 1% if units are redeemed within the first 12 months, but this varies by fund house.",
        "Each SIP instalment is treated as a fresh purchase, so each instalment's exit-load period runs from its own allotment date.",
        "Example: a ₹27,000 redemption value with a 1% exit load means ₹270 is deducted and ₹26,730 is credited.",
        "Groww charges zero commission on direct mutual funds."
      ],
      "sources": [
        {"title": "Groww — Exit Load in Mutual Funds", "url": "https://groww.in/p/exit-load-in-mutual-funds"},
        {"title": "SEBI Investor Website — Exit Load", "url": "https://investor.sebi.gov.in/exit_load.html"}
      ]
    },
    "brokerage": {
      "label": "Equity brokerage and minimum brokerage",
      "keywords": ["brokerage", "minimum brokerage", "min brokerage", "₹5", "rs 5", "0.1%", "upi mandate", "1% brokerage", "stt", "stamp duty", "statutory"],
      "facts": [
        "Equity brokerage on Groww is ₹20 or 0.1% per executed order, whichever is lower, with a minimum of ₹5.",
        "Orders placed using UPI Mandate balance are charged 1% of the order value as brokerage, with no maximum cap.",
        "STT, stamp duty, exchange transaction charges and SEBI turnover charges are regulatory and statutory charges, not Groww fees.",
        "18% GST applies on brokerage, DP charges and exchange transaction charges.",
        "Account opening and account maintenance charges (AMC) on Groww are ₹0."
      ],
      "sources": [
        {"title": "Groww — Brokerage Charges & Pricing (Stocks)", "url": "https://groww.in/pricing/stocks"},
        {"title": "Groww Help — How much does Groww charge for stocks?", "url": "https://groww.in/help/stocks%2C-f%26o%2C-ipo-%26-mtf/sx-demat-account/how-much-does-groww-charge-for-stocks-1"}
      ]
    }
  },
  "generic_fee_keywords": ["charge", "charges", "charged", "fee", "fees", "deduct", "deducted", "hidden", "cut", "commission", "gst", "penalty", "extra money", "less amount"]
}
```

## Appendix B — Scraper spec (`scripts/scrape_reviews.py`)

- Library: `google-play-scraper`; app ID `com.nextbillion.groww`; `lang="en"`, `country="in"`; `Sort.NEWEST`.
- Page with `continuation_token`, 200 per call, 1 s pause between calls; stop when a review is older than the cutoff (`--weeks`, must be 8–12, default 12) or `--max-reviews` (default 5000) is reached.
- Map each review to the §4 columns: `reviewId→review_id`, `at→date` (ISO date), `score→rating`, `content→text` (newlines flattened), `thumbsUpCount→thumbs_up`, `reviewCreatedVersion→app_version`, `source="google_play"`.
- **Never store `userName`** or profile image.
- Drop empty text, dedupe on `review_id`, write CSV, print row count and date range.
- Run on your own machine: `python scripts/scrape_reviews.py --weeks 12 --out data/reviews.csv`.
