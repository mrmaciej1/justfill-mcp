"""HTTP client for the JustFill API.

Auth, preferred: a per-user API key (`JUSTFILL_API_KEY`, format `jf_live_…`,
created at justfill.app → Account → API keys) sent as `Authorization: Bearer`.
Keys don't expire, so no refresh logic is needed.

Auth, fallback (email+password): POST /api/auth/token sets an httpOnly
`access_token` cookie whose value is the JWT itself. We lift it out of the
cookie jar and send it as a Bearer header instead — cookie-less requests
bypass the CSRF origin check (which only guards cookie-authenticated
mutations) and work from any non-browser client. Tokens are short-lived
(~30 min), so any 401 triggers a single transparent re-login + retry.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class JustFillAuthError(RuntimeError):
    pass


class JustFillApiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        self.status = status
        super().__init__(f"JustFill API error {status}: {detail}")


class JustFillClient:
    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        timeout: float = 180.0,
    ):
        self.base_url = (base_url or os.getenv("JUSTFILL_API_URL", "https://justfill.app")).rstrip("/")
        self.email = email or os.getenv("JUSTFILL_EMAIL", "")
        self.password = password or os.getenv("JUSTFILL_PASSWORD", "")
        self.api_key = api_key or os.getenv("JUSTFILL_API_KEY", "")
        if not self.api_key and not (self.email and self.password):
            # `justfill-mcp login` stores a key in ~/.config/justfill/
            from justfill_mcp.login import load_stored_api_key
            self.api_key = load_stored_api_key() or ""
        self._token: str | None = self.api_key or None
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    # ---------- auth ----------

    def _login(self) -> None:
        if self.api_key:
            # API keys don't expire — a 401 with one means it was revoked.
            raise JustFillAuthError(
                "JUSTFILL_API_KEY was rejected (revoked or invalid). "
                "Create a new key at justfill.app."
            )
        if not self.email or not self.password:
            raise JustFillAuthError(
                "Not authorized. Run `justfill-mcp login` (opens the browser), "
                "or set JUSTFILL_API_KEY, or JUSTFILL_EMAIL and JUSTFILL_PASSWORD."
            )
        resp = self._http.post(
            f"{self.base_url}/api/auth/token",
            data={"username": self.email, "password": self.password},
        )
        if resp.status_code != 200:
            raise JustFillAuthError(f"Login failed ({resp.status_code}): {resp.text[:200]}")
        token = resp.cookies.get("access_token") or self._http.cookies.get("access_token")
        if not token:
            raise JustFillAuthError("Login succeeded but no access_token cookie was returned.")
        self._token = token
        # Bearer-only from here on: cookies must NOT ride along or the CSRF
        # origin guard will 403 mutating requests from non-browser clients.
        self._http.cookies.clear()

    def _request(self, method: str, path: str, *, _retried: bool = False, **kwargs) -> httpx.Response:
        if self._token is None:
            self._login()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token}"
        resp = self._http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if resp.status_code == 401 and not _retried:
            self._token = None
            return self._request(method, path, _retried=True, headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise JustFillApiError(resp.status_code, resp.text[:500])
        return resp

    # ---------- endpoints ----------

    def render_page(self, pdf_bytes: bytes, page: int = 0, dpi: int = 150) -> dict[str, Any]:
        """POST /api/pdfs/render -> {imageBase64, width, height, pageCount}"""
        resp = self._request(
            "POST",
            f"/api/pdfs/render?page={page}&dpi={dpi}",
            files={"file": ("document.pdf", pdf_bytes, "application/pdf")},
        )
        return resp.json()

    def detect_fillable(self, pdf_bytes: bytes) -> dict[str, Any]:
        """POST /api/analyze/detect-fillable -> {isFillable, fields, pageCount, warnings}"""
        resp = self._request(
            "POST",
            "/api/analyze/detect-fillable",
            files={"pdf_file": ("document.pdf", pdf_bytes, "application/pdf")},
        )
        return resp.json()

    # 200 DPI matches the detector's training resolution — same default the web
    # UI uses. Measured recall 0.944 @200 vs 0.917 @300 (test_dpi_recall.py);
    # rendering above the training resolution hurts.
    def detect_fields_batch(self, pdf_b64: str, pages: list[int], dpi: int = 200) -> dict[str, Any]:
        """POST /api/detect-fields/batch -> {results: [{pageIndex, imageWidth, imageHeight, fields}], creditsCharged}"""
        resp = self._request(
            "POST",
            "/api/detect-fields/batch",
            json={"pdfBase64": pdf_b64, "pages": pages, "dpi": dpi},
        )
        return resp.json()

    def calibrations_by_hash(self, content_hash: str, include_others: bool = True) -> list[dict[str, Any]]:
        """GET /api/calibrations/by-hash/{hash} -> own calibrations + published templates."""
        resp = self._request(
            "GET",
            f"/api/calibrations/by-hash/{content_hash}?include_others={'true' if include_others else 'false'}",
        )
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def save_calibration(
        self,
        document_id: str,
        content_hash: str,
        name: str,
        fields: list[dict[str, Any]],
        pdf_bytes: bytes | None,
    ) -> dict[str, Any]:
        """PUT /api/calibrations/{document_id} (multipart upsert)."""
        import json as _json

        calibration = {
            "documentId": document_id,
            "contentHash": content_hash,
            "documentName": name,
            "displayName": name,
            "fields": fields,
        }
        files: dict[str, Any] = {"calibration_data": (None, _json.dumps(calibration))}
        if pdf_bytes is not None:
            files["pdf_file"] = ("document.pdf", pdf_bytes, "application/pdf")
        resp = self._request("PUT", f"/api/calibrations/{document_id}", files=files)
        return resp.json()

    def list_calibrations(self, limit: int = 50) -> dict[str, Any]:
        resp = self._request("GET", f"/api/calibrations?limit={limit}")
        return resp.json()

    def generate_pdf(
        self,
        pdf_bytes: bytes,
        fields_json: str,
        flatten: bool = True,
    ) -> tuple[bytes, str]:
        """POST /api/generate/pdf -> (filled PDF bytes, output_mode).

        output_mode mirrors the X-Output-Mode header: "clean", or
        "watermarked" when the account's free fills are used up — the agent
        must be told, or it silently delivers a watermarked document.
        """
        resp = self._request(
            "POST",
            "/api/generate/pdf",
            files={"pdf_file": ("document.pdf", pdf_bytes, "application/pdf")},
            data={"fields_json": fields_json, "flatten": "true" if flatten else "false"},
        )
        return resp.content, resp.headers.get("X-Output-Mode", "clean")
