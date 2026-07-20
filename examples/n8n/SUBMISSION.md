# n8n template submission packet

Prepared: 2026-07-20. Submit the deterministic workflow first. New n8n creators
may have only one template under review, so do not block it with the AI-vision
variant.

Official template catalog: `https://n8n.io/workflows/` → **Submit a template**.

## Primary submission

- Workflow JSON: `fill-pdf-workflow.json`
- Title: `Fill an Existing PDF Form from JSON with a Reviewed JustFill Template`
- Category: `Document Ops`
- Audience: operations teams, agencies, bookkeepers, and automation builders
- Apps/services: `Form Trigger`, `HTTP Request`, `Code`, `JustFill MCP`
- External URL:
  `https://justfill.app/integrations/n8n-fill-pdf-forms?utm_source=n8n&utm_medium=referral&utm_campaign=b2b_pdf_automation_2026q3&utm_content=deterministic_template`

### Short overview

> Upload an existing PDF and provide JSON values. The workflow sends the binary
> through a one-time upload URL, opens the PDF through JustFill's hosted MCP
> server, maps JSON keys to a previously reviewed field layout, fills the form,
> and returns a short-lived download link. After the template is saved, each run
> is deterministic and uses no AI model.

### Detailed description

> Use this workflow when the required output must keep an existing customer's,
> carrier's, employer's, or organization's PDF layout. It starts with a test
> form, but the trigger can be replaced with Gmail, Drive, Dropbox, Airtable, or
> a webhook. The file is uploaded as binary rather than embedded as base64.
>
> Setup requires a JustFill API key and a reviewed template for the exact PDF.
> The JSON object's keys are matched to semantic field names with
> case/punctuation-insensitive matching. The result reports the download URL,
> number of filled fields, output mode, expiration, and warnings. Use synthetic
> data for the first run and move the key from the Config node to an n8n
> credential before production.

### Required credentials

- A JustFill API key created under `https://justfill.app/account`.
- No model key is required.
- The imported Config node stores its sample value in workflow JSON; replace it
  and move the real key to an n8n credential or environment variable.

### Setup steps

1. Import `fill-pdf-workflow.json`.
2. Create a JustFill API key and configure it without committing the secret.
3. Open the exact recurring PDF once, visually review the fields, give them
   semantic names, and save the template.
4. Activate the workflow and test with the same PDF plus synthetic JSON keyed by
   those names.
5. Verify field placement and `output_mode` before replacing the Form Trigger.

### Reviewer test data

Upload `reviewer-sample-supplier-intake.pdf` and use this synthetic data:

```json
{
  "company_name": "Northwind LLC",
  "vendor_reference": "V-1042",
  "remittance_email": "ap@northwind.example"
}
```

Do not use real tax IDs, health data, card data, secrets, signatures, or customer
documents in the public template review.

### Production validation evidence

Validated on 2026-07-20 in a fresh, isolated n8n 2.28.6 instance against the
production MCP endpoint and a saved copy of the reviewer template:

- execution status: `success` in 4.748 seconds;
- 10/10 executable nodes ran once and succeeded;
- `open_pdf` reused `source=template`;
- 3/3 JSON keys matched, with no unmatched keys;
- `fill_pdf` filled three fields with no warnings;
- the technical free account correctly returned `output_mode=watermarked`;
- the downloaded PDF parsed as one page, was flattened as expected, exposed all
  three values to text extraction, and passed manual visual review.

Submission assets:

- `assets/deterministic-workflow-success.png` — full green canvas and success
  notification, with the Config node closed;
- `assets/deterministic-filled-result.png` — rendered synthetic output;
- `assets/deterministic-e2e-2026-07-20.json` — sanitized timing/status report;
- `reviewer-sample-supplier-intake.pdf` — synthetic input PDF.

## Secondary submission after the first is approved

- Workflow JSON: `fill-pdf-ai-vision.json`
- Title: `Map JSON Values onto an Unfamiliar PDF with Vision and JustFill`
- Category: `AI` or `Document Ops`
- External URL:
  `https://justfill.app/integrations/n8n-fill-pdf-forms?utm_source=n8n&utm_medium=referral&utm_campaign=b2b_pdf_automation_2026q3&utm_content=vision_template`

This variant requires a JustFill key and a Gemini API key. It renders all
detected pages, uses the first vision call for mapping, renders the proposed
values on every used page, and sends both source and filled images to a second
vision call. `fill_pdf` is reachable only through an explicit approval gate;
rejections and malformed reviews stop the run. Its output must still be
described as best-effort, with manual review required before high-impact use.
For recurring documents, the deterministic template remains the recommended
production path.

## Primary submission checklist

- [x] Import the deterministic JSON into a clean n8n 2.28.6 instance; confirm
  the public workflow contains only the documented API-key placeholder.
- [x] Execute the primary workflow against production with synthetic data.
- [x] Capture a workflow-canvas screenshot and one result screenshot after the
  successful run; confirm neither exposes an API key or upload token.
- [ ] Paste the primary copy above into n8n Creator and submit only that
  workflow. This step requires the publisher's interactive n8n account.
- [ ] After publication, add the public template URL and date to
  `docs/marketing/b2b-distribution-tracker.csv`.
- [ ] Evaluate the channel by external MCP opens/fills, checkouts, and paid
  customers—not template views alone.

The secondary AI-vision workflow has separate contract and production-approval
evidence. Execute and capture its live rejection path only when the primary
template has been approved and n8n allows the second submission; it is not a
blocker for this deterministic listing.
