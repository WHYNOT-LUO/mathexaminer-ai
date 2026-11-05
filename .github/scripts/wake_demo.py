"""Visit both hosted copies of the demo so they stay awake, and check that they load.

The demo runs on Render (the main link) and on Streamlit Community Cloud. Both put a free app to
sleep after a while without traffic; a real browser visit counts as traffic, so this runs on a
schedule (.github/workflows/keep-demo-awake.yml). On Streamlit it presses the "Yes, get this app
back up!" button if the app is asleep, and since Streamlit's page sometimes stalls on its loading
spinner, a stalled page is reloaded as a person would.

The run fails only if the Render copy does not show its login screen, so a failed workflow run
means the main demo is broken. Streamlit's result is logged, with how reliably its server answered:
on 3-4 Nov 2026 Community Cloud dropped about one request in six to this account's apps.
"""

import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright

RENDER_URL = "https://mathexaminer-ai-demo.onrender.com/"
STREAMLIT_URL = "https://mathexaminer-ai-demo.streamlit.app/"
WAKE_BUTTON = "Yes, get this app back up!"
READY_TEXT = "CIE A-Level"  # tagline on the app's login screen
RENDER_TIMEOUT_S = 240  # a Render free instance takes about a minute to wake
STREAMLIT_TIMEOUT_S = 420  # the job limit is 15 minutes for both visits together
RELOAD_AFTER_S = 90  # a stalled spinner is reloaded after this long (not while a woken app starts)


def app_is_ready(page) -> bool:
    # On streamlit.app the app is served inside an iframe, so look in every frame.
    for frame in page.frames:
        try:
            if frame.get_by_text(READY_TEXT).count():
                return True
        except Exception:
            pass  # frame navigated away while we looked
    return False


def _health_ok(url: str) -> tuple[bool, float]:
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=70) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    return ok, time.monotonic() - start


def report_server_health(name: str, health_url: str, n: int = 30) -> None:
    """Log how reliably a server answers, so a flaky host shows up in the run log."""
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_health_ok, [health_url] * n))
    failed = [t for ok, t in results if not ok]
    slowest = max(t for _, t in results)
    print(f"{name} server health: {n - len(failed)}/{n} answered, slowest {slowest:.1f} s")


def visit(browser, name: str, url: str, timeout_s: int, screenshot: str | None = None) -> bool:
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    except Exception as exc:
        print(f"{name}: could not open the page ({exc})")
        page.close()
        return False
    deadline = time.monotonic() + timeout_s
    loaded_at = time.monotonic()
    woke = False
    reloads = 0
    while time.monotonic() < deadline:
        if app_is_ready(page):
            print(f"{name}: demo is up" + (" (it was asleep; woken)" if woke else "")
                  + (f" after {reloads} reload(s)" if reloads else ""))
            page.close()
            return True
        wake = page.get_by_role("button", name=WAKE_BUTTON)
        if not woke and wake.count():
            print(f"{name}: demo was asleep; pressing the wake-up button")
            wake.first.click()
            woke = True
        elif not woke and time.monotonic() - loaded_at > RELOAD_AFTER_S:
            print(f"{name}: page stalled while loading; reloading")
            page.reload(wait_until="domcontentloaded", timeout=60_000)
            loaded_at = time.monotonic()
            reloads += 1
        page.wait_for_timeout(5000)
    print(f"{name}: demo did not show its login screen within {timeout_s} s", file=sys.stderr)
    if screenshot:
        page.screenshot(path=screenshot, full_page=True)
    page.close()
    return False


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        render_ok = visit(browser, "Render", RENDER_URL, RENDER_TIMEOUT_S, screenshot="demo-failure.png")
        report_server_health("Render", RENDER_URL + "_stcore/health")
        report_server_health("Streamlit", STREAMLIT_URL + "~/+/_stcore/health")
        visit(browser, "Streamlit", STREAMLIT_URL, STREAMLIT_TIMEOUT_S)
        browser.close()
    return 0 if render_ok else 1


if __name__ == "__main__":
    sys.exit(main())
