"""Visit the hosted demo so Streamlit Community Cloud does not put it to sleep.

Community Cloud hibernates any app without traffic for 12 hours; a visitor then sees a
"Yes, get this app back up!" button and waits for a cold start. A real browser visit counts as
traffic, so this runs on a schedule (.github/workflows/keep-demo-awake.yml). If the demo is
already asleep it presses the button. It fails if the login screen never appears, so a broken
demo shows up as a failed workflow run.
"""

import sys
import time

from playwright.sync_api import sync_playwright

URL = "https://mathexaminer-ai-demo.streamlit.app/"
WAKE_BUTTON = "Yes, get this app back up!"
READY_TEXT = "CIE A-Level"  # tagline on the app's login screen
TIMEOUT_S = 300


def app_is_ready(page) -> bool:
    # The app is served inside an iframe on the streamlit.app page, so look in every frame.
    for frame in page.frames:
        try:
            if frame.get_by_text(READY_TEXT).count():
                return True
        except Exception:
            pass  # frame navigated away while we looked
    return False


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        deadline = time.monotonic() + TIMEOUT_S
        woke = False
        while time.monotonic() < deadline:
            if app_is_ready(page):
                print("demo is up" + (" (it was asleep; woken)" if woke else ""))
                browser.close()
                return 0
            wake = page.get_by_role("button", name=WAKE_BUTTON)
            if not woke and wake.count():
                print("demo was asleep; pressing the wake-up button")
                wake.first.click()
                woke = True
            page.wait_for_timeout(5000)
        print(f"demo did not show its login screen within {TIMEOUT_S} s", file=sys.stderr)
        page.screenshot(path="demo-failure.png", full_page=True)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
