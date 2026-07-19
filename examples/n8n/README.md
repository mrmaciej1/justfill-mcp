# JustFill × n8n — PDF form filling

Two import-ready workflows (n8n → Workflows → Import from File). Both send the
file through a one-time capability URL (`request_file_upload` → `post_url`, no
base64) and call the hosted MCP endpoint (`https://justfill.app/api/mcp`) as
plain JSON-RPC over HTTP Request nodes — no MCP client library needed. The
result is a no-auth download link (valid ~24 h).

| Workflow | When to use | Needs |
|----------|-------------|-------|
| **fill-pdf-workflow.json** (deterministic) | Recurring / high-stakes forms you fill often | A saved template; one JustFill key |
| **fill-pdf-ai-vision.json** (AI vision) | Any form, one-off, no template | JustFill key + an OpenRouter key |

## 1. Deterministic (fill-pdf-workflow.json)

PDF + JSON data in → filled PDF out. **No AI model** — data maps to form
fields by name using a saved JustFill template, so every run is deterministic
and reviewable.

```
Form (PDF + JSON) → mint upload slot → POST binary → open_pdf
  → map values by field name → fill_pdf → redirect to the filled PDF
```

## 2. AI vision (fill-pdf-ai-vision.json)

For forms you have **not** templated. The workflow renders every page that has
detected fields. One vision pass maps the supplied data to labelled boxes; a
second, independent vision pass reviews images rendered with those exact
values before the PDF can be produced.

```
Form (PDF + JSON) → upload → open_pdf
  → render_preview for every detected page
  → mapping vision model returns field_id→value
  → render_filled_preview for every page receiving a value
  → independent vision review → explicit approval gate
  → fill_pdf with the unchanged values → redirect
```

Runs through **OpenRouter** (one key, many models) — set the model in Config.
Default is `google/gemini-3.1-flash-lite`. If you change provider, update both
model HTTP nodes: **Mapping vision model** and **Review vision model**.

The mapping prompt includes every labelled page, field id, page number, type,
and detector **confidence**. The reviewer receives both the labelled source
and the filled image for each used page plus the proposed mapping. Invalid JSON,
invented field ids, missing images, inconsistent values, a negative review, or
an approval containing issues all stop the run before `fill_pdf`.

**Model reliability varies** — on a dense scoring table we measured: correct
with `google/gemini-3.1-pro-preview`, `qwen/qwen3-vl-32b-instruct`,
`x-ai/grok-4.20` (even without the confidence prompt), and
`google/gemini-3.1-flash-lite` (with it); weaker/older tiers (gpt-4o-mini,
mistral-medium) still mis-map. For tough forms use a top-tier vision model.

**Trade-off:** works on an unfamiliar form without a template, but costs two
model calls per run. Automated visual review lowers the risk of silent errors;
it does not make probabilistic mapping equivalent to human approval. Treat it
as best-effort and manually review high-impact output. Put both keys (JustFill
+ OpenRouter) into **Config**.

## Setup (2 minutes)

1. **API key**: justfill.app → Account → API keys → create → paste into the
   **Config** node. For production move it to an n8n credential or env var —
   the Config node stores it in plain text.
2. **Deterministic workflow only — template**: open your PDF once with any MCP
   agent (Claude, Cursor, `uvx justfill-mcp`), review the detected boxes, give
   the fields semantic names (`age_score`, `total_amount`…) and `save_template`.
   Manage templates at justfill.app/dashboard?tab=templates.
3. **AI workflow only — model key**: create an OpenRouter key, paste it into
   Config, and keep the same vision-capable model in both model calls.
4. Run the form: upload the PDF, paste values as JSON keyed by field name:
   `{"age_score": "1", "bp_score": "1", "total_score": "5"}`.
   Deterministic key matching is case- and punctuation-insensitive; the AI
   variant may also accept descriptive source keys that differ from field names.

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
- `output_mode` in the result is `clean` or `watermarked`; make that status
  visible before delivering the document.
- The upload slot is single-use and expires after 30 min; the workflow mints
  a fresh one per run.
- The AI workflow renders every page containing a detected field and every page
  that receives a proposed value. Do not bypass **Require approval** when
  adapting the workflow.
- n8n gotcha: object literals inside `{{ }}` expressions that contain `}}`
  (e.g. `arguments: {}}`) break the expression parser — this workflow builds
  the JSON-RPC bodies in Code nodes and only `JSON.stringify(...)` in
  expressions. Keep that pattern if you extend it.

Verified end-to-end on n8n 2.28.6 against production.
