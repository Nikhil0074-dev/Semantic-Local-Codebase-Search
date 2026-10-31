"""Regenerate docs/screenshots/*.png from the running UI:  python scripts/capture_screenshots.py"""
import shutil
import sys
import tempfile
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "indexing-engine"))
from playwright.sync_api import sync_playwright  # noqa: E402
from slcs.config import Settings  # noqa: E402
from slcs.server import start_background  # noqa: E402
from slcs.service import AppService  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
logging.getLogger("werkzeug").setLevel(logging.ERROR)


def main():
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "sample-repositories" / "acme-shop", tmp / "acme-shop", ignore=shutil.ignore_patterns("__pycache__"))
    s = Settings(); s.data_dir = str(tmp / "data")
    svc = AppService(s)
    rid = svc.register_repository(str(tmp / "acme-shop"))["id"]
    svc.index_blocking(rid)
    # a second repository whose folder later disappears -> demonstrates the 'folder missing' state
    ghost = tmp / "legacy-billing"; ghost.mkdir(); (ghost / "bill.py").write_text("def bill_customer(c):\n    return c\n")
    gid = svc.register_repository(str(ghost))["id"]; svc.index_blocking(gid)
    shutil.rmtree(ghost)
    url, token, stop = start_background(svc)

    def search(page, q):
        page.fill("#q", q); page.press("#q", "Enter")
        page.wait_for_selector(".result, .banner, .empty", timeout=8000); page.wait_for_timeout(250)

    def shot(page, name, full=False):
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
        print("saved", name)

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=1)
        pg.goto(url + "/#/search"); pg.wait_for_selector("#q"); pg.wait_for_timeout(300)
        shot(pg, "01-search-home")
        search(pg, "Where do we handle JWT authentication refresh loops?"); shot(pg, "02-natural-language-search")
        pg.click("text=Exact symbol"); search(pg, "refreshAccessToken"); shot(pg, "03-exact-symbol-search")
        pg.click("text=Ask in words"); pg.click(".chip:has-text('java')"); search(pg, "payment gateway callbacks"); shot(pg, "04-language-filter")
        pg.click(".chip:has-text('java')")
        search(pg, "zxqv wlkj qqqq"); shot(pg, "05-no-results")
        # stale result: edit a file after indexing
        pth = tmp / "acme-shop/backend/auth/passwords.py"; pth.write_text("# edited after indexing\n" + pth.read_text())
        search(pg, "how are user passwords stored securely"); shot(pg, "06-stale-result-warning")
        pg.goto(url + "/#/repos"); pg.wait_for_selector("section.card"); pg.wait_for_timeout(300); shot(pg, "07-repositories", full=True)
        pg.goto(url + f"/#/repos/{rid}"); pg.wait_for_selector("details"); pg.locator("details").first.locator("summary").click()
        pg.locator("details >> nth=1").locator("summary").click(); shot(pg, "08-repository-details", full=True)
        pg.goto(url + "/#/settings"); pg.wait_for_selector("form[aria-label='Settings form']"); shot(pg, "09-settings", full=True)
        # model outage: point at a local Ollama that is not running -> search degrades to keyword results with a warning
        svc.update_settings({"embedding_provider": "ollama"})
        pg.goto(url + "/#/search"); pg.wait_for_selector("#q"); search(pg, "where are API errors logged"); shot(pg, "10-embedding-model-unavailable")
        pg.goto(url + "/#/settings"); pg.wait_for_selector("form[aria-label='Settings form']"); pg.wait_for_timeout(300); shot(pg, "11-settings-model-warning", full=True)
        svc.update_settings({"embedding_provider": "hash"})
        pg.goto(url + "/#/search"); pg.wait_for_selector("#q"); search(pg, "Which functions validate user permissions before updating a record?")
        pg.locator(".result a, .result button:has-text('Open in editor')").first.focus(); shot(pg, "12-keyboard-focus")
        m = b.new_page(viewport={"width": 420, "height": 860}); m.goto(url + "/#/search"); m.wait_for_selector("#q"); search(m, "refreshAccessToken"); shot(m, "13-narrow-window")
        b.close()
    stop()


if __name__ == "__main__":
    main()
