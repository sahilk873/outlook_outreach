#!/usr/bin/env python3
"""One-off: open Outlook compose and dump form structure so we can fix selectors. Run from project root."""
import asyncio
import json
import sys
from pathlib import Path

# Project root
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

OUTLOOK_MAIL_URL = "https://outlook.office.com/mail/"
SESSION_PATH = _root / "outlook_session.json"


async def _ensure_logged_in(page, session_path):
    await page.goto(OUTLOOK_MAIL_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeout:
        await page.wait_for_load_state("load", timeout=5000)
    if "login" in page.url.lower():
        print("Not logged in. Log in in the browser, then run this script again.")
        await page.wait_for_url(lambda u: "outlook.office.com" in u and "login" not in u.lower(), timeout=120_000)
        await page.context.storage_state(path=str(session_path))


async def _click_new_mail(page):
    for sel in [
        "button:has-text('New mail')",
        "button:has-text('New message')",
        "button:has-text('New')",
        "[aria-label*='New mail']",
        "[aria-label*='Compose']",
    ]:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=5000)
            try:
                async with page.expect_popup(timeout=4000) as popup_info:
                    await btn.click()
                return await popup_info.value
            except PlaywrightTimeout:
                return page
        except PlaywrightTimeout:
            continue
    raise RuntimeError("Could not find New mail button.")


# JS to collect form-like elements and buttons from the page (and main frame)
DUMP_SCRIPT = """
() => {
  const out = { inputs: [], contenteditables: [], buttons: [], frames: [] };
  const add = (arr, el, extra = {}) => {
    try {
      const rect = el.getBoundingClientRect();
      arr.push({
        tag: el.tagName,
        type: el.type || null,
        ariaLabel: el.getAttribute('aria-label'),
        placeholder: el.getAttribute('placeholder'),
        role: el.getAttribute('role'),
        id: el.id || null,
        name: el.getAttribute('name'),
        className: (el.className && typeof el.className === 'string' ? el.className.slice(0, 120) : '') || null,
        visible: rect.width > 0 && rect.height > 0,
        ...extra
      });
    } catch (e) { arr.push({ error: String(e) }); }
  };
  document.querySelectorAll('input').forEach(el => add(out.inputs, el));
  document.querySelectorAll('[contenteditable="true"]').forEach(el => add(out.contenteditables, el, { content: (el.innerText || '').slice(0, 80) }));
  document.querySelectorAll('button, [role="button"]').forEach(el => add(out.buttons, el, { text: (el.innerText || '').slice(0, 60) }));
  return out;
}
"""


async def main():
    out_file = _root / "scripts" / "compose_structure.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context_options = {}
        if SESSION_PATH.exists():
            context_options["storage_state"] = str(SESSION_PATH)
        context = await browser.new_context(**context_options)
        page = await context.new_page()
        page.set_default_timeout(30000)

        await _ensure_logged_in(page, SESSION_PATH)
        print("Clicking New mail...")
        compose_page = await _click_new_mail(page)
        await compose_page.wait_for_timeout(4000)

        # Dump main frame
        main_dump = await compose_page.evaluate(DUMP_SCRIPT)
        result = { "url": compose_page.url, "main_frame": main_dump }

        # Dump each iframe
        for i, frame in enumerate(compose_page.frames):
            if frame == compose_page.main_frame:
                continue
            try:
                dump = await frame.evaluate(DUMP_SCRIPT)
                result[f"frame_{i}_url"] = frame.url
                result[f"frame_{i}"] = dump
            except Exception as e:
                result[f"frame_{i}_error"] = str(e)

        with open(out_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Wrote {out_file}")

        await compose_page.screenshot(path=_root / "scripts" / "compose_screenshot.png")
        print("Screenshot: scripts/compose_screenshot.png")

        await context.close()
        await browser.close()

    print("Done. Check scripts/compose_structure.json for selectors.")


if __name__ == "__main__":
    asyncio.run(main())
