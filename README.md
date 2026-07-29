# JustFill MCP Server

Let AI agents (Claude, ChatGPT, n8n — any MCP client) detect, review and fill
PDF form fields through [justfill.app](https://justfill.app).

<!-- mcp-name: io.github.mrmaciej1/justfill -->

## Excel or CSV batch workflow

If the source data is already in a spreadsheet and you need one filled copy of
the same existing PDF per row, an MCP client is optional. The guided browser
workflow imports XLSX or CSV, maps columns to reviewed PDF fields, previews each
record, and exports the approved PDFs in a ZIP.

[Try the five-row PDF mail merge sample](https://justfill.app/solutions/fill-pdf-from-excel?utm_source=github&utm_medium=referral&utm_campaign=b2b_pdf_automation_2026q3&utm_content=mcp_readme_excel_batch)
— no card or sales call.

## Import-ready n8n workflows

Start with the
[free reviewed template in the official n8n workflow catalog](https://n8n.io/workflows/17274-fill-pdf-forms-from-json-data-with-justfill-and-a-form-trigger/).
It collects a PDF and JSON payload, reuses the reviewed field names saved for
that exact form, fills the original layout and returns a temporary download
link.

The repository also includes the exact deterministic workflow JSON, synthetic
test PDF, production evidence and a separate two-pass vision workflow for an
unfamiliar form. Both call the hosted MCP endpoint with standard HTTP Request
nodes and can be inspected before adding credentials.

[Inspect the source workflows and evidence](examples/n8n/README.md), or follow the
[step-by-step n8n setup](https://justfill.app/integrations/n8n-fill-pdf-forms?utm_source=github&utm_medium=referral&utm_campaign=b2b_pdf_automation_2026q3&utm_content=mcp_readme_n8n_workflows).

## Gemini CLI extension

Install the same reviewed MCP tools plus the included PDF workflow guidance:

```bash
gemini extensions install https://github.com/mrmaciej1/justfill-mcp
```

The extension manifest lives at the repository root and uses the published
`justfill-mcp` package. Gemini CLI asks for normal third-party extension consent
before enabling it.

## Why agents can trust it

| Source | Confidence | What it means |
|---|---|---|
| **Saved template** | 1.0 | This exact PDF was filled before; geometry is human/agent-verified. No ML runs at all. |
| **AcroForm** | 1.0 | The PDF has embedded form fields — read from the file, filled natively. |
| **ML detection** | 0.0–0.95 | An honest draft. Review it visually (`render_preview`), fix it, then `save_template` to lock it in. |

ML confidence is *calibrated*: the detector's raw scores are not
probabilities (its server-side filter accepts boxes from raw ~0.02 and
auto-accepts at raw 0.15), so they are mapped onto 0–1 to mean what you'd
expect — ≥0.75 "detector is sure", 0.4–0.75 "probably right, glance at the
preview", <0.4 "borderline accept, verify". The raw detector score is kept
on each field as `raw_score`.

The correction loop (`render_preview` → `add/update/remove_field`) exists
precisely because ML detection has false positives and negatives. A false
positive costs nothing (leave it unfilled or remove it); a false negative is
visible on the preview and fixable with one `add_field` call. Once reviewed,
`save_template` makes every future fill of that form deterministic.

## Setup

```bash
uv tool install justfill-mcp
```

Authorize once (opens the browser, one click while logged in to justfill.app):

```bash
justfill-mcp login
```

Then the config needs no credentials at all:

```json
{
  "mcpServers": {
    "justfill": { "command": "justfill-mcp" }
  }
}
```

For a zero-install configuration, use `uvx` directly:

```json
{
  "mcpServers": {
    "justfill": {
      "command": "uvx",
      "args": ["justfill-mcp"]
    }
  }
}
```

Alternatives, in the order the server checks them:

1. `JUSTFILL_API_KEY` env — create a key at justfill.app → Account → API Keys
   and put `"env": {"JUSTFILL_API_KEY": "jf_live_…"}` in the config.
2. The key saved by `justfill-mcp login` (`~/.config/justfill/credentials.json`).
3. `JUSTFILL_EMAIL` + `JUSTFILL_PASSWORD` — legacy fallback; an API key is
   better (no password in config files, revocable per client, never expires
   mid-session).

## Tools

- `open_pdf(path, min_confidence=0.0, max_pages=10, force_detect=False)` —
  template → AcroForm → ML resolution order. Accepts scanned images too
  (jpg/png/tiff → converted to PDF, deterministically, so templates still
  match). `force_detect=True` ignores a saved template and re-runs ML.
- `render_preview(page_index)` — page image with labeled field boxes (blue = deterministic, green/orange/red = ML confidence)
- `render_filled_preview(values, page_index)` — the same page with your values
  drawn in place (checkboxes get an X). Costs no fills — check before you fill.
- `list_fields(page_index?)`
- `add_field(x, y, w, h, name, page_index, field_type, align?, vertical_align?)` — coords in % of page, top-left origin
- `update_field(field_id, …)` / `remove_field(field_id)`
- `update_fields([{field_id, …}, …])` / `remove_fields([ids])` — batch versions
- `prune_fields(field_type?, confidence_below?, width_below?, height_below?, page_index?, exclude_ids?)` —
  bulk-delete detection noise in one call (criteria AND-ed, removed ids returned)
- `fill_pdf(values, output_path, flatten=True)` — `values` = `{field_id: text}`;
  responds with `warnings` for values that will be shrunk/truncated to fit
- `save_template(name)` — persist the reviewed layout for deterministic repeat fills
- `list_templates()`

Text alignment: `align` = `left|center|right`, `vertical_align` =
`top|middle|bottom` — set per field (e.g. `right` for RTL forms, `center` for
boxed digits). Persisted in templates.

## Example agent flow

```
open_pdf("~/forms/w-9.pdf")            → acroform, 27 fields, confidence 1.0
fill_pdf({"f1": "Jane Doe", …}, "~/out/w-9-filled.pdf")
```

```
open_pdf("~/forms/scan.jpg")           → converted to PDF; ml, 34 fields
render_preview(0)                      → agent sees noise + one missed line
prune_fields(field_type="cell", width_below=3)   → 16 removed in one call
add_field(x=18, y=62.5, w=40, h=3, name="Phone")
render_filled_preview({…})             → values sit right, no overflow
fill_pdf({…}, "~/out/filled.pdf")
save_template("Client intake form")    → next time: deterministic
```

## Notes

- Auth is a regular justfill.app account; tokens auto-refresh on expiry.
- Usage and document-output rules are enforced by the same account service as
  the web app. `fill_pdf` reports whether the output is clean or watermarked.
- One PDF open at a time per server session (by design — keeps ids stable).
- This repository mirrors released versions of the MCP client (development
  happens in a private monorepo alongside the justfill.app backend). Bug
  reports and feature requests are very welcome in the issue tracker here.
