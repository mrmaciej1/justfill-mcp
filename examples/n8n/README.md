# JustFill × n8n — PDF form filling

Two import-ready workflows (n8n → Workflows → Import from File). Both send the
file through a one-time capability URL (`request_file_upload` → `post_url`, no
base64) and call the hosted MCP endpoint (`https://justfill.app/api/mcp`) as
plain JSON-RPC over HTTP Request nodes — no MCP client library needed. The
result is a no-auth download link (valid ~24 h).

| Workflow | When to use | Needs |
|----------|-------------|-------|
| **fill-pdf-workflow.json** (deterministic) | Recurring / high-stakes forms you fill often | A saved template; one JustFill key |
| **fill-pdf-ai-vision.json** (AI vision) | Any form, one-off, no template | JustFill key + a Gemini API key |

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
  → mapping vision model returns source-key→field-id pairs
  → workflow copies the original values deterministically
  → render_filled_preview for every page receiving a value
  → independent vision review → explicit approval gate
  → fill_pdf with the unchanged values → redirect
```

Calls the **Gemini API directly** — set the model in Config. The default is the
stable, vision-capable `gemini-3.5-flash`. Both model calls use Gemini's
structured JSON output, ultra-high image resolution for dense field labels,
and disabled interaction storage. The mapping pass uses high reasoning because
dense forms need reliable spatial association; the independent review pass uses
low reasoning to keep the second call inexpensive.

The mapping prompt includes every labelled page, field id, box coordinates,
page number, type, and detector **confidence**. Gemini returns only source keys
and field ids; the workflow rejects unknown or duplicate identifiers and then
copies values from the untouched input JSON. The reviewer receives both the labelled source
and the filled image for each used page plus the proposed source-key-to-field
mapping, so it verifies the user's intended label instead of guessing intent
from a value such as a company name. Invalid JSON,
invented field ids, missing images, inconsistent values, a negative review, or
an approval containing issues all stop the run before `fill_pdf`.

**Model reliability varies** — the default was selected for low-cost vision
mapping and should still be treated as probabilistic. For recurring or
high-impact forms, use the deterministic template workflow instead.

**Trade-off:** works on an unfamiliar form without a template, but costs two
model calls per run. Automated visual review lowers the risk of silent errors;
it does not make probabilistic mapping equivalent to human approval. Treat it
as best-effort and manually review high-impact output. Put both keys (JustFill
+ Gemini) into **Config**.

## Setup (2 minutes)

1. **API key**: justfill.app → Account → API keys → create → paste into the
   **Config** node. For production move it to an n8n credential or env var —
   the Config node stores it in plain text.
2. **Deterministic workflow only — template**: open your PDF once with any MCP
   agent (Claude, Cursor, `uvx justfill-mcp`), review the detected boxes, give
   the fields semantic names (`age_score`, `total_amount`…) and `save_template`.
   Manage templates at justfill.app/dashboard?tab=templates.
3. **AI workflow only — model key**: create a Gemini API key in Google AI
   Studio, paste it into Config, and keep the same vision-capable model in both
   model calls.
4. Run the form: upload the PDF, paste values as JSON keyed by field name:
   `{"age_score": "1", "bp_score": "1", "total_score": "5"}`.
   Deterministic key matching is case- and punctuation-insensitive; the AI
   variant may also accept descriptive source keys that differ from field names.

## Reviewer-safe deterministic smoke test

Use [`reviewer-sample-supplier-intake.pdf`](reviewer-sample-supplier-intake.pdf)
with this entirely synthetic payload:

```json
{
  "company_name": "Northwind LLC",
  "vendor_reference": "V-1042",
  "remittance_email": "ap@northwind.example"
}
```

The sample contains three named AcroForm fields, so a reviewer can verify the
workflow without ML or customer data. For an exact recurring business PDF,
review and save its layout once as described above; subsequent opens use the
saved template deterministically.

The latest production audit evidence is in [`assets/`](assets/): the complete
green workflow canvas, the filled result, and a sanitized execution report.
The screenshots contain no API key, upload token, customer document, or real
customer data.

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
- `open_pdf` has a 180-second HTTP timeout because the first dense-form
  detection after a cold start can exceed n8n's 60-second default.
- The AI workflow renders every page containing a detected field and every page
  that receives a proposed value. Do not bypass **Require approval** when
  adapting the workflow.
- Field ids are drawn inside their own boxes. Keep that placement: labels drawn
  above tightly stacked rows can visually associate row B's id with row A.
- n8n gotcha: object literals inside `{{ }}` expressions that contain `}}`
  (e.g. `arguments: {}}`) break the expression parser — this workflow builds
  the JSON-RPC bodies in Code nodes and only `JSON.stringify(...)` in
  expressions. Keep that pattern if you extend it.

Verified end-to-end on n8n 2.28.6 against production on 2026-07-20: all ten
executable nodes succeeded, three of three values matched a saved template,
and the downloaded PDF passed structural, extracted-text, and visual checks.
