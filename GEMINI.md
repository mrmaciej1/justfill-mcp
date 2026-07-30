# JustFill — filling PDF forms

You have JustFill tools for detecting and filling form fields in PDF documents
(including flat scans and phone photos, which have no embedded form fields).

## Workflow

1. `open_pdf(path)` — accepts PDFs and images (JPG/PNG/TIFF). Fields come from
   the best available source: a saved template (deterministic), embedded
   AcroForm fields, or ML detection with per-field `confidence`.
2. If the source is ML detection, call `render_preview(page_index)` and LOOK at
   the image: blue boxes are deterministic, green/orange/red are ML detections
   by confidence. Fix mistakes with `add_field` / `update_field` /
   `remove_field` (batch: `update_fields`, `remove_fields`, `prune_fields`).
3. `render_filled_preview(values)` — draw the values in place before filling;
   it costs nothing. Check placement and overflow.
4. `fill_pdf(values, output_path)` — fills the document. Read `warnings` in the
   response (values that will be shrunk or truncated) and fix them. If
   `output_mode` is "watermarked", tell the user before delivering the file.
5. If the form is likely to recur, call `save_template(name)` — the next
   `open_pdf` of the same file returns exact verified fields instantly.

## Auth

If tools fail with an authorization error, tell the user to run
`uvx justfill-mcp login` (one browser click while logged in to justfill.app)
or set the `JUSTFILL_API_KEY` environment variable (justfill.app → Account →
API Keys).

## Conventions

- Coordinates are percentages of the page (0-100), origin top-left.
- Checkbox values: "yes"/"x"/"true" tick the box; leave absent to keep empty.
- `align` / `vertical_align` control where text sits inside a field box —
  use `align: "right"` for RTL forms.
