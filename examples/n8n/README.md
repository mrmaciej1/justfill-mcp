# JustFill × n8n — deterministic PDF form filling

Import-ready workflow: **fill-pdf-workflow.json** (n8n → Workflows → Import
from File).

## What it does

PDF + JSON data in → filled PDF out. **No AI model, no credentials beyond one
API key** — data maps to form fields by name using a saved JustFill template,
so every run is deterministic and reviewable.

```
Form (PDF + JSON) → mint upload slot → POST binary → open_pdf
  → map values by field name → fill_pdf → redirect to the filled PDF
```

Every JustFill call is a plain HTTP Request node against the hosted MCP
endpoint (`https://justfill.app/api/mcp`) — stateless JSON-RPC, so no MCP
client library is needed. The file travels through a one-time capability URL
(`request_file_upload` → `post_url`), so there's no base64 and no size games;
the result comes back as a no-auth download link (valid ~24 h).

## Setup (2 minutes)

1. **API key**: justfill.app → Account → API keys → create → paste into the
   **Config** node. For production move it to an n8n credential or env var —
   the Config node stores it in plain text.
2. **Template**: open your PDF once with any MCP agent (Claude, Cursor,
   `uvx justfill-mcp`), review the detected boxes, give the fields semantic
   names (`age_score`, `total_amount`…) and `save_template`. Manage templates
   at justfill.app/dashboard?tab=templates.
3. Run the form: upload the PDF, paste values as JSON keyed by field name:
   `{"age_score": "1", "bp_score": "1", "total_score": "5"}`.
   Key matching is case- and punctuation-insensitive.

## Adapting for production

- Swap the Form Trigger for whatever feeds you documents: Gmail attachment,
  Google Drive, Dropbox, a Webhook from your app, Airtable…
- Replace the final redirect with your delivery: email the link, upload the
  PDF to Drive (an HTTP Request GET on `download_url` returns the binary),
  post to Slack, write back to Airtable.
- Batch fills: loop the mapping + fill over rows from a spreadsheet — one
  `open_pdf` per document, one `fill_pdf` per row.

## Notes

- Checkbox fields: send `"yes"` / `"x"` / `"true"` to tick.
- `output_mode` in the result is `clean` or `watermarked` (free allowance
  used up — paid plans at justfill.app/billing remove the watermark).
- The upload slot is single-use and expires after 30 min; the workflow mints
  a fresh one per run.
- n8n gotcha: object literals inside `{{ }}` expressions that contain `}}`
  (e.g. `arguments: {}}`) break the expression parser — this workflow builds
  the JSON-RPC bodies in Code nodes and only `JSON.stringify(...)` in
  expressions. Keep that pattern if you extend it.

Verified end-to-end on n8n 2.28.6 against production.
