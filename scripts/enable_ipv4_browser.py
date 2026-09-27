"""Try to enable Supabase dedicated IPv4 via an existing browser session."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REF = "aulgiuinmlbikwyrrjur"
OUT = Path(__file__).resolve().parents[1] / "database" / "ipv4_attempt"
OUT.mkdir(parents=True, exist_ok=True)

CANDIDATE_URLS = [
    f"https://supabase.com/dashboard/project/{REF}/settings/infrastructure",
    f"https://supabase.com/dashboard/project/{REF}/settings",
    f"https://supabase.com/dashboard/project/{REF}",
]


def main() -> int:
    chrome_candidates = [
        Path.home() / "AppData/Local/Google/Chrome/User Data",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    user_data = None
    for c in chrome_candidates:
        if c.is_dir() and (c / "Default").exists():
            user_data = str(c)
            break

    with sync_playwright() as p:
        context_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "channel": "chrome",
        }
        # Persistent profile to reuse Supabase login if Chrome not locking profile
        if user_data:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(OUT / "profile"),
                    headless=False,
                    **{k: v for k, v in context_kwargs.items() if k != "channel"},
                    executable_path=None,
                    args=[f"--profile-directory=Default"],
                )
                # Note: cannot open live profile while Chrome runs; use fresh + ask login if needed
            except Exception:
                context = None
        else:
            context = None

        if context is None:
            browser = p.chromium.launch(channel="chrome", headless=False)
            context = browser.new_context(viewport={"width": 1440, "height": 900})

        page = context.pages[0] if context.pages else context.new_page()
        for url in CANDIDATE_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT / f"step_{CANDIDATE_URLS.index(url)}.png"), full_page=True)
                title = page.title()
                body = page.inner_text("body")[:500]
                print(f"URL {url}\n title={title}\n body={body!r}\n")
                if "Log in" in title or "Sign in" in title or "supabase.com/auth" in page.url:
                    print("NEED_LOGIN: complete Supabase dashboard login in the opened window, then re-run.")
                    page.wait_for_timeout(120000)
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                # Look for IPv4 enable controls
                for label in [
                    "Enable IPv4",
                    "Enable dedicated IPv4",
                    "IPv4 address",
                    "Dedicated IPv4",
                ]:
                    loc = page.get_by_text(label, exact=False)
                    if loc.count() > 0:
                        print(f"Found text: {label} count={loc.count()}")
                        try:
                            loc.first.click(timeout=5000)
                            page.wait_for_timeout(2000)
                            # confirm dialogs
                            for confirm in ["Enable", "Confirm", "Continue", "Purchase", "Subscribe"]:
                                btn = page.get_by_role("button", name=confirm, exact=False)
                                if btn.count() > 0 and btn.first.is_visible():
                                    btn.first.click(timeout=3000)
                                    page.wait_for_timeout(1500)
                            page.screenshot(path=str(OUT / "after_enable.png"), full_page=True)
                            print("Clicked enable flow; screenshot saved")
                        except Exception as exc:
                            print(f"Click failed for {label}: {exc}")

                if "infrastructure" in page.url or "IPv4" in page.inner_text("body"):
                    page.screenshot(path=str(OUT / "final.png"), full_page=True)
                    print("Reached infrastructure/IPv4 UI")
                    break
            except Exception as exc:
                print(f"Failed {url}: {exc}")

        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
