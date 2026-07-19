"""Regression tests for tool-level workspace state in server.py."""

import base64
import io
import json

import justfill_mcp.server as srv
from PIL import Image


class FakeClient:
    """Stubs the HTTP client; template hit for hash of PDF_B only."""

    def __init__(self, template_hash=""):
        self.template_hash = template_hash

    def calibrations_by_hash(self, h):
        if h == self.template_hash:
            return [{
                "displayName": "saved tpl",
                "fields": [{"id": "T1", "name": "Name", "box_2d": [100, 100, 150, 500]}],
            }]
        return []

    def detect_fillable(self, pdf_bytes):
        return {"isFillable": False, "fields": [], "pageCount": 1}

    def detect_fields_batch(self, b64, pages):
        return {"results": [{
            "pageIndex": 0, "imageWidth": 1000, "imageHeight": 1000,
            "fields": [{"id": "M1", "x0": 100, "y0": 100, "x1": 500, "y1": 150, "score": 0.9}],
        }]}

    def generate_pdf(self, pdf_bytes, fields_json, flatten=True):
        self.last_fields = json.loads(fields_json)
        return b"%PDF-1.4 filled", "clean"

    def render_page(self, pdf_bytes, page=0, dpi=150):
        buf = io.BytesIO()
        Image.new("RGB", (850, 1100), "white").save(buf, format="PNG")
        return {"imageBase64": base64.b64encode(buf.getvalue()).decode(), "pageCount": 1}


def test_open_pdf_template_path_replaces_workspace(tmp_path, monkeypatch):
    """Bug found in prod E2E 2026-07-02: the template branch returned without
    _set_ws, so edits/fills silently kept operating on the previously opened PDF."""
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_a.write_bytes(b"%PDF-1.4 doc A")
    pdf_b.write_bytes(b"%PDF-1.4 doc B")

    from justfill_mcp.workspace import content_hash
    monkeypatch.setattr(srv, "_client", FakeClient(content_hash(pdf_b.read_bytes())))
    monkeypatch.setattr(srv, "_ws", None)

    srv.open_pdf(str(pdf_a))                     # ML path
    assert srv._ws.hash == content_hash(pdf_a.read_bytes())

    out = json.loads(srv.open_pdf(str(pdf_b)))   # template path
    assert out["summary"]["source"] == "template"
    assert srv._ws.hash == content_hash(pdf_b.read_bytes()), \
        "template branch must swap the active workspace"
    assert srv._ws.fields[0]["id"] == "T1"


def _open_ml_pdf(tmp_path, monkeypatch):
    """Open a real (image-derived) one-page PDF through the ML path."""
    img_path = tmp_path / "scan.png"
    Image.new("RGB", (850, 1100), "white").save(img_path)
    monkeypatch.setattr(srv, "_client", FakeClient())
    monkeypatch.setattr(srv, "_ws", None)
    return json.loads(srv.open_pdf(str(img_path)))


def test_open_pdf_accepts_image_and_is_deterministic(tmp_path, monkeypatch):
    out = _open_ml_pdf(tmp_path, monkeypatch)
    assert out["summary"]["converted_from_image"] is True
    assert out["summary"]["source"] == "ml"
    first_hash = out["summary"]["content_hash"]
    out2 = _open_ml_pdf(tmp_path, monkeypatch)
    assert out2["summary"]["content_hash"] == first_hash, \
        "image->PDF conversion must be deterministic or templates never match"


def test_open_pdf_rejects_garbage_with_clear_error(tmp_path, monkeypatch):
    bad = tmp_path / "notes.txt"
    bad.write_bytes(b"just some text")
    monkeypatch.setattr(srv, "_client", FakeClient())
    out = json.loads(srv.open_pdf(str(bad)))
    assert "neither a PDF nor a readable image" in out["error"]


def test_close_workspace_releases_memory_and_is_idempotent(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)

    assert json.loads(srv.close_workspace()) == {
        "closed": True,
        "already_closed": False,
    }
    assert srv._ws is None
    assert json.loads(srv.close_workspace()) == {
        "closed": False,
        "already_closed": True,
    }


def test_force_detect_skips_template(tmp_path, monkeypatch):
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.4 doc B")
    from justfill_mcp.workspace import content_hash
    monkeypatch.setattr(srv, "_client", FakeClient(content_hash(pdf.read_bytes())))
    monkeypatch.setattr(srv, "_ws", None)

    assert json.loads(srv.open_pdf(str(pdf)))["summary"]["source"] == "template"
    out = json.loads(srv.open_pdf(str(pdf), force_detect=True))
    assert out["summary"]["source"] == "ml"


def test_add_field_sequential_ids_and_align(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)
    f1 = json.loads(srv.add_field(x=10, y=10, w=20, h=3))
    f2 = json.loads(srv.add_field(x=10, y=20, w=20, h=3, align="right", vertical_align="top"))
    f3 = json.loads(srv.add_field(x=10, y=30, w=20, h=3, page_index=1))
    assert (f1["id"], f2["id"], f3["id"]) == ("p0_a1", "p0_a2", "p1_a1")
    assert f2["align"] == "right" and f2["vertical_align"] == "top"
    bad = json.loads(srv.add_field(x=1, y=1, w=1, h=1, align="justify"))
    assert "align must be one of" in bad["error"]


def test_batch_update_and_remove(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)
    ids = [json.loads(srv.add_field(x=10, y=10 * i, w=20, h=3))["id"] for i in range(1, 4)]
    out = json.loads(srv.update_fields([
        {"field_id": ids[0], "name": "first", "align": "center"},
        {"field_id": "nope", "name": "ghost"},
        {"field_id": ids[1], "x": 55.5},
    ]))
    assert out["updated"] == [ids[0], ids[1]]
    assert out["unknown_field_ids"] == ["nope"]
    assert srv._ws.get_field(ids[0])["align"] == "center"
    assert srv._ws.get_field(ids[1])["x"] == 55.5

    out = json.loads(srv.remove_fields([ids[0], ids[2], "nope"]))
    assert out["removed"] == 2
    assert out["unknown_field_ids"] == ["nope"]


def test_prune_fields(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)
    ws = srv._ws
    ws.fields = [
        {"id": f"p0_f{i}", "name": "", "x": 1.0 * i, "y": 5, "w": 1.2, "h": 2,
         "page_index": 0, "type": "cell", "confidence": 0.75, "source": "ml", "fillable": None}
        for i in range(20)
    ] + [
        {"id": "p0_f99", "name": "real", "x": 10, "y": 50, "w": 30, "h": 3,
         "page_index": 0, "type": "field", "confidence": 0.75, "source": "ml", "fillable": None},
    ]
    err = json.loads(srv.prune_fields())
    assert "at least one criterion" in err["error"]

    out = json.loads(srv.prune_fields(field_type="cell", width_below=5, exclude_ids=["p0_f0"]))
    assert out["removed"] == 19
    assert out["fields_remaining"] == 2
    remaining = {f["id"] for f in ws.fields}
    assert remaining == {"p0_f0", "p0_f99"}


def test_fill_pdf_align_mapping_and_overflow_warning(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)
    fid = json.loads(srv.add_field(x=10, y=10, w=6, h=2, name="tiny", align="right"))["id"]
    out_path = tmp_path / "out.pdf"
    values = {
        fid: "a very long value that cannot possibly fit in such a narrow box, repeated "
             "a very long value that cannot possibly fit in such a narrow box"
    }
    blocked = json.loads(srv.fill_pdf(values, str(out_path)))
    assert {item["tool"] for item in blocked["required_actions"]} == {
        "render_preview", "render_filled_preview",
    }
    srv.render_preview()
    srv.render_filled_preview(values)
    out = json.loads(srv.fill_pdf(values, str(out_path)))
    assert out["saved"] == str(out_path)
    assert any(fid in w for w in out["warnings"])
    sent = srv._client.last_fields
    assert sent[0]["textAlign"] == "right"


def test_render_filled_preview_no_fill_consumed(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)
    ws = srv._ws
    buf = io.BytesIO()
    Image.new("RGB", (850, 1100), "white").save(buf, format="PNG")

    calls = []

    class RenderClient(FakeClient):
        def render_page(self, pdf_bytes, page=0, dpi=150):
            calls.append("render")
            return {"imageBase64": base64.b64encode(buf.getvalue()).decode(), "pageCount": 1}

        def generate_pdf(self, *a, **k):
            raise AssertionError("preview must not call generate_pdf")

    monkeypatch.setattr(srv, "_client", RenderClient())
    fid = json.loads(srv.add_field(x=10, y=10, w=40, h=3, name="n"))["id"]
    cb = json.loads(srv.add_field(x=60, y=10, w=3, h=3, field_type="checkbox"))["id"]
    img = srv.render_filled_preview({fid: "John Smith", cb: "yes"})
    assert img.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert calls == ["render"]
    assert ws.fields  # workspace untouched


def test_ml_review_receipt_is_exact_and_invalidated_by_geometry(tmp_path, monkeypatch):
    _open_ml_pdf(tmp_path, monkeypatch)
    values = {"M1": "Alice"}
    output = tmp_path / "filled.pdf"

    srv.render_preview(0)
    srv.render_filled_preview(values, 0)
    assert "saved" in json.loads(srv.fill_pdf(values, str(output)))

    changed = json.loads(srv.fill_pdf({"M1": "Bob"}, str(output)))
    assert [action["tool"] for action in changed["required_actions"]] == [
        "render_filled_preview"
    ]

    srv.update_field("M1", x=11.0)
    stale = json.loads(srv.fill_pdf(values, str(output)))
    assert {action["tool"] for action in stale["required_actions"]} == {
        "render_preview", "render_filled_preview",
    }


def test_deterministic_template_does_not_require_visual_review(tmp_path, monkeypatch):
    pdf = tmp_path / "template.pdf"
    pdf.write_bytes(b"%PDF-1.4 template")
    from justfill_mcp.workspace import content_hash

    monkeypatch.setattr(srv, "_client", FakeClient(content_hash(pdf.read_bytes())))
    monkeypatch.setattr(srv, "_ws", None)
    srv.open_pdf(str(pdf))
    out = json.loads(srv.fill_pdf({"T1": "Alice"}, str(tmp_path / "out.pdf")))
    assert out["filled_fields"] == 1
