#!/usr/bin/env python3
"""
meet-join-diagnostic.py — Inspect a Google Meet page through the bot's chrome-profile.

Opens the Meet URL using launch_persistent_context with the shared chrome-profile,
dumps the full page state (buttons, body text, UI elements), and saves a screenshot.
Useful for diagnosing why the bot can't join — auth, button state, meeting room status.

Usage:
    python3 scripts/meet-join-diagnostic.py <meet-url> [--headed] [--wait N]

Positional arguments:
    meet-url    Full Google Meet URL (https://meet.google.com/xxx-xxxx-xxx)

Options:
    --headed    Run in headed mode (needs DISPLAY). Default: headless
    --wait N    Seconds to wait after page load before dumping state. Default: 10

Output:
    - Prints full page state: title, URL, buttons, body text, UI elements
    - Saves screenshot to /tmp/meet-diagnostic-<timestamp>.png
    - Also saves to the meeting's output directory if it exists under ~/.hermes/workspace/meetings/

Requirements:
    - Playwright must be installed (pip install playwright && playwright install chromium)
    - The chrome-profile must exist at ~/.hermes/workspace/meetings/chrome-profile
"""

from pathlib import Path
import json, os, sys, time, argparse

PROFILE = os.path.expanduser("~/.hermes/workspace/meetings/chrome-profile")

def main():
    parser = argparse.ArgumentParser(description="Diagnose Google Meet join state")
    parser.add_argument("meet_url", help="Google Meet URL")
    parser.add_argument("--headed", action="store_true", help="Run in headed mode")
    parser.add_argument("--wait", type=int, default=10, help="Seconds to wait after page load")
    args = parser.parse_args()

    if not os.path.isdir(PROFILE):
        print(f"ERROR: Chrome profile not found at {PROFILE}")
        sys.exit(1)

    sys.path.insert(0, "~/.hermes/hermes-agent")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            PROFILE,
            headless=not args.headed,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
            viewport={"width": 1440, "height": 900},
        )
        page = browser.pages[0]
        print(f"Navigating to {args.meet_url}...")
        page.goto(args.meet_url, wait_until="domcontentloaded", timeout=30000)
        print(f"Waiting {args.wait}s for page to settle...")
        time.sleep(args.wait)

        # ---- Page metadata ----
        title = page.title()
        url = page.url
        print(f"\n=== PAGE STATE ===")
        print(f"Title: {title}")
        print(f"URL: {url}")

        # ---- All buttons ----
        buttons = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            return btns.map(b => ({
                text: b.textContent.trim().substring(0, 80),
                ariaLabel: b.getAttribute('aria-label'),
                visible: b.offsetParent !== null,
                disabled: b.disabled,
            })).filter(b => b.text || b.ariaLabel);
        }""")
        print(f"\nButtons ({len(buttons)}):")
        for i, b in enumerate(buttons):
            print(f"  [{i}] text={b['text']!r} aria={b['ariaLabel']!r} visible={b['visible']} disabled={b['disabled']}")

        # ---- Body text ----
        body = page.evaluate("() => document.body.innerText.substring(0, 1000)")
        print(f"\nBody text (first 1000 chars):\n{body}")

        # ---- Key UI element detection ----
        ui = page.evaluate("""() => {
            const body = document.body.innerText;
            return {
                hasJoinBtn: !!document.querySelector('button:has-text("Join now")'),
                joinBtnEnabled: (() => {
                    const btn = document.querySelector('button:has-text("Join now")');
                    return btn ? {visible: btn.offsetParent !== null, disabled: btn.disabled} : null;
                })(),
                hasAskToJoin: !!document.querySelector('button:has-text("Ask to join")'),
                askBtnEnabled: (() => {
                    const btn = document.querySelector('button:has-text("Ask to join")');
                    return btn ? {visible: btn.offsetParent !== null, disabled: btn.disabled} : null;
                })(),
                hasLeaveBtn: !!document.querySelector('button[aria-label*="Leave"], button:has-text("Leave")'),
                hasMic: !!document.querySelector('button[aria-label*="mic"], button[aria-label*="Microphone"]'),
                hasCam: !!document.querySelector('button[aria-label*="cam"], button[aria-label*="Camera"]'),
                hasCaptions: !!document.querySelector('button[aria-label*="captions"], button[aria-label*="Captions"]'),
                hasGettingReady: body.includes('Getting ready'),
                hasJoinInMoment: body.includes('join in just a moment'),
                hasYouCantJoin: body.includes("can't join") || body.includes("cannot join"),
                hasMeetingEnded: body.includes("Meeting ended") || body.includes("You left"),
                hasReturnHome: body.includes("Return to home screen"),
                hasSignIn: body.includes("Sign in"),
            };
        }""")
        print(f"\n=== UI STATE ===")
        for k, v in ui.items():
            print(f"  {k}: {v}")

        # ---- Screenshot ----
        ts = int(time.time())
        screenshot_path = f"/tmp/meet-diagnostic-{ts}.png"
        page.screenshot(path=screenshot_path)
        print(f"\nScreenshot saved to {screenshot_path}")

        # Try to also save to meeting output dir
        slug = args.meet_url.rstrip("/").split("/")[-1]
        meet_dir = os.path.expanduser(f"~/.hermes/workspace/meetings/{slug}")
        if os.path.isdir(meet_dir):
            diag_path = os.path.join(meet_dir, f"diagnostic-{ts}.png")
            page.screenshot(path=diag_path)
            print(f"Also saved to {diag_path}")

        browser.close()

if __name__ == "__main__":
    main()
