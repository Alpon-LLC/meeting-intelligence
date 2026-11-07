#!/usr/bin/env python3
"""
Diagnose what Google Meet UI shows for a given meet URL using the chrome-profile.
Launches headed Chromium with the persistent profile and dumps page content.

Usage:
  /home/Work/.hermes/hermes-agent/venv/bin/python3 \
    /home/Work/.hermes/skills/meeting/meeting-auto-join/scripts/diagnose-meet-ui.py \
    <meet_url>

Example:
  .../diagnose-meet-ui.py https://meet.google.com/ryy-srsr-yjf

Outputs: page title, full body text, all visible/hidden buttons, signed-in user info.
Use this to distinguish between:
  - Wrong-account auth (body shows pre-join UI but no signed-in indicator)
  - Dead session / sign-in prompt (body contains "Sign in with your Google account")
  - Hard block (body contains "You can't join this video call")
  - Normal lobby (body contains "Ask to join" as a visible button)
"""
import sys, os, json

sys.path.insert(0, "/home/Work/.hermes/hermes-agent/plugins/google_meet")
os.environ.setdefault("DISPLAY", ":1")
os.environ.setdefault("XAUTHORITY", "/home/Work/.Xauthority")

from playwright.sync_api import sync_playwright

def main():
    meet_url = sys.argv[1] if len(sys.argv) > 1 else "https://meet.google.com/ryy-srsr-yjf"
    profile = "/home/Work/.hermes/workspace/meetings/chrome-profile"

    print(f"Profile exists: {os.path.isdir(profile)}", flush=True)
    cookies_path = os.path.join(profile, "Default", "Cookies")
    print(f"Cookies DB exists: {os.path.isfile(cookies_path)}", flush=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            profile,
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
            permissions=["microphone", "camera"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        print("Navigating...", flush=True)
        page.goto(meet_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        title = page.title()
        print(f"Page title: {title}", flush=True)

        body_text = page.evaluate("document.body.innerText")
        print(f"--- BODY TEXT ---\n{body_text[:2000]}\n--- END BODY ---", flush=True)

        # Check for specific sign-in prompts
        has_sign_in_prompt = "Sign in with your Google account" in body_text or "What's your name?" in body_text
        has_hard_block = "You can't join this video call" in body_text or "Your meeting is safe" in body_text
        has_ask_to_join = "Ask to join" in body_text and not has_sign_in_prompt

        print(f"\nDiagnosis:", flush=True)
        print(f"  Sign-in prompt present: {has_sign_in_prompt}", flush=True)
        print(f"  Hard block present: {has_hard_block}", flush=True)
        print(f"  Ask to join visible: {has_ask_to_join}", flush=True)

        # Enumerate all buttons
        buttons = page.evaluate("""() => {
            const all = document.querySelectorAll('button, [role="button"], [jsaction]');
            return Array.from(all).slice(0, 80).map(b => ({
                tag: b.tagName,
                text: b.innerText.slice(0, 60),
                visible: b.offsetParent !== null,
                disabled: b.disabled,
                role: b.getAttribute('role') || '',
                aria_label: b.getAttribute('aria-label') || '',
            }));
        }""")
        print(f"\n--- BUTTONS ({len(buttons)}) ---", flush=True)
        for i, btn in enumerate(buttons):
            if btn['visible']:
                print(f"  [{i}] VISIBLE: {btn['text'][:50]} | label={btn['aria_label'][:50]}", flush=True)
        for i, btn in enumerate(buttons):
            if not btn['visible'] and ('join' in btn['text'].lower() or 'ask' in btn['text'].lower() or btn['aria_label']):
                print(f"  [{i}] HIDDEN: {btn['text'][:50]} | label={btn['aria_label'][:50]}", flush=True)

        # Check for signed-in user
        signed_in = page.evaluate("""() => {
            const el = document.querySelector('[data-user-name], [data-email], .gb_ab');
            if (el) return el.innerText;
            const chips = document.querySelectorAll('[class*="user"]');
            for (const c of chips) {
                if (c.innerText.includes('@')) return c.innerText;
            }
            return null;
        }""")
        print(f"\nSigned in as: {signed_in}", flush=True)

        heading = page.evaluate("document.querySelector('h1, h2, h3')?.innerText || 'N/A'")
        print(f"Page heading: {heading}", flush=True)

        context.close()

if __name__ == "__main__":
    main()
