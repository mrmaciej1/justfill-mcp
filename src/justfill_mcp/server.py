"""JustFill MCP server — AI agents detect, review and fill PDF form fields.

Design pillars (in tool-flow order):
  1. TEMPLATES are the guarantee: open_pdf first probes saved calibrations by
     content hash — a match means exact, human/agent-verified geometry (conf 1.0).
  2. ACROFORM is the sure thing: PDFs with embedded form fields need no ML at
     all — fields come from the file itself (conf 1.0, filled natively).
  3. ML DETECTION is an honest draft: per-field `confidence` is exposed and
     `min_confidence` lets the caller pick the precision/recall trade-off.
  4. AGENT-REVIEW closes the loop: render_preview shows the boxes drawn on the
     page (color = trust), and add/update/remove_field let the agent fix FP/FN
     before filling. save_template persists the corrected layout so every
     future fill of this form is deterministic.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image as MCPImage

from .api_client import JustFillClient
from .workspace import (
    Workspace,
    content_hash,
    draw_filled_overlay,
    draw_overlay,
    estimate_fit,
    field_from_box2d,
    field_from_pixels,
    field_to_calibration,
    field_to_generation,
    page_sizes_pt,
    pdf_from_image,
)

mcp = FastMCP(
    "justfill",
    instructions=(
        "Fill PDF forms via justfill.app. Typical flow: open_pdf (accepts PDFs "
        "and scanned images) -> render_preview (inspect the detected boxes "
        "visually) -> fix mistakes with add/update/remove_field (batch: "
        "update_fields, remove_fields, prune_fields) -> render_filled_preview "
        "to sanity-check the values in place -> fill_pdf -> then ASK the user "
        "whether to save the layout as a reusable template (save_template) so "
        "the next fill of this form is instant and exact; saved templates are "
        "viewable at justfill.app/dashboard?tab=templates. Field confidence: "
        "1.0 means deterministic (saved template or embedded AcroForm field); "
        "lower values are ML detections — review them."
    ),
)

_client: JustFillClient | None = None
_ws: Workspace | None = None


def _api() -> JustFillClient:
    global _client
    if _client is None:
        _client = JustFillClient()
    return _client


def _workspace() -> Workspace:
    if _ws is None:
        raise RuntimeError("No PDF is open. Call open_pdf first.")
    return _ws


@mcp.tool()
def open_pdf(
    path: str, min_confidence: float = 0.0, max_pages: int = 10, force_detect: bool = False
) -> str:
    """Open a PDF (or a scanned image: jpg/png/tiff) and detect its fillable fields.

    Images are converted to a single-page PDF automatically (deterministically,
    so a template saved for a photo matches the same photo next time).

    Resolution order (best source wins):
      1. Saved template matching this exact file (deterministic, confidence 1.0)
      2. Embedded AcroForm fields (deterministic, confidence 1.0)
      3. ML detection (each field carries a confidence score)

    force_detect=True skips steps 1-2 and re-runs ML detection from scratch —
    use it to rebuild a layout when the saved template is wrong or stale.
    min_confidence drops ML fields scored below it (templates/AcroForm are
    always kept). Returns a JSON summary + the field list.
    """
    global _ws
    raw_bytes = Path(path).expanduser().read_bytes()
    converted = False
    if raw_bytes.startswith(b"%PDF-"):
        pdf_bytes = raw_bytes
    else:
        try:
            pdf_bytes = pdf_from_image(raw_bytes)
            converted = True
        except Exception:
            return json.dumps({
                "error": f"'{path}' is neither a PDF nor a readable image. "
                         "Supported inputs: PDF, JPG, PNG, TIFF, BMP, WEBP."
            })
    h = content_hash(pdf_bytes)
    ws = Workspace(
        pdf_path=str(path), pdf_bytes=pdf_bytes, hash=h, converted_from_image=converted
    )

    # 1) Template probe — the recurring-fill guarantee.
    calibrations = [] if force_detect else _api().calibrations_by_hash(h)
    if calibrations:
        cal = calibrations[0]
        raw_fields = cal.get("fields", [])
        ws.fields = [field_from_box2d(f, "template") for f in raw_fields if f.get("enabled", True)]
        ws.source = "template"
        ws.template_name = cal.get("displayName") or cal.get("documentName")
        ws.page_count = max((f["page_index"] for f in ws.fields), default=0) + 1
        _set_ws(ws)
        return json.dumps({"summary": ws.summary(), "fields": ws.fields}, indent=1)

    # 2) AcroForm probe — exact fields embedded in the file, no ML needed.
    fillable = _api().detect_fillable(pdf_bytes)
    ws.page_count = fillable.get("pageCount", 1)
    if fillable.get("isFillable") and fillable.get("fields"):
        ws.fields = [field_from_box2d(f, "acroform") for f in fillable["fields"]]
        ws.source = "acroform"
        _set_ws(ws)
        return json.dumps({"summary": ws.summary(), "fields": ws.fields}, indent=1)

    # 3) ML detection — an honest draft with per-field confidence.
    pages = list(range(min(ws.page_count, max_pages)))
    result = _api().detect_fields_batch(base64.b64encode(pdf_bytes).decode(), pages)
    fields: list[dict] = []
    for page_result in result.get("results", []):
        pw, ph = page_result["imageWidth"], page_result["imageHeight"]
        for f in page_result.get("fields", []):
            nf = field_from_pixels(f, pw, ph, page_result["pageIndex"])
            if nf["confidence"] >= min_confidence:
                fields.append(nf)
    ws.fields = fields
    ws.source = "ml"
    _set_ws(ws)
    payload = {"summary": ws.summary(), "fields": ws.fields}
    if ws.source == "ml":
        payload["note"] = (
            "Fields are ML detections — call render_preview and visually verify "
            "box placement before filling. Fix with update/add/remove_field; "
            "after a successful fill, ask the user whether to save the layout "
            "as a template (future fills of this form become deterministic)."
        )
    return json.dumps(payload, indent=1)


def _set_ws(ws: Workspace) -> None:
    global _ws
    _ws = ws


@mcp.tool()
def list_fields(page_index: int | None = None) -> str:
    """List the current working fields (optionally one page only)."""
    ws = _workspace()
    fields = [f for f in ws.fields if page_index is None or f["page_index"] == page_index]
    return json.dumps({"summary": ws.summary(), "fields": fields}, indent=1)


@mcp.tool()
def render_preview(page_index: int = 0) -> MCPImage:
    """Render a page with the working field boxes drawn on it.

    Look at this image to VERIFY placement: blue boxes are deterministic
    (template/AcroForm/agent-placed); green/orange/red are ML detections by
    confidence (>=0.7 / >=0.4 / <0.4). Each box is labeled with its field id.
    """
    ws = _workspace()
    render = _api().render_page(ws.pdf_bytes, page=page_index, dpi=150)
    ws.page_count = render.get("pageCount", ws.page_count)
    png = draw_overlay(render["imageBase64"], ws.fields, page_index)
    return MCPImage(data=png, format="png")


@mcp.tool()
def render_filled_preview(values: dict[str, str], page_index: int = 0) -> MCPImage:
    """Preview how the filled page will look BEFORE generating the PDF.

    Draws `values` (field id -> text; checkboxes get an X) into their boxes on
    the rendered page. Costs nothing — no fill is consumed. Typography is an
    approximation of the final output (the server typesets the real PDF), so
    use it to verify placement, alignment and obvious overflow, then fill_pdf.
    """
    ws = _workspace()
    render = _api().render_page(ws.pdf_bytes, page=page_index, dpi=150)
    ws.page_count = render.get("pageCount", ws.page_count)
    png = draw_filled_overlay(render["imageBase64"], ws.fields, values, page_index, dpi=150)
    return MCPImage(data=png, format="png")


_ALIGNS = ("left", "center", "right")
_VALIGNS = ("top", "middle", "bottom")


def _validate_align(align: str | None, vertical_align: str | None) -> str | None:
    if align is not None and align not in _ALIGNS:
        return f"align must be one of {_ALIGNS}, got '{align}'."
    if vertical_align is not None and vertical_align not in _VALIGNS:
        return f"vertical_align must be one of {_VALIGNS}, got '{vertical_align}'."
    return None


@mcp.tool()
def add_field(
    x: float, y: float, w: float, h: float,
    name: str = "", page_index: int = 0, field_type: str = "text",
    align: str | None = None, vertical_align: str | None = None,
) -> str:
    """Add a field the detector missed (a false negative).

    Coordinates are percentages of the page (0-100), top-left origin —
    read them off the render_preview image proportionally.
    align ('left'|'center'|'right') and vertical_align ('top'|'middle'|'bottom')
    control where the value sits inside the box when the PDF is filled.
    """
    ws = _workspace()
    if err := _validate_align(align, vertical_align):
        return json.dumps({"error": err})
    n = 1
    while any(f["id"] == f"p{page_index}_a{n}" for f in ws.fields):
        n += 1
    f = {
        "id": f"p{page_index}_a{n}",
        "name": name, "x": x, "y": y, "w": w, "h": h,
        "page_index": page_index, "type": field_type,
        "confidence": 1.0, "source": "agent", "fillable": None,
    }
    if align:
        f["align"] = align
    if vertical_align:
        f["vertical_align"] = vertical_align
    ws.fields.append(f)
    return json.dumps(f)


def _apply_update(f: dict, changes: dict) -> None:
    for key in ("x", "y", "w", "h", "name"):
        if changes.get(key) is not None:
            f[key] = changes[key]
    if changes.get("field_type") is not None:
        f["type"] = changes["field_type"]
    for key in ("align", "vertical_align"):
        if changes.get(key) is not None:
            f[key] = changes[key]
    if f["source"] == "ml":
        f["source"] = "agent"   # reviewed by the agent -> now trusted
        f["confidence"] = 1.0


@mcp.tool()
def update_field(
    field_id: str,
    x: float | None = None, y: float | None = None,
    w: float | None = None, h: float | None = None,
    name: str | None = None, field_type: str | None = None,
    align: str | None = None, vertical_align: str | None = None,
) -> str:
    """Move/resize/rename a field, or set its text alignment.

    align: 'left'|'center'|'right'; vertical_align: 'top'|'middle'|'bottom' —
    where the value sits inside the box in the filled PDF.
    """
    ws = _workspace()
    if err := _validate_align(align, vertical_align):
        return json.dumps({"error": err})
    f = ws.get_field(field_id)
    _apply_update(f, {
        "x": x, "y": y, "w": w, "h": h, "name": name,
        "field_type": field_type, "align": align, "vertical_align": vertical_align,
    })
    return json.dumps(f)


@mcp.tool()
def update_fields(updates: list[dict]) -> str:
    """Update many fields in one call (batch version of update_field).

    Each item: {"field_id": "...", and any of x, y, w, h, name, field_type,
    align, vertical_align}. Items with an unknown field_id are reported back,
    the rest are still applied.
    """
    ws = _workspace()
    applied, missing = [], []
    for changes in updates:
        fid = changes.get("field_id") or changes.get("id")
        if not fid:
            missing.append(changes)
            continue
        if err := _validate_align(changes.get("align"), changes.get("vertical_align")):
            return json.dumps({"error": f"{fid}: {err}"})
        try:
            f = ws.get_field(str(fid))
        except KeyError:
            missing.append(fid)
            continue
        _apply_update(f, changes)
        applied.append(f["id"])
    out: dict = {"updated": applied}
    if missing:
        out["unknown_field_ids"] = missing
    return json.dumps(out)


@mcp.tool()
def remove_field(field_id: str) -> str:
    """Delete a field that isn't a real input (a false positive)."""
    ws = _workspace()
    f = ws.get_field(field_id)
    ws.fields.remove(f)
    return f"Removed {field_id} ({len(ws.fields)} fields remain)."


@mcp.tool()
def remove_fields(field_ids: list[str]) -> str:
    """Delete many fields in one call (batch version of remove_field)."""
    ws = _workspace()
    ids = set(field_ids)
    found = [f for f in ws.fields if f["id"] in ids]
    ws.fields = [f for f in ws.fields if f["id"] not in ids]
    out: dict = {"removed": len(found), "fields_remaining": len(ws.fields)}
    missing = ids - {f["id"] for f in found}
    if missing:
        out["unknown_field_ids"] = sorted(missing)
    return json.dumps(out)


@mcp.tool()
def prune_fields(
    field_type: str | None = None,
    confidence_below: float | None = None,
    width_below: float | None = None,
    height_below: float | None = None,
    page_index: int | None = None,
    exclude_ids: list[str] | None = None,
) -> str:
    """Bulk-delete fields matching ALL given criteria (e.g. detection noise).

    field_type: exact type match (e.g. 'cell'); confidence_below /
    width_below / height_below: strictly-less-than thresholds (w/h in % of
    page); page_index: limit to one page; exclude_ids: always keep these.
    Returns the removed ids so the operation is auditable (and reversible
    via add_field if it cut too much).
    """
    ws = _workspace()
    if all(v is None for v in (field_type, confidence_below, width_below, height_below, page_index)):
        return json.dumps({"error": "Give at least one criterion — refusing to delete everything."})
    keep = set(exclude_ids or [])

    def matches(f: dict) -> bool:
        if f["id"] in keep:
            return False
        if field_type is not None and f.get("type") != field_type:
            return False
        if confidence_below is not None and not f.get("confidence", 0) < confidence_below:
            return False
        if width_below is not None and not f.get("w", 0) < width_below:
            return False
        if height_below is not None and not f.get("h", 0) < height_below:
            return False
        if page_index is not None and f.get("page_index", 0) != page_index:
            return False
        return True

    removed = [f["id"] for f in ws.fields if matches(f)]
    ws.fields = [f for f in ws.fields if f["id"] not in set(removed)]
    return json.dumps({
        "removed": len(removed), "removed_ids": removed, "fields_remaining": len(ws.fields),
    })


def _overflow_warnings(ws, values: dict[str, str]) -> list[str]:
    """Predict which values the server will shrink or truncate to fit their box."""
    try:
        sizes = page_sizes_pt(ws.pdf_bytes)
    except Exception:
        return []
    warnings = []
    for f in ws.fields:
        val = values.get(f["id"], "")
        if not val or f.get("type") == "checkbox" or f.get("fillable"):
            continue
        page_w, page_h = sizes[min(f.get("page_index", 0), len(sizes) - 1)]
        fit = estimate_fit(val, f["w"] / 100 * page_w, f["h"] / 100 * page_h)
        if fit and fit["result"] == "truncated":
            warnings.append(
                f"{f['id']}: value does not fit even at minimum font size — "
                f"it will be cut off with an ellipsis. Shorten it or enlarge the box."
            )
        elif fit:
            warnings.append(
                f"{f['id']}: value only fits shrunk to ~{fit['font_pt']}pt — "
                f"check readability in render_filled_preview."
            )
    return warnings


@mcp.tool()
def fill_pdf(values: dict[str, str], output_path: str, flatten: bool = True) -> str:
    """Fill the PDF and save it.

    `values` maps field id -> text value (checkboxes: "true"/"yes"/"x" to tick).
    Fields not present in `values` stay empty. AcroForm fields are filled
    natively inside the PDF; everything else is drawn at its exact box.
    The response's `warnings` list flags values that will be shrunk or
    truncated to fit their box — fix those before delivering the document.
    """
    ws = _workspace()
    gen_fields = []
    unknown = [fid for fid in values if not any(f["id"] == fid for f in ws.fields)]
    if unknown:
        return json.dumps({
            "error": f"Unknown field ids: {unknown}. Use list_fields for valid ids, "
                     f"or add_field if the detector missed an input there."
        })
    for f in ws.fields:
        val = values.get(f["id"], "")
        if val:
            gen_fields.append(field_to_generation(f, val))
    if not gen_fields:
        return json.dumps({"error": "No values matched any field id."})

    pdf, output_mode = _api().generate_pdf(ws.pdf_bytes, json.dumps(gen_fields), flatten=flatten)
    out = Path(output_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf)
    result = {
        "saved": str(out),
        "filled_fields": len(gen_fields),
        "output_mode": output_mode,
        "next_step": (
            "ASK the user now whether to save this reviewed field layout as a "
            "reusable template (save_template) — future fills of this exact "
            "form become instant and deterministic. Ask first; do not save "
            "without their yes."
        ),
    }
    overflow = _overflow_warnings(ws, values)
    if overflow:
        result["warnings"] = overflow
    if output_mode == "watermarked":
        result["warning"] = (
            "This PDF carries a JustFill watermark — the account's free fills "
            "are used up. TELL THE USER before delivering it; a paid plan or "
            "credit pack at justfill.app/billing removes the watermark."
        )
    return json.dumps(result)


@mcp.tool()
def save_template(name: str) -> str:
    """Save the current (reviewed) field layout as a reusable template.

    Next time this exact PDF is opened — by you or another agent session on
    this account — open_pdf returns these fields with confidence 1.0 and no
    ML pass at all. This is what makes repeat filling deterministic.

    Before saving, give every field a short semantic name (update_fields with
    name=..., e.g. 'age_score', 'total_abcd2') — names are stored in the
    template, so the next session maps values by meaning instead of guessing
    from coordinates. Also remove false positives first (remove_fields).
    """
    ws = _workspace()
    if not ws.fields:
        return "Nothing to save — no fields in the workspace."
    unnamed = [f["id"] for f in ws.fields if not f.get("name")]
    if len(unnamed) * 2 > len(ws.fields):
        return json.dumps({
            "error": (
                "Most fields have no name — a template of anonymous boxes "
                "forces every future session to re-derive what each field "
                "means. Name them first (update_fields with name=..., short "
                "semantic names like 'age_score'), remove false positives "
                "(remove_fields), then call save_template again."
            ),
            "unnamed_field_ids": unnamed,
        })
    document_id = str(uuid.uuid4())
    cal_fields = [field_to_calibration(f) for f in ws.fields]
    _api().save_calibration(document_id, ws.hash, name, cal_fields, ws.pdf_bytes)
    ws.source = "template"
    ws.template_name = name
    result = {
        "saved_template": name, "document_id": document_id, "fields": len(cal_fields),
        "view_url": f"{_api().base_url}/dashboard?tab=templates",
        "tell_user": (
            "Tell the user the template is saved to their justfill.app account "
            "and they can view, rename or delete it anytime at the view_url."
        ),
    }
    if unnamed:
        result["warning"] = (
            f"{len(unnamed)} field(s) still unnamed ({unnamed}) — future "
            "sessions will see opaque ids for those."
        )
    return json.dumps(result)


@mcp.tool()
def list_templates(limit: int = 50) -> str:
    """List saved templates on this account (name + field count + hash)."""
    data = _api().list_calibrations(limit=limit)
    items = data.get("items", data) if isinstance(data, dict) else data
    slim = [
        {
            "name": c.get("displayName") or c.get("documentName"),
            "document_id": c.get("documentId"),
            "content_hash": c.get("contentHash"),
            "fields": len(c.get("fields", [])),
        }
        for c in items
    ]
    return json.dumps(slim, indent=1)


def main() -> None:
    import sys
    if sys.argv[1:2] == ["login"]:
        # `justfill-mcp login` — browser-based authorization (see login.py)
        from justfill_mcp.login import main as login_main
        sys.exit(login_main())
    mcp.run()


if __name__ == "__main__":
    main()
