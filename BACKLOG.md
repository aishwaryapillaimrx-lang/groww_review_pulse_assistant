# Backlog — Shelved Enhancements

Source: review of `PRD.md` and `implementation.md` (16 Oct 2026). None of these are applied; the two docs stay as-is for v1. Revisit before or after the build.

## A. Correctness and robustness

| # | Item | Why it matters |
|---|---|---|
| A1 | Use word-boundary regex for fee keywords. Drop weak terms (`redeem`, `redemption amount`, `₹5`, `rs 5`, `cut`). | Broad terms inflate mention counts and can distort the fee pick. |
| A2 | Review DP-charges keywords (`20 rs`, `16.5`, `18.5`, etc.) and the "strongest candidate" wording in PRD §6. | Looks tuned to a pre-chosen answer, which conflicts with G3 and R3.1. |
| A3 | Define number normalisation for the explainer validator (`₹3.50` vs `3.5`, `1%` vs `1 percent`, derived sums like ₹3.50 + ₹16.50 = ₹20). | Validator would over-reject valid bullets. |
| A4 | Label the exit-load ₹27,000 → ₹270 example explicitly as illustrative. | Facts-only requirement (R3.3). |
| A5 | Staleness check on registry `last_checked` (warn or block if older than about 7 days). | A static date would be shown as "verified" on later runs. |
| A6 ✅ done in Step 7 | Hash the exact rendered doc entry and email subject/body, not only the payload. | Template changes could bypass the approval hash. |
| A7 ✅ done in Step 7 | Per-tool completion flags for publish (doc done, draft done). Put the run ID in the doc entry. | Partial failure plus retry would append the doc entry twice. |
| A8 | Note that n8n tools take no idempotency key, so dedupe is client-side only. | A lost response could still cause a duplicate. |
| A9 | Extend PII masking to PAN-like strings and UPI IDs. | Current masking covers only emails, phones and long digits. |
| A10 ✅ done in Step 7 | Prefix each doc entry with `\n\n`. | `endOfSegmentLocation` appends with no separator. |

## B. Spec ambiguities to resolve

- B1. Does the "Other / praise" bucket count toward the 5-theme limit? Can it appear in the top 3?
- B2. Quote fallback when a top theme has no rating ≤3 reviews (for example, a praise theme).
- B3. Does the 250-word pulse limit include headings and quotes? Consider capping quote length at about 120 characters.
- B4. Review window anchor: `max_date` versus the run date (a stale CSV looks current).
- B5. Fee tie-break rule, and behaviour when "other" is the top fee (the registry has no entry for it).
- B6. Align PRD §7 ("most-mentioned fee type") with the A+B agreement rule in §5.5.
- B7. Resolve the Hindi/Hinglish open question against the ingest non-English filter. Latin-script Hinglish passes the script-ratio check.

## C. Nice to have

- C1. Grounding check for the pulse's "key observation" and actions (R2.3 covers numbers only).
- C2. Re-estimate effort (1.5–2 days looks optimistic given OAuth, n8n verification and validator edge cases).
- C4. Contingency: local mock MCP server (`mock_mcp_server.py`, `FastMCP` stdio, same two tools, writes `.md` and `.eml`, selected via `MCP_MODE=mock`). Build only if n8n can't be made to work (wrong tool names or arguments, or Google credentials blocked).
- C3. Housekeeping: confirm the PRD owner name, and align the repo folder name with the `groww-pulse/` layout.
- C5. Rich formatting: HTML email body (Gmail node email type HTML + markdown-to-HTML) and styled Doc headings/bold via Docs batchUpdate. v1 sends clean plain text (pulse/render.py).
- C6. Guard against a second approved run for the same date (a new run ID currently appends a second Doc entry and a second draft).
