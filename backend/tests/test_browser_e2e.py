"""Browser end-to-end test of the main workflow (Playwright, Chromium).

upload demo -> clarify -> preview (8%) -> revise to 5% -> apply -> export -> restore.

Runs against E2E_BASE_URL when set (e.g. the Docker stack at http://127.0.0.1:8080).
Otherwise it starts the backend in-process with the real LibreOffice worker and serves
the built frontend (frontend/dist) on the same origin. Skipped if Playwright's browser,
LibreOffice (local mode), or the frontend build is missing.
"""

from __future__ import annotations

import io
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tests.conftest import HAS_SOFFICE, make_settings

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"
pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def base_url(tmp_path):
    external = os.environ.get("E2E_BASE_URL")
    if external:
        yield external.rstrip("/")
        return
    if not HAS_SOFFICE or not (DIST / "index.html").exists():
        pytest.skip("needs LibreOffice and a built frontend (npm run build)")
    import uvicorn
    from fastapi.staticfiles import StaticFiles

    from app.main import create_app

    port = _free_port()
    app = create_app(make_settings(tmp_path, allowed_origin=f"http://127.0.0.1:{port}"))
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")  # same origin, as the gateway does
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def test_main_workflow_in_browser(base_url, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(accept_downloads=True)
        try:
            _workflow(page, base_url)
        except Exception:
            shot = Path(os.environ.get("E2E_ARTIFACTS", tmp_path)) / "failure.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"screenshot saved to {shot}")
            raise
        finally:
            browser.close()


def _workflow(page, base_url):
    from playwright.sync_api import expect

    if True:
        page.on("dialog", lambda d: d.accept())  # confirmation dialogs (restore)
        page.goto(base_url)

        page.get_by_role("button", name="Load synthetic demo workbook").click()
        chat = page.locator("#chat-input")
        expect(chat).to_be_visible(timeout=60_000)

        chat.fill("Increase labor costs by 8%, keep prices unchanged, and tell me which products lose money.")
        page.get_by_role("button", name="Send").click()
        choice = page.get_by_role("button", name="Hourly wages only, for manufacturing")
        expect(choice).to_be_visible(timeout=30_000)
        choice.click()

        apply = page.get_by_role("button", name="Apply as new version")
        expect(apply).to_be_enabled(timeout=60_000)
        expect(page.get_by_text("Turned negative").first).to_be_visible()
        expect(page.get_by_text("Walnut Side Table").first).to_be_visible()
        expect(page.get_by_text("$34.56").first).to_be_visible()

        chat.fill("Actually make that 5%.")
        page.get_by_role("button", name="Send").click()
        expect(page.get_by_text("$33.60").first).to_be_visible(timeout=60_000)
        expect(apply).to_be_enabled(timeout=60_000)
        apply.click()

        history = page.locator(".history-item.active")
        expect(history).to_contain_text("Version 2", timeout=30_000)

        with page.expect_download() as download_info:
            page.get_by_label("Download version 2").click()
        exported = download_info.value.path().read_bytes()
        wb = load_workbook(io.BytesIO(exported))
        assert wb["Inputs"]["B4"].value == pytest.approx(33.6)
        assert wb["Products"]["N5"].value == "=G5-M5"

        page.locator(".history-item", has_text="Version 1").get_by_role("button", name="Restore").click()
        expect(page.locator(".history-item.active")).to_contain_text("Version 1", timeout=30_000)
