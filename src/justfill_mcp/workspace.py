"""Working state for one open PDF + coordinate normalization + preview overlay.

Internal field format (single source of truth for the agent):
    {
      "id": str,            # stable handle the agent uses in update/fill calls
      "name": str,          # human/AcroForm field name
      "x", "y", "w", "h":   float, 0-100 percentages of page size, top-left origin
      "page_index": int,
      "type": str,          # text | checkbox | field | cell | ...
      "confidence": float,  # 1.0 = template/AcroForm (deterministic),
                            # else calibrated ML score (see calibrate_ml_score)
      "source": str,        # "template" | "acroform" | "ml" | "agent"
      "fillable": dict|None # passthrough AcroForm keys for native fill
    }

Upstream coordinate systems being normalized:
  - ML detect:            pixels (x0,y0,x1,y1) + imageWidth/imageHeight
  - AcroForm/calibration: box_2d [ymin,xmin,ymax,xmax] on a 0-1000 scale
  - generate/fill API:    x/y/w/h 0-100 percentages (our internal format)
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Passthrough keys (camelCase, as the API serializes them) that make the
# server route a field through the NATIVE AcroForm fill instead of overlay.
_FILLABLE_KEYS = [
    "fillableFieldName", "fillableFieldType", "fillableExportValue",
    "fillableOptions", "fillableTooltip", "fillableIsRequired",
    "fillableIsMultiline", "fillableMaxLength", "fillableTextAlign",
    "fillableDefaultValue", "fillableIsComb",
]

# snake_case fallbacks in case an endpoint serializes without aliases
_SNAKE_TO_CAMEL = {
    "fillable_field_name": "fillableFieldName",
    "fillable_field_type": "fillableFieldType",
    "fillable_export_value": "fillableExportValue",
    "fillable_options": "fillableOptions",
    "fillable_tooltip": "fillableTooltip",
    "fillable_is_required": "fillableIsRequired",
    "fillable_is_multiline": "fillableIsMultiline",
    "fillable_max_length": "fillableMaxLength",
    "fillable_text_align": "fillableTextAlign",
    "fillable_default_value": "fillableDefaultValue",
    "fillable_is_comb": "fillableIsComb",
}


def _get(d: dict, camel: str, snake: str, default=None):
    if camel in d:
        return d[camel]
    return d.get(snake, default)


def _extract_fillable(raw: dict) -> dict | None:
    out: dict[str, Any] = {}
    for k in _FILLABLE_KEYS:
        if raw.get(k) is not None:
            out[k] = raw[k]
    for snake, camel in _SNAKE_TO_CAMEL.items():
        if camel not in out and raw.get(snake) is not None:
            out[camel] = raw[snake]
    return out or None


def content_hash(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()[:32]


def pdf_from_image(data: bytes) -> bytes:
    """Convert an image (jpg/png/tiff/...) to a single-page PDF.

    Deterministic: the same image bytes always produce the same PDF bytes
    (fixed creation/mod dates), so content_hash — and therefore template
    matching — is stable across sessions for the same source photo.
    """
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Scale the page to roughly A4 so downstream DPI-based rendering
    # (detect at 200 DPI) doesn't explode pixel counts for large photos.
    resolution = max(72.0, max(img.size) / 11.69)  # A4 long side = 11.69 in
    epoch = time.gmtime(946684800)  # 2000-01-01, fixed so output bytes are stable
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=resolution, creationDate=epoch, modDate=epoch)
    return buf.getvalue()


def page_sizes_pt(pdf_bytes: bytes) -> list[tuple[float, float]]:
    """Per-page (width, height) in PDF points, using the cropbox like the server does."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(float(p.cropbox.width), float(p.cropbox.height)) for p in reader.pages]


def field_from_box2d(raw: dict, source: str, page_index: int | None = None) -> dict:
    """Convert a CalibrationField-shaped dict (box_2d 0-1000) to internal format."""
    ymin, xmin, ymax, xmax = raw["box_2d"] if "box_2d" in raw else raw["box2d"]
    fillable = _extract_fillable(raw)
    out = {
        "id": str(raw.get("id") or f"F{uuid.uuid4().hex[:6]}"),
        "name": raw.get("name") or _get(raw, "fieldDescription", "field_description") or "",
        "x": round(xmin / 10.0, 2),
        "y": round(ymin / 10.0, 2),
        "w": round((xmax - xmin) / 10.0, 2),
        "h": round((ymax - ymin) / 10.0, 2),
        "page_index": _get(raw, "pageIndex", "page_index", page_index) or page_index or 0,
        "type": raw.get("type") or "text",
        # Saving a template is an act of endorsement (tools tell the agent to
        # review first), and AcroForm geometry comes from the file itself —
        # both are deterministic on load, whatever confidence the fields had
        # when saved. Otherwise every template hit would trigger a re-review
        # and the recurring-fill guarantee is gone.
        "confidence": 1.0,
        "source": source,
        "fillable": fillable,
    }
    align = _get(raw, "textAlign", "text_align")
    valign = _get(raw, "verticalAlign", "vertical_align")
    if align:
        out["align"] = align
    if valign:
        out["vertical_align"] = valign
    return out


# Raw D-FINE scores are NOT softmax-style probabilities. The server's
# post-filter (dfine_loose_filter) accepts boxes from ~0.02 (bucketed
# thresholds) and treats 0.15+ as strong enough to accept on score alone
# (LOOSE_CONF_RESCUE). Piecewise-map raw scores onto the 0-1 scale that
# min_confidence and the preview color bands promise, anchored on those
# filter semantics. 0.95 cap keeps 1.0 reserved for deterministic sources.
_ML_SCORE_CALIBRATION = [(0.0, 0.0), (0.03, 0.4), (0.15, 0.75), (0.5, 0.95)]


def calibrate_ml_score(score: float) -> float:
    pts = _ML_SCORE_CALIBRATION
    if score >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if score <= x1:
            return round(y0 + (score - x0) * (y1 - y0) / (x1 - x0), 3)
    return 0.0


def field_from_pixels(raw: dict, img_w: int, img_h: int, page_index: int) -> dict:
    """Convert a D-FINE DetectedField (pixel coords + score) to internal format."""
    x0, y0, x1, y1 = raw["x0"], raw["y0"], raw["x1"], raw["y1"]
    raw_score = float(raw.get("score", 0.0))
    return {
        "id": str(raw.get("id") or f"F{uuid.uuid4().hex[:6]}"),
        "name": "",
        "x": round(x0 / img_w * 100.0, 2),
        "y": round(y0 / img_h * 100.0, 2),
        "w": round((x1 - x0) / img_w * 100.0, 2),
        "h": round((y1 - y0) / img_h * 100.0, 2),
        "page_index": page_index,
        "type": raw.get("type") or "field",
        "confidence": calibrate_ml_score(raw_score),
        "raw_score": round(raw_score, 3),
        "source": "ml",
        "fillable": None,
    }


def field_to_generation(f: dict, value: str) -> dict:
    """Internal field + value -> FieldForGeneration payload (camelCase)."""
    out = {
        "id": f["id"],
        "value": value,
        "x": f["x"],
        "y": f["y"],
        "w": f["w"],
        "h": f["h"],
        "fieldDescription": f.get("name") or None,
        "fontSize": 0,  # 0 = server auto-sizes from box height
        "pageIndex": f.get("page_index", 0),
        "isCalibrated": f.get("source") in ("template", "acroform"),
    }
    if f.get("align"):
        out["textAlign"] = f["align"]
    if f.get("vertical_align"):
        out["verticalAlign"] = f["vertical_align"]
    if f.get("fillable"):
        out.update(f["fillable"])
    elif f.get("type") == "checkbox":
        # Same mapping the web UI applies to D-FINE type=='checkbox' boxes:
        # the overlay generator draws an X cross for checkbox/radio fields
        # instead of rendering the value as text (pdf_service.py).
        out["fillableFieldType"] = "checkbox"
    return out


def field_to_calibration(f: dict) -> dict:
    """Internal field -> CalibrationField payload (box_2d 0-1000, camelCase)."""
    out = {
        "id": f["id"],
        "name": f.get("name") or f["id"],
        "box_2d": [
            int(round(f["y"] * 10)),
            int(round(f["x"] * 10)),
            int(round((f["y"] + f["h"]) * 10)),
            int(round((f["x"] + f["w"]) * 10)),
        ],
        "pageIndex": f.get("page_index", 0),
        "type": f.get("type") or "text",
        "enabled": True,
    }
    if f.get("confidence") is not None:
        out["confidence"] = f["confidence"]
    if f.get("align"):
        out["textAlign"] = f["align"]
    if f.get("vertical_align"):
        out["verticalAlign"] = f["vertical_align"]
    if f.get("fillable"):
        out.update(f["fillable"])
    return out


@dataclass
class Workspace:
    """One open PDF and its working field set."""

    pdf_path: str = ""
    pdf_bytes: bytes = b""
    hash: str = ""
    page_count: int = 0
    source: str = ""            # template | acroform | ml
    template_name: str | None = None
    converted_from_image: bool = False
    fields: list[dict] = field(default_factory=list)

    def get_field(self, field_id: str) -> dict:
        for f in self.fields:
            if f["id"] == field_id:
                return f
        raise KeyError(f"No field with id '{field_id}'. Use list_fields to see current ids.")

    def summary(self) -> dict:
        by_page: dict[int, int] = {}
        for f in self.fields:
            by_page[f["page_index"]] = by_page.get(f["page_index"], 0) + 1
        out = {
            "pdf": self.pdf_path,
            "content_hash": self.hash,
            "page_count": self.page_count,
            "source": self.source,
            "template_name": self.template_name,
            "total_fields": len(self.fields),
            "fields_per_page": by_page,
        }
        if self.converted_from_image:
            out["converted_from_image"] = True
        return out


def draw_overlay(image_b64: str, fields: list[dict], page_index: int) -> bytes:
    """Draw field boxes + ids on a rendered page image. Returns PNG bytes.

    Colors encode trust: blue = deterministic (template/AcroForm/agent-placed),
    green/orange/red = ML confidence bands (>=0.7 / >=0.4 / <0.4).
    """
    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    for f in fields:
        if f.get("page_index", 0) != page_index:
            continue
        x0 = f["x"] / 100.0 * w
        y0 = f["y"] / 100.0 * h
        x1 = (f["x"] + f["w"]) / 100.0 * w
        y1 = (f["y"] + f["h"]) / 100.0 * h
        if f.get("source") in ("template", "acroform", "agent"):
            color = (37, 99, 235)      # blue — deterministic
        elif f.get("confidence", 0) >= 0.7:
            color = (22, 163, 74)      # green
        elif f.get("confidence", 0) >= 0.4:
            color = (217, 119, 6)      # orange
        else:
            color = (220, 38, 38)      # red
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = f["id"] if not f.get("name") else f"{f['id']}:{f['name'][:18]}"
        ty = y0 - 12 if y0 >= 12 else y1 + 2
        draw.rectangle([x0, ty, x0 + 6 * len(label) + 4, ty + 11], fill=color)
        draw.text((x0 + 2, ty), label, fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Overflow estimation — mirrors the server's _draw_field sizing so warnings
# are honest. Keep IN SYNC with server pdf_service.py / utils/fontSizing.ts:
#   auto font  = clamp(box_h_pt * 0.72, 7pt, 14pt); explicit capped at 0.85*h
#   wrap       = word wrap at box width - 2*2pt padding, line-height 1.2
#   shrink     = -0.5pt steps down to 4pt, then truncate with an ellipsis
# The default output font is DejaVuSansMono (advance width ~0.602 em).
_MONO_ADVANCE_EM = 0.602
_PAD_X_PT = 2.0
_MIN_FONT_PT = 4.0
_LINE_HEIGHT = 1.2


def _char_em(ch: str) -> float:
    # CJK glyphs are full-width (~1.0 em); everything else uses the mono advance.
    o = ord(ch)
    return 1.0 if (0x1100 <= o <= 0x11FF or 0x2E80 <= o <= 0x9FFF
                   or 0xAC00 <= o <= 0xD7FF or 0xF900 <= o <= 0xFAFF
                   or 0xFF00 <= o <= 0xFF60 or 0x20000 <= o <= 0x2FA1F) else _MONO_ADVANCE_EM


def _estimate_lines(value: str, font_pt: float, max_width_pt: float) -> int:
    lines = 0
    for para in value.split("\n"):
        width = sum(_char_em(ch) for ch in para) * font_pt
        lines += max(1, math.ceil(width / max(max_width_pt, 1.0)))
    return lines


def estimate_fit(value: str, box_w_pt: float, box_h_pt: float, explicit_pt: float = 0) -> dict | None:
    """Predict whether `value` fits its box in the server-rendered output.

    Returns None when it fits at the natural font size, otherwise a dict:
      {"result": "shrunk", "font_pt": ...}   — still complete, but smaller text
      {"result": "truncated"}                — won't fit even at 4pt; the server
                                               will cut it off with an ellipsis
    """
    if not value.strip():
        return None
    if explicit_pt and explicit_pt > 0:
        natural = min(explicit_pt, box_h_pt * 0.85)
    else:
        natural = min(max(box_h_pt * 0.72, 7.0), 14.0)
    max_width = max(1.0, box_w_pt - 2 * _PAD_X_PT)

    font_pt = natural
    while font_pt >= _MIN_FONT_PT:
        needed = _estimate_lines(value, font_pt, max_width) * font_pt * _LINE_HEIGHT
        if needed <= box_h_pt:
            break
        font_pt -= 0.5
    if font_pt < _MIN_FONT_PT:
        return {"result": "truncated"}
    if font_pt < natural:
        return {"result": "shrunk", "font_pt": round(font_pt, 1)}
    return None


# ---------------------------------------------------------------------------
# Client-side filled preview — draws the values into their boxes on the
# rendered page image. An approximation of the server's typography (the real
# fill uses reportlab), but costs no fill credits and closes the review loop.

def _preview_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSansMono.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size_px)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def draw_filled_overlay(
    image_b64: str, fields: list[dict], values: dict[str, str], page_index: int, dpi: int = 150
) -> bytes:
    """Draw field values (and checkbox ticks) on a rendered page. Returns PNG bytes."""
    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    px_per_pt = dpi / 72.0
    ink = (29, 78, 216)  # blue ballpoint

    for f in fields:
        if f.get("page_index", 0) != page_index:
            continue
        value = values.get(f["id"], "")
        x0 = f["x"] / 100.0 * w
        y0 = f["y"] / 100.0 * h
        box_w = f["w"] / 100.0 * w
        box_h = f["h"] / 100.0 * h
        draw.rectangle([x0, y0, x0 + box_w, y0 + box_h], outline=(203, 213, 225), width=1)
        if not value:
            continue

        is_checkbox = (f.get("type") == "checkbox") or (
            (f.get("fillable") or {}).get("fillableFieldType", "").lower() in ("checkbox", "radio")
        )
        if is_checkbox:
            if value.lower().strip() not in ("", "off", "false", "no", "0"):
                m = min(box_w, box_h) * 0.2
                lw = max(2, int(min(box_w, box_h) * 0.08))
                draw.line([x0 + m, y0 + m, x0 + box_w - m, y0 + box_h - m], fill=ink, width=lw)
                draw.line([x0 + m, y0 + box_h - m, x0 + box_w - m, y0 + m], fill=ink, width=lw)
            continue

        # Same auto-size rule as the server, converted to pixels at this DPI.
        box_h_pt = box_h / px_per_pt
        font_pt = min(max(box_h_pt * 0.72, 7.0), 14.0)
        font = _preview_font(max(6, int(font_pt * px_per_pt)))
        pad = 2 * px_per_pt
        max_width = max(1.0, box_w - 2 * pad)

        # Greedy word wrap using true pixel metrics.
        lines: list[str] = []
        for para in value.split("\n"):
            current = ""
            for word in para.split(" "):
                test = f"{current} {word}".strip()
                if draw.textlength(test, font=font) <= max_width or not current:
                    current = test
                else:
                    lines.append(current)
                    current = word
            lines.append(current)

        line_h = font_pt * px_per_pt * _LINE_HEIGHT
        total_h = line_h * len(lines)
        align = f.get("align") or "left"
        ty = y0 + max(0.0, (box_h - total_h) / 2.0)
        for line in lines:
            if align == "center":
                tx = x0 + (box_w - draw.textlength(line, font=font)) / 2
            elif align == "right":
                tx = x0 + box_w - pad - draw.textlength(line, font=font)
            else:
                tx = x0 + pad
            draw.text((tx, ty), line, fill=ink, font=font)
            ty += line_h

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
