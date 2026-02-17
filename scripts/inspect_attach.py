#!/usr/bin/env python3
"""Inspect the Attach UI in Outlook compose: find attach buttons and what appears after click."""
import asyncio
import json
import sys
from pathlib import Path

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
        print("Not logged in. Log in, then run again.")
        await page.wait_for_url(lambda u: "outlook.office.com" in u and "login" not in u.lower(), timeout=120_000)
        await page.context.storage_state(path=str(session_path))


async def _click_new_mail(page):
    for sel in [
        "button:has-text('New mail')",
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


# Dump elements that might be "Attach" related
DUMP_ATTACH_SCRIPT = """
() => {
  const out = [];
  document.querySelectorAll('button, [role="button"], [aria-label*="Attach"], [aria-label*="Insert"]').forEach(el => {
    const label = el.getAttribute('aria-label') || '';
    const text = (el.innerText || '').slice(0, 80);
    if (label.toLowerCase().includes('attach') || label.toLowerCase().includes('insert') || text.toLowerCase().includes('attach') || text.toLowerCase().includes('file')) {
      const rect = el.getBoundingClientRect();
      out.push({
        tag: el.tagName,
        ariaLabel: label,
        text: text,
        id: el.id,
        visible: rect.width > 0 && rect.height > 0,
        className: (el.className && typeof el.className === 'string' ? el.className.slice(0, 80) : '')
      });
    }
  });
  return out;
}
"""

# After clicking Attach, dump any new visible list/menu items
DUMP_MENU_SCRIPT = """
() => {
  const out = [];
  document.querySelectorAll('[role="menu"] [role="menuitem"], [role="menu"] button, [role="listbox"] [role="option"], .ms-Callout button, [data-tid="attachMenu"] button, [aria-label*="ttach"]').forEach(el => {
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      out.push({
        tag: el.tagName,
        ariaLabel: el.getAttribute('aria-label'),
        text: (el.innerText || '').slice(0, 100),
        role: el.getAttribute('role')
      });
    }
  });
  return out;
}
"""


async def main():
    out_file = _root / "scripts" / "attach_inspect.json"
    _root.mkdir(parents=True, exist_ok=True)

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
        await compose_page.wait_for_timeout(3000)

        # 1) Dump attach-related elements
        attach_elements = await compose_page.evaluate(DUMP_ATTACH_SCRIPT)
        result = {"attach_candidates": attach_elements}

        # 2) Try to find and click the main Attach file button
        attach_btn_selectors = [
            "[aria-label='Attach file']",
            "button[aria-label='Attach file']",
            "button:has-text('Attach file')",
            "[aria-label*='Attach file']",
        ]
        clicked = False
        for sel in attach_btn_selectors:
            try:
                btn = compose_page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2000)
                await btn.click()
                clicked = True
                result["clicked_selector"] = sel
                break
            except PlaywrightTimeout:
                continue

        if clicked:
            await compose_page.wait_for_timeout(2500)
            result["menu_or_dialog_after_click"] = await compose_page.evaluate(DUMP_MENU_SCRIPT)
            # Also dump all visible buttons that appeared (e.g. in a dropdown)
            result["all_visible_buttons_now"] = await compose_page.evaluate("""
                () => {
                    const out = [];
                    document.querySelectorAll('button, [role="button"]').forEach(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            const t = (el.innerText || '').trim().slice(0, 120);
                            const l = el.getAttribute('aria-label') || '';
                            if (t || l) out.push({ text: t, ariaLabel: l });
                        }
                    });
                    return out.slice(0, 50);
                }
            """)
            await compose_page.screenshot(path=_root / "scripts" / "attach_after_click.png")
            result["screenshot"] = "scripts/attach_after_click.png"

        with open(out_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Wrote {out_file}")
        if result.get("screenshot"):
            print(f"Screenshot: {result['screenshot']}")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
