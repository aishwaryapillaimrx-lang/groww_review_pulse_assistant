# n8n setup (Step 8)

Goal: an active n8n workflow that exposes two MCP tools, `append_to_doc` and `create_email_draft`. There is no send node, so email can only be drafted.

## 1. Create the target Google Doc
- Make a new Google Doc (for testing, a scratch doc named e.g. "Groww Pulse log (test)").
- Copy its ID from the URL: `docs.google.com/document/d/<THIS PART>/edit`.

## 2. Create the bearer token (keeps the MCP URL from being open to the world)
- Generate a token in your terminal (do not paste it into chat):
  `.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"`
- In n8n: **Credentials > Create credential > Bearer Auth**, name it `Groww MCP bearer`, paste the token, save.
- Put the same token in your local `.env` as `N8N_MCP_TOKEN=`.

## 3. Import the workflow
- n8n: **Workflows > Create workflow > ⋯ menu > Import from file** and pick `n8n/groww_pulse_mcp.json`.
- Tip: keep your own copy with your real Doc ID and email as `n8n/groww_pulse_mcp.local.json` (git-ignored, so those never reach GitHub). The committed file keeps placeholders.

## 4. Fix up the three nodes (credentials cannot be exported, so set them here)
| Node | Set |
|---|---|
| **MCP Server Trigger** | Credential: `Groww MCP bearer`. Leave Path as `groww-pulse`. |
| **append_to_doc** | Credential: your Google account (Google Docs OAuth2; on n8n Cloud use "Sign in with Google"). Replace `REPLACE_WITH_YOUR_GOOGLE_DOC_ID_OR_URL` with the Doc ID from step 1. |
| **create_email_draft** | Credential: your Google account (Gmail OAuth2). Under Options, replace `REPLACE_WITH_YOUR_EMAIL` in "Send To" with your own address. |

If n8n shows a node as "unknown" or a field is missing after import, open it and re-pick Resource/Operation (Docs: Document > Update; Gmail: Draft > Create), then re-enter the fields from the table. The expressions use `$fromAI('entry_text')`, `$fromAI('subject')` and `$fromAI('body')`.

## 5. Activate and copy the URL
- Toggle the workflow **Active** (top right).
- Open **MCP Server Trigger** and copy the **Production URL**. On n8n Cloud it ends in `/mcp/groww-pulse/sse` (the older SSE transport); our client detects the `/sse` suffix and uses SSE automatically. Do not use the Test URL: it only works while the editor is open and listening.
- Put it in `.env` as `N8N_MCP_URL=`.

## 6. Check the connection (writes nothing)
```
.venv\Scripts\python publish.py --list-tools
```
Expected: `append_to_doc` with input `entry_text`, and `create_email_draft` with inputs `subject` and `body`. If the names or argument names differ, tell Claude what it printed.
