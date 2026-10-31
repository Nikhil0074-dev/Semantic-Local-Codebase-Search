"""Browser tests (Playwright + Chromium). Skipped automatically when a browser is not available."""
import pytest

pw = pytest.importorskip("playwright.sync_api")

from slcs.server import start_background  # noqa: E402


@pytest.fixture
def page_env(indexed):
    svc, rid, repo = indexed
    url, token, stop = start_background(svc)
    try:
        p = pw.sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as exc:                                    # pragma: no cover - environment dependent
        stop()
        pytest.skip(f"Chromium unavailable: {exc}")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    yield page, url, svc, rid, repo, requests, errors
    browser.close(); p.stop(); stop()


def search(page, q):
    page.fill("#q", q)
    page.press("#q", "Enter")
    page.wait_for_selector(".result, .banner, .empty", timeout=5000)


def test_page_makes_no_external_requests_and_no_console_errors(page_env):
    page, url, *_, requests, errors = page_env
    page.goto(url + "/#/search")
    search(page, "refreshAccessToken")
    page.goto(url + "/#/repos"); page.wait_for_selector(".card")
    page.goto(url + "/#/settings"); page.wait_for_selector("form")
    assert all(r.startswith(url) or r.startswith("data:") for r in requests), [r for r in requests if not r.startswith(url)]
    assert errors == []


def test_search_renders_real_lines_highlights_and_evidence(page_env):
    page, url, *_ = page_env
    page.goto(url + "/#/search")
    search(page, "Where are API errors logged?")
    first = page.locator(".result").first
    assert "log_api_error" in first.inner_text()
    assert first.locator(".line .no").first.inner_text().strip().isdigit()
    assert first.locator("mark").count() > 0
    assert "backend/logging_utils.py" in first.locator(".loc").inner_text()
    assert first.locator(".rail .pip.on").count() >= 1


def test_exact_symbol_mode(page_env):
    page, url, *_ = page_env
    page.goto(url + "/#/search")
    page.click("text=Exact symbol")
    search(page, "ReserveStock")
    assert "StockService.ReserveStock" in page.locator(".result").first.inner_text()


def test_keyboard_shortcut_and_focus_visibility(page_env):
    page, url, *_ = page_env
    page.goto(url + "/#/search"); page.wait_for_selector("#q")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "q"
    search(page, "payment gateway")
    page.keyboard.press("Tab")
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline != "none"


def test_hostile_source_code_is_rendered_as_text_not_executed(page_env):
    page, url, svc, rid, repo, *_ = page_env
    (repo / "backend" / "evil.py").write_text(
        'def xss_probe():\n    return "<img src=x onerror=window.__pwned=1><script>window.__pwned=2</script>"\n')
    svc.index_blocking(rid)
    page.goto(url + "/#/search")
    search(page, "xss_probe")
    assert "<img src=x onerror" in page.locator(".result").first.inner_text()      # visible as text
    assert page.evaluate("window.__pwned") is None                                  # never executed


def test_stale_result_is_flagged(page_env):
    page, url, svc, rid, repo, *_ = page_env
    p = repo / "backend" / "auth" / "passwords.py"
    p.write_text("# changed\n" + p.read_text())
    page.goto(url + "/#/search")
    search(page, "hash_password")
    assert "file changed" in page.locator(".result").first.inner_text()


def test_open_button_reports_missing_editor_without_crashing(page_env, monkeypatch):
    page, url, *_ = page_env
    monkeypatch.setattr("slcs.navigation.shutil.which", lambda n: None)
    page.goto(url + "/#/search"); search(page, "log_api_error")
    page.click("text=Open in editor")
    page.wait_for_selector(".toast.err")
    assert "PATH" in page.locator("#toast").inner_text()


def test_add_repository_flow_and_validation_error(page_env, tmp_path):
    page, url, *_ = page_env
    page.goto(url + "/#/repos"); page.wait_for_selector("#new-path")
    page.fill("#new-path", "/definitely/missing")
    page.click("text=Add and index")
    page.wait_for_selector(".toast.err")
    d = tmp_path / "another"; d.mkdir(); (d / "m.py").write_text("def another_fn():\n    return 1\n")
    page.fill("#new-path", str(d)); page.click("text=Add and index")
    page.wait_for_selector("text=another", timeout=5000)


def test_remove_requires_confirmation_and_keeps_source(page_env):
    page, url, svc, rid, repo, *_ = page_env
    page.goto(url + "/#/repos"); page.wait_for_selector(".card")
    page.click("section.card button:has-text('Remove')")
    page.wait_for_function("document.querySelector('#confirm').open")
    assert "source files are not touched" in page.locator("dialog").inner_text().lower()
    page.click("dialog >> text=Cancel")
    assert len(svc.list_repositories()) == 1
    page.click("section.card button:has-text('Remove')"); page.click("#confirm-ok")
    page.wait_for_selector("text=No repositories yet")
    assert (repo / "backend/logging_utils.py").exists()


def test_no_horizontal_overflow_in_narrow_window(page_env):
    page, url, *_ = page_env
    page.set_viewport_size({"width": 400, "height": 800})
    page.goto(url + "/#/search"); search(page, "refreshAccessToken")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
