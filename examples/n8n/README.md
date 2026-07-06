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

For forms you have **not** templated. A vision model looks at the rendered
form — with each detected box labelled by its field id — and decides which
value goes in which field.

```
Form (PDF + JSON) → upload → open_pdf → render_preview (labelled image)
  → vision model returns field_id→value → fill_pdf → redirect
```

Runs through **OpenRouter** (one key, many models) — set the model in Config.
Default is `google/gemini-3.1-flash-lite` (cheap, and maps correctly with the
prompt below). To use the OpenAI API directly instead, point the model node at
`https://api.openai.com/v1/chat/completions` and set the model to `gpt-4o-mini`.

The model is used for ONE decision (mapping); everything else is deterministic
HTTP. The prompt hands the model each box's detector **confidence** and warns
it that low-confidence boxes are often false positives (and that some real
fields may be missing) — this is what stops it filling spurious boxes (e.g. a
stray box the detector drew over a printed digit). No box-pruning heuristic is
used; the model reasons over the confidence signal.

**Model reliability varies** — on a dense scoring table we measured: correct
with `google/gemini-3.1-pro-preview`, `qwen/qwen3-vl-32b-instruct`,
`x-ai/grok-4.20` (even without the confidence prompt), and
`google/gemini-3.1-flash-lite` (with it); weaker/older tiers (gpt-4o-mini,
mistral-medium) still mis-map. For tough forms use a top-tier vision model.

**Trade-off:** works on any form with no template, but costs one model call
per run and there is no human review — treat it as best-effort, not for
high-stakes forms. Put both keys (JustFill + OpenRouter) into **Config**.

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
