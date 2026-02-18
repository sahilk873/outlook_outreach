"""Send email via Outlook Web using Playwright browser automation (async)."""
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

from outlook.email_util import normalize_email


@dataclass
class OutlookSendSession:
    """Holds an open Playwright browser/context/page for sending multiple emails. Caller must call close_outlook_send_session when done."""

    _pw_cm: object  # async_playwright() context manager, for __aexit__ on close
    browser: object
    context: object
    page: Page
    session_path: Path


OUTLOOK_MAIL_URL = "https://outlook.office.com/mail/"
# Outlook Web selectors—these may need updates if Microsoft changes the UI
SELECTOR_NEW_MAIL = "button:has-text('New'), button:has-text('New mail'), [aria-label*='New'], [aria-label*='Compose']"
SELECTOR_TO = "input[aria-label*='To'], input[aria-label*='to'], [aria-label*='To'] input"
SELECTOR_SUBJECT = "input[placeholder*='subject'], input[placeholder*='Subject'], [aria-label*='Add a subject']"
SELECTOR_BODY = "div[role='textbox'], div[aria-label*='Message body'], [contenteditable='true']"
SELECTOR_SEND = "button:has-text('Send'), [aria-label*='Send'], button[title*='Send']"
SELECTOR_ATTACH = "button:has-text('Attach'), button[aria-label*='Attach'], [aria-label*='Attach file']"


async def _ensure_logged_in(page: Page, session_path: Path, headless: bool) -> None:
    """Navigate to Outlook; if not logged in, wait for user to complete login and save session."""
    await page.goto(OUTLOOK_MAIL_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeout:
        await page.wait_for_load_state("load", timeout=5000)

    if "login" in page.url.lower() or "login.microsoftonline.com" in page.url:
        if headless:
            raise RuntimeError(
                "Outlook requires login. Run once with headless=False to log in and save the session."
            )
        print("Please log in to Outlook in the browser window. Waiting for redirect to mail...")
        await page.wait_for_url(
            lambda url: "outlook.office.com" in url and "login" not in url.lower(),
            timeout=120_000,
        )
        await page.context.storage_state(path=str(session_path))
        print(f"Session saved to {session_path}")


async def _click_new_mail(page: Page) -> Page:
    """Click New mail to open compose; returns the page that has the compose form (may be a popup)."""
    btn = None
    for sel in [
        "button:has-text('New mail')",
        "button:has-text('New message')",
        "button:has-text('New')",
        "[aria-label*='New mail']",
        "[aria-label*='New message']",
        "[aria-label*='Compose']",
    ]:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=5000)
            break
        except PlaywrightTimeout:
            continue
    if btn is None:
        raise RuntimeError("Could not find 'New mail' button. Outlook UI may have changed.")
    # Compose may open in same page (pane) or in a new window
    try:
        async with page.expect_popup(timeout=4000) as popup_info:
            await btn.click()
        return await popup_info.value
    except PlaywrightTimeout:
        return page


# To field: Outlook Web uses a contenteditable div with aria-label="To", not an input.
TO_SELECTORS = [
    "[aria-label='To']",
    "div[contenteditable='true'][aria-label='To']",
    "[aria-label*='To']",
    "input[aria-label*='To']",
    "input[aria-label*='to']",
    "input[placeholder*='To']",
    "input[placeholder*='Enter recipient']",
    "[aria-label*='To recipients'] input",
    "input[aria-label*='To recipients']",
    "[role='combobox'][aria-label*='To']",
    "input[placeholder*='recipient']",
    "input",
]


def _compose_roots(page: Page):
    """Yield the main page and any iframes that might contain the compose form."""
    yield page
    for frame in page.frames:
        if frame != page.main_frame and frame.url and "outlook" in frame.url.lower():
            yield frame


async def _find_to_locator(page: Page):
    """Find the To field on page or in an iframe; return (locator, frame_or_page) for filling."""
    for root in _compose_roots(page):
        for sel in TO_SELECTORS:
            try:
                loc = root.locator(sel).first
                await loc.wait_for(state="visible", timeout=2000)
                return loc
            except PlaywrightTimeout:
                continue
    return None


async def _fill_and_send(
    page: Page,
    to_email: str,
    subject: str,
    body: str,
    attachments: list[str],
) -> None:
    """Fill compose form and send. Assumes compose pane/window is already open."""
    await page.wait_for_timeout(2500)

    to_email = normalize_email(to_email)

    to_input = await _find_to_locator(page)
    if to_input is None:
        raise RuntimeError(
            "Could not find To field. Outlook compose UI may have changed; check selectors in outlook/send.py."
        )
    await to_input.fill(to_email)
    await page.wait_for_timeout(300)

    # Subject: Outlook uses input with aria-label="Subject" or placeholder "Add a subject"
    subject_input = None
    for root in _compose_roots(page):
        try:
            loc = root.locator(
                "input[aria-label='Subject'], input[placeholder='Add a subject'], "
                "input[placeholder*='subject'], input[aria-label*='Add a subject']"
            ).first
            await loc.wait_for(state="visible", timeout=2000)
            subject_input = loc
            break
        except PlaywrightTimeout:
            continue
    if subject_input is None:
        try:
            subject_input = page.locator("input").nth(1)
            await subject_input.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeout:
            raise RuntimeError("Could not find Subject field.")
    await subject_input.fill(subject)
    await page.wait_for_timeout(300)

    # Body: first contenteditable or role=textbox after To/Subject
    body_selectors = [
        "div[role='textbox']",
        "div[aria-label*='Message body']",
        "div[contenteditable='true']",
        "[data-accept='text']",
    ]
    body_el = None
    for root in _compose_roots(page):
        for sel in body_selectors:
            try:
                loc = root.locator(sel).first
                await loc.wait_for(state="visible", timeout=2000)
                body_el = loc
                break
            except PlaywrightTimeout:
                continue
        if body_el is not None:
            break
    if body_el is None:
        raise RuntimeError("Could not find message body field.")
    await body_el.click()
    await body_el.fill(body)

    if attachments:
        # Outlook: "Attach file" opens a dropdown; file chooser opens only after "Browse this computer"
        attach_btn_selectors = [
            "[aria-label='Attach file']",
            "button[aria-label='Attach file']",
            "button:has-text('Attach file')",
            "[aria-label*='Attach file']",
        ]
        browse_menu_labels = ["Browse this computer", "Attach a file", "Browse", "Upload from this device"]
        for path in attachments:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                print(f"  Attachment skipped (not found): {path}", file=sys.stderr)
                continue
            attached = False
            for root in _compose_roots(page):
                for sel in attach_btn_selectors:
                    try:
                        attach_btn = root.locator(sel).first
                        await attach_btn.wait_for(state="visible", timeout=2000)
                        await attach_btn.click()
                        await page.wait_for_timeout(800)
                        # Click the menu item that opens the file chooser (Outlook: "Browse this computer")
                        for label in browse_menu_labels:
                            try:
                                menu_item = page.get_by_role("menuitem", name=label)
                                await menu_item.wait_for(state="visible", timeout=2000)
                                async with page.expect_file_chooser(timeout=8000) as fc_info:
                                    await menu_item.click()
                                chooser = await fc_info.value
                                await chooser.set_files(str(p))
                                attached = True
                                await page.wait_for_timeout(2000)  # allow Outlook to process attachment
                                break
                            except PlaywrightTimeout:
                                continue
                        if not attached:
                            # Fallback: get_by_text in case role isn't menuitem
                            for label in browse_menu_labels:
                                try:
                                    menu_item = page.get_by_text(label, exact=False).first
                                    await menu_item.wait_for(state="visible", timeout=2000)
                                    async with page.expect_file_chooser(timeout=8000) as fc_info:
                                        await menu_item.click()
                                    chooser = await fc_info.value
                                    await chooser.set_files(str(p))
                                    attached = True
                                    await page.wait_for_timeout(2000)  # allow Outlook to process attachment
                                    break
                                except (PlaywrightTimeout, Exception):
                                    continue
                        if attached:
                            break
                    except PlaywrightTimeout:
                        continue
                if attached:
                    break
            if not attached:
                print(f"  Attachment failed (could not open file chooser): {p.name}", file=sys.stderr)

    # Dismiss any open dropdown (e.g. attach menu) by clicking the message body.
    # Do not use Escape - Outlook treats it as "close compose" and shows "Discard message?" dialog.
    try:
        body_el = page.locator("div[aria-label='Message body'], div[role='textbox']").first
        await body_el.click(timeout=2000)
        await page.wait_for_timeout(400)
    except PlaywrightTimeout:
        pass

    # Primary Send button has exact aria-label "Send"; avoid "More send options" (aria-label*='Send')
    send_btn = None
    for root in _compose_roots(page):
        try:
            loc = root.get_by_role("button", name="Send")
            await loc.first.wait_for(state="visible", timeout=5000)
            send_btn = loc.first
            break
        except PlaywrightTimeout:
            pass
        for sel in [
            "[aria-label='Send']",
            "button[aria-label='Send']",
            "button:has-text('Send')",
            "#splitButton-r6e__primaryActionButton",
            "[id^='splitButton-'][id*='primaryActionButton']",
        ]:
            try:
                loc = root.locator(sel).first
                await loc.wait_for(state="visible", timeout=3000)
                send_btn = loc
                break
            except PlaywrightTimeout:
                continue
        if send_btn is not None:
            break
    if send_btn is None:
        raise RuntimeError("Could not find Send button.")
    await send_btn.click()
    await page.wait_for_timeout(3000)


async def ensure_outlook_session(session_path: Path, headless: bool = False) -> bool:
    """
    Open browser, navigate to Outlook, and ensure we have a valid session (login if needed).
    Saves session to session_path for use by send_via_outlook_web. Call this at pipeline start
    so the user can log in once before discovery/draft, instead of only when they confirm send.
    Returns True if session is ready, False on failure.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context_options = {}
            if session_path.exists():
                context_options["storage_state"] = str(session_path)
            context = await browser.new_context(**context_options)
            page = await context.new_page()
            page.set_default_timeout(30000)
            await _ensure_logged_in(page, session_path, headless)
            await context.storage_state(path=str(session_path))
            await context.close()
            await browser.close()
        return True
    except Exception as e:
        print(f"Outlook session check failed: {e}", file=sys.stderr)
        return False


async def open_outlook_send_session(session_path: Path, headless: bool = False) -> OutlookSendSession:
    """
    Open a browser and ensure logged in to Outlook; return a session for sending multiple emails.
    Caller must call close_outlook_send_session(session) when done.
    """
    pw_cm = async_playwright()
    playwright = await pw_cm.__aenter__()
    browser = None
    context = None
    try:
        browser = await playwright.chromium.launch(headless=headless)
        context_options = {}
        if session_path.exists():
            context_options["storage_state"] = str(session_path)
        context = await browser.new_context(**context_options)
        page = await context.new_page()
        page.set_default_timeout(30000)
        await _ensure_logged_in(page, session_path, headless)
        await context.storage_state(path=str(session_path))
        return OutlookSendSession(_pw_cm=pw_cm, browser=browser, context=context, page=page, session_path=session_path)
    except Exception:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw_cm.__aexit__(None, None, None)
        except Exception:
            pass
        raise


async def send_one_on_session(
    session: OutlookSendSession,
    to: str,
    subject: str,
    body: str,
    attachments: list[str],
    session_path: Path,
) -> bool:
    """
    Send one email using an existing Outlook send session. Does not close the browser.
    Returns True on success, False on failure (logs exception).
    """
    try:
        compose_page = await _click_new_mail(session.page)
        await _fill_and_send(
            compose_page,
            to_email=to,
            subject=subject,
            body=body,
            attachments=attachments,
        )
        await session.context.storage_state(path=str(session_path))
        return True
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)
        return False


async def close_outlook_send_session(session: OutlookSendSession) -> None:
    """Save session state and close the browser. Idempotent if session is already closed."""
    try:
        await session.context.storage_state(path=str(session.session_path))
    except Exception:
        pass
    try:
        await session.context.close()
    except Exception:
        pass
    try:
        await session.browser.close()
    except Exception:
        pass
    try:
        await session._pw_cm.__aexit__(None, None, None)
    except Exception:
        pass


async def send_via_outlook_web(
    to: str,
    subject: str,
    body: str,
    *,
    session_path: Path,
    attachments: list[str] | None = None,
    headless: bool = False,
) -> bool:
    """
    Send one email via Outlook Web (Playwright).

    - Uses session_path to load/save cookies (reuse after first login).
    - If not logged in and headless=False, waits for manual login and saves session.
    - Returns True on success, False on failure.
    """
    session = None
    try:
        session = await open_outlook_send_session(session_path, headless=headless)
        ok = await send_one_on_session(
            session,
            to=to,
            subject=subject,
            body=body,
            attachments=list(attachments or []),
            session_path=session_path,
        )
        return ok
    finally:
        if session is not None:
            await close_outlook_send_session(session)
