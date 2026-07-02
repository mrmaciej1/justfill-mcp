"""Unit tests for coordinate normalization and payload mapping."""

import base64
import io

from PIL import Image

from justfill_mcp.workspace import (
    Workspace,
    calibrate_ml_score,
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


def test_content_hash_matches_backend_convention():
    # backend: hashlib.sha256(pdf_bytes).hexdigest()[:32]
    h = content_hash(b"%PDF-1.4 test")
    assert len(h) == 32
    assert h == content_hash(b"%PDF-1.4 test")  # deterministic


def test_field_from_box2d_scales_1000_to_percent():
    raw = {"id": "T1", "name": "SSN", "box_2d": [100, 250, 150, 750], "pageIndex": 2}
    f = field_from_box2d(raw, "acroform")
    assert (f["x"], f["y"], f["w"], f["h"]) == (25.0, 10.0, 50.0, 5.0)
    assert f["page_index"] == 2
    assert f["confidence"] == 1.0
    assert f["source"] == "acroform"


def test_field_from_box2d_carries_fillable_keys():
    raw = {
        "id": "T2", "name": "agree", "box_2d": [0, 0, 100, 100],
        "fillableFieldName": "chk1", "fillableFieldType": "checkbox",
        "fillableExportValue": "Yes",
    }
    f = field_from_box2d(raw, "template")
    assert f["fillable"]["fillableFieldName"] == "chk1"
    assert f["fillable"]["fillableExportValue"] == "Yes"


def test_field_from_box2d_accepts_snake_case():
    raw = {
        "id": "T3", "name": "x", "box_2d": [0, 0, 10, 10],
        "fillable_field_name": "txt1", "page_index": 1,
    }
    f = field_from_box2d(raw, "acroform")
    assert f["fillable"]["fillableFieldName"] == "txt1"
    assert f["page_index"] == 1


def test_field_from_pixels_normalizes_to_percent():
    raw = {"id": "D1", "x0": 127.5, "y0": 165.0, "x1": 382.5, "y1": 198.0, "score": 0.83, "type": "field"}
    f = field_from_pixels(raw, img_w=1275, img_h=1650, page_index=0)
    assert (f["x"], f["y"], f["w"], f["h"]) == (10.0, 10.0, 20.0, 2.0)
    assert f["confidence"] == 0.95  # calibrated: >=0.5 raw caps at 0.95
    assert f["raw_score"] == 0.83
    assert f["source"] == "ml"


def test_ml_score_calibration_matches_server_filter_semantics():
    # Anchors: ~0.03 = bucketed-threshold accept (borderline), 0.15 =
    # LOOSE_CONF_RESCUE (accepted on score alone), 0.5+ = cap below 1.0.
    assert calibrate_ml_score(0.0) == 0.0
    assert calibrate_ml_score(0.03) == 0.4
    assert calibrate_ml_score(0.15) == 0.75
    assert calibrate_ml_score(0.5) == 0.95
    assert calibrate_ml_score(0.99) == 0.95
    # Typical prod scores on flat forms (observed 0.1-0.2) land orange/green,
    # not "red, wiped by min_confidence=0.3" like the raw scale did.
    assert 0.6 < calibrate_ml_score(0.10) < 0.75
    assert calibrate_ml_score(0.17) > 0.75


def test_roundtrip_box2d_to_calibration():
    raw = {"id": "T1", "name": "SSN", "box_2d": [100, 250, 150, 750]}
    f = field_from_box2d(raw, "template")
    cal = field_to_calibration(f)
    assert cal["box_2d"] == [100, 250, 150, 750]
    assert cal["name"] == "SSN"


def test_field_to_generation_maps_camel_case():
    f = field_from_box2d(
        {"id": "T1", "name": "SSN", "box_2d": [100, 250, 150, 750],
         "fillableFieldName": "ssn_1", "fillableFieldType": "text"},
        "acroform",
    )
    g = field_to_generation(f, "123-45-6789")
    assert g["value"] == "123-45-6789"
    assert g["pageIndex"] == 0
    assert g["fontSize"] == 0            # auto
    assert g["isCalibrated"] is True     # deterministic source
    assert g["fillableFieldName"] == "ssn_1"


def test_ml_checkbox_maps_to_x_cross_rendering():
    # UI parity: D-FINE type=='checkbox' must reach the generator as
    # fillableFieldType='checkbox' so it draws an X instead of text.
    raw = {"id": "C1", "x0": 0, "y0": 0, "x1": 20, "y1": 20, "score": 0.2, "type": "checkbox"}
    f = field_from_pixels(raw, img_w=1000, img_h=1000, page_index=0)
    g = field_to_generation(f, "yes")
    assert g["fillableFieldType"] == "checkbox"
    # ...but a plain text field must not get the checkbox treatment
    raw["type"] = "field"
    g2 = field_to_generation(field_from_pixels(raw, 1000, 1000, 0), "yes")
    assert "fillableFieldType" not in g2


def test_workspace_get_field_and_summary():
    ws = Workspace(pdf_path="a.pdf", hash="h" * 32, page_count=2)
    ws.fields = [
        field_from_box2d({"id": "A", "name": "n", "box_2d": [0, 0, 10, 10], "pageIndex": 0}, "ml"),
        field_from_box2d({"id": "B", "name": "n", "box_2d": [0, 0, 10, 10], "pageIndex": 1}, "ml"),
    ]
    assert ws.get_field("B")["page_index"] == 1
    s = ws.summary()
    assert s["total_fields"] == 2
    assert s["fields_per_page"] == {0: 1, 1: 1}


def test_draw_overlay_produces_png():
    img = Image.new("RGB", (200, 300), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    fields = [
        {"id": "F1", "name": "Name", "x": 10, "y": 10, "w": 40, "h": 5,
         "page_index": 0, "confidence": 0.9, "source": "ml"},
        {"id": "F2", "name": "", "x": 10, "y": 30, "w": 40, "h": 5,
         "page_index": 0, "confidence": 1.0, "source": "acroform"},
        {"id": "F3", "name": "other page", "x": 0, "y": 0, "w": 10, "h": 5,
         "page_index": 1, "confidence": 0.2, "source": "ml"},
    ]
    png = draw_overlay(b64, fields, page_index=0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    out = Image.open(io.BytesIO(png))
    assert out.size == (200, 300)


def test_align_roundtrips_through_calibration_and_generation():
    raw = {"id": "T1", "name": "email", "box_2d": [100, 250, 150, 750],
           "textAlign": "right", "verticalAlign": "top"}
    f = field_from_box2d(raw, "template")
    assert f["align"] == "right" and f["vertical_align"] == "top"
    g = field_to_generation(f, "a@b.c")
    assert g["textAlign"] == "right" and g["verticalAlign"] == "top"
    cal = field_to_calibration(f)
    assert cal["textAlign"] == "right" and cal["verticalAlign"] == "top"
    # absent -> keys omitted, server defaults apply (left / middle)
    f2 = field_from_box2d({"id": "T2", "name": "n", "box_2d": [0, 0, 10, 10]}, "template")
    g2 = field_to_generation(f2, "v")
    assert "textAlign" not in g2 and "verticalAlign" not in g2


def test_pdf_from_image_is_deterministic_and_parseable():
    img = Image.new("RGB", (1241, 1754), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    pdf1 = pdf_from_image(buf.getvalue())
    pdf2 = pdf_from_image(buf.getvalue())
    assert pdf1 == pdf2, "same image must yield byte-identical PDF (template hash stability)"
    assert pdf1.startswith(b"%PDF-")
    (w, h), = page_sizes_pt(pdf1)
    # scaled to ~A4: long side pinned to 11.69in = 841.68pt
    assert abs(h - 841.7) < 2.0
    assert 0.69 < w / h < 0.72  # aspect preserved


def test_estimate_fit_counts_cjk_as_fullwidth():
    # 20 Han chars ~ 20em; the same count of Latin chars is ~12em. A box wide
    # enough for the Latin string must still warn for the CJK one.
    cjk = "東京都渋谷区神南一丁目二十三番地の四五六七"
    latin = "a" * 20
    box_w, box_h = 130, 12  # pt
    assert estimate_fit(latin, box_w, box_h) is None
    assert estimate_fit(cjk, box_w, box_h) is not None


def test_estimate_fit_mirrors_server_sizing():
    # generous box: no warning
    assert estimate_fit("John Smith", box_w_pt=200, box_h_pt=20) is None
    # narrow box: fits only after shrinking below the natural size
    shrunk = estimate_fit("a moderately long value here", box_w_pt=80, box_h_pt=14)
    assert shrunk["result"] == "shrunk"
    assert 4.0 <= shrunk["font_pt"] < 14.0
    # hopeless box: truncated even at the 4pt floor
    assert estimate_fit("x" * 500, box_w_pt=30, box_h_pt=10) == {"result": "truncated"}
    assert estimate_fit("   ", box_w_pt=5, box_h_pt=5) is None


def test_draw_filled_overlay_renders_values_and_checkbox():
    img = Image.new("RGB", (400, 500), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    fields = [
        {"id": "F1", "name": "Name", "x": 10, "y": 10, "w": 60, "h": 4,
         "page_index": 0, "confidence": 0.9, "source": "ml", "type": "field"},
        {"id": "C1", "name": "", "x": 80, "y": 10, "w": 5, "h": 4,
         "page_index": 0, "confidence": 0.9, "source": "ml", "type": "checkbox"},
    ]
    png = draw_filled_overlay(b64, fields, {"F1": "Jane Doe", "C1": "yes"}, page_index=0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    out = Image.open(io.BytesIO(png)).convert("RGB")
    assert out.size == (400, 500)
    # ink must have landed: the page is no longer pure white
    colors = out.getcolors(maxcolors=1_000_000)
    assert any(c != (255, 255, 255) for _, c in colors)
