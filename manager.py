"""Orchestrator: discovery -> find emails -> draft -> (optional approve) -> send via Outlook Web."""
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agents import Runner

from outreach_agents.discovery_agent import discovery_agent
from outreach_agents.email_finder_agent import email_finder_agent
from outreach_agents.models import DiscoveryOutput, EmailDraft, StartupItem
from outreach_agents.writer_agent import writer_agent
from outlook.email_util import normalize_email
from outlook.send import (
    close_outlook_send_session,
    ensure_outlook_session,
    open_outlook_send_session,
    send_one_on_session,
)
from config import OUTLOOK_SESSION_PATH, EMAILED_COMPANIES_PATH

# Simple email regex to detect if finder returned an address
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _normalize_domain(domain: str) -> str:
    """Lowercase and strip for consistent lookup."""
    return (domain or "").strip().lower()


def _load_emailed_companies(path: Path) -> dict[str, str]:
    """Load dict of domain -> last_emailed_at (iso) from JSON file."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_emailed_company(path: Path, domain: str) -> None:
    """Append one domain to the emailed-companies file (merge with existing)."""
    data = _load_emailed_companies(path)
    data[_normalize_domain(domain)] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


@dataclass
class DraftWithMeta:
    """A draft plus startup/recipient info for review or send."""

    startup: StartupItem
    to_email: str
    subject: str
    body: str


@dataclass
class OutreachResult:
    """Result of a full outreach run."""

    discovered: list[StartupItem] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    with_email: list[tuple[StartupItem, str]] = field(default_factory=list)
    drafts: list[DraftWithMeta] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    failed_send: list[str] = field(default_factory=list)
    skipped_no_email: list[str] = field(default_factory=list)
    skipped_already_emailed: list[str] = field(default_factory=list)


def _parse_email_from_field(text: str) -> str | None:
    """Extract a single email from a string (e.g. list-file column); return None if none found."""
    if not text or not text.strip():
        return None
    m = EMAIL_PATTERN.search(text.strip())
    if m:
        return normalize_email(m.group(0))
    return None


def load_startups_from_list_file(path: Path) -> list[StartupItem]:
    """
    Parse a list file into StartupItems (one company per line).
    Format: "Name | domain.com | one-liner" or "Name | domain.com | one-liner | email".
    Optional 4th column: contact email; when present and valid, email-finding is skipped for that row.
    Deduplicates by normalized domain if present, else by normalized name. Empty file returns [].
    Raises FileNotFoundError if path does not exist.
    """
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"list_file not found: {path}")
    seen_domain: set[str] = set()
    seen_name: set[str] = set()
    result: list[StartupItem] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                name, domain, one_liner = parts[0], parts[1], parts[2][:120]
            elif len(parts) == 2:
                name, domain, one_liner = parts[0], parts[1], ""
            else:
                token = parts[0]
                if "." in token and " " not in token:
                    name = domain = token
                    one_liner = ""
                else:
                    name, domain, one_liner = token, "", ""
            parsed_email: str | None = None
            if len(parts) >= 4 and parts[3]:
                parsed_email = _parse_email_from_field(parts[3])
            domain_n = _normalize_domain(domain)
            name_n = (name or "").strip().lower()
            key = domain_n if domain_n else name_n
            if not key:
                continue
            if domain_n and domain_n in seen_domain:
                continue
            if not domain_n and name_n in seen_name:
                continue
            if domain_n:
                seen_domain.add(domain_n)
            else:
                seen_name.add(name_n)
            result.append(
                StartupItem(
                    name=name or key,
                    domain=domain,
                    one_liner=one_liner,
                    email=parsed_email,
                )
            )
    return result


async def _discover_startups(criteria: str) -> list[StartupItem]:
    """Run discovery agent once and return structured list of startups (unique by domain)."""
    result = await Runner.run(discovery_agent, criteria)
    discovery = result.final_output_as(DiscoveryOutput)
    seen: set[str] = set()
    unique: list[StartupItem] = []
    for s in discovery.startups:
        d = _normalize_domain(s.domain)
        if d and d not in seen:
            seen.add(d)
            unique.append(s)
    return unique


def _parse_email_from_finder_output(text: str) -> str | None:
    """Extract a single email from agent response, or None if NOT_FOUND."""
    if not text or "NOT_FOUND" in text.strip().upper():
        return None
    for part in text.replace(",", " ").split():
        m = EMAIL_PATTERN.search(part)
        if m:
            return normalize_email(m.group(0))
    return None


async def _find_email(startup: StartupItem) -> str | None:
    """Run email finder agent for one startup; return email or None."""
    input_text = (
        f"Company name: {startup.name}\n"
        f"Domain: {startup.domain}\n"
        "Find the best contact email (e.g. hello@, contact@, or founder)."
    )
    result = await Runner.run(email_finder_agent, input_text)
    raw = str(result.final_output).strip()
    return _parse_email_from_finder_output(raw)


async def _draft_email(
    startup: StartupItem,
    to_email: str,
    purpose: str,
    tone: str = "professional",
    extra_notes: str = "",
    subject_hint: str = "",
) -> EmailDraft:
    """Run writer agent to produce subject and body."""
    input_text = (
        f"Startup: {startup.name} ({startup.domain})\n"
        f"Recipient email: {to_email}\n"
        f"One-liner: {startup.one_liner}\n"
        f"Purpose: {purpose}\n"
        f"Tone: {tone}\n"
    )
    if subject_hint:
        input_text += f"Subject line or hint (use or adapt): {subject_hint}\n"
    if extra_notes:
        input_text += f"Notes or bullets to include: {extra_notes}\n"
    result = await Runner.run(writer_agent, input_text)
    return result.final_output_as(EmailDraft)


class OutreachManager:
    """Manager-driven pipeline: discover -> find emails -> draft -> optional confirm -> send via Outlook Web."""

    def __init__(
        self,
        *,
        confirm_before_send: bool = True,
        headless: bool = False,
    ):
        self.confirm_before_send = confirm_before_send
        self.headless = headless
        self.result = OutreachResult()
        self.attachments: list[str] = []

    async def run(
        self,
        criteria: str,
        purpose: str,
        tone: str = "professional",
        extra_notes: str = "",
        subject_hint: str = "",
        max_startups: int | None = None,
        attachments: list[str] | None = None,
        confirm_callback: Callable[[DraftWithMeta], bool] | None = None,
        startups: list[StartupItem] | None = None,
    ) -> OutreachResult:
        """
        Run the full pipeline: discover startups (or use provided list), then for each company find email -> draft -> send.

        When startups is provided, discovery is skipped and that list is used as-is. Otherwise criteria is passed
        to the discovery agent. Sends via Outlook Web (Playwright); first run opens a browser for login.
        """
        self.attachments = list(attachments) if attachments else []
        self.result.attachments = self.attachments
        for a in self.attachments:
            p = Path(a).expanduser().resolve()
            if not p.exists():
                print(f"Warning: attachment not found (will skip when sending): {a}", file=sys.stderr)
        sent_so_far = 0

        # Ensure Outlook session up front (opens browser for first-time login) so user
        # logs in before we run discovery/draft and ask "Send this email?"
        if not self.headless:
            print("Checking Outlook session (browser may open for login)...")
            if not await ensure_outlook_session(OUTLOOK_SESSION_PATH, headless=False):
                print("Warning: Could not establish Outlook session. Send step may fail.", file=sys.stderr)

        while True:
            emailed = _load_emailed_companies(EMAILED_COMPANIES_PATH)
            if startups is not None:
                raw_list = startups
            else:
                raw_list = await _discover_startups(criteria)
            to_process: list[StartupItem] = []
            for startup in raw_list:
                if _normalize_domain(startup.domain) in emailed:
                    self.result.skipped_already_emailed.append(startup.name)
                else:
                    to_process.append(startup)

            if max_startups is not None:
                batch = to_process[: max_startups - sent_so_far]
            else:
                batch = to_process

            if not batch:
                break

            if max_startups is None:
                self.result.discovered = batch
            else:
                self.result.discovered.extend(batch)

            send_session = None
            try:
                for startup in batch:
                    if startup.email and startup.email.strip():
                        email = normalize_email(startup.email)
                    else:
                        email = await _find_email(startup)
                    if not email:
                        self.result.skipped_no_email.append(startup.name)
                        continue
                    self.result.with_email.append((startup, email))

                    draft_data = await _draft_email(
                        startup=startup,
                        to_email=email,
                        purpose=purpose,
                        tone=tone,
                        extra_notes=extra_notes,
                        subject_hint=subject_hint,
                    )
                    draft = DraftWithMeta(
                        startup=startup,
                        to_email=email,
                        subject=draft_data.subject,
                        body=draft_data.body_plain,
                    )
                    self.result.drafts.append(draft)

                    should_send = True
                    if self.confirm_before_send:
                        should_send = confirm_callback(draft) if confirm_callback else False

                    if should_send:
                        if send_session is None:
                            send_session = await open_outlook_send_session(
                                OUTLOOK_SESSION_PATH, headless=self.headless
                            )
                        ok = await send_one_on_session(
                            send_session,
                            to=draft.to_email,
                            subject=draft.subject,
                            body=draft.body,
                            attachments=list(self.attachments),
                            session_path=OUTLOOK_SESSION_PATH,
                        )
                        if ok:
                            self.result.sent.append(draft.to_email)
                            _save_emailed_company(EMAILED_COMPANIES_PATH, startup.domain)
                            sent_so_far += 1
                        else:
                            self.result.failed_send.append(draft.to_email)
                            print("Send failed (see error above for details).", file=sys.stderr)
            finally:
                if send_session is not None:
                    await close_outlook_send_session(send_session)

            if max_startups is None:
                break
            if sent_so_far >= max_startups:
                break

        return self.result
