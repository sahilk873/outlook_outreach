"""CLI: run startup outreach pipeline (discover -> find emails -> draft -> optional send) via Outlook Web."""
import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is on path when run as script
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from manager import DraftWithMeta, OutreachManager, OutreachResult

# Keys that can be set in a config file (same names as CLI, but with underscores)
_CONFIG_KEYS = (
    "criteria",
    "list_file",
    "purpose",
    "subject",
    "tone",
    "notes",
    "max_startups",
    "no_confirm",
    "attach",
)


def _load_config(path: str) -> dict:
    import yaml
    p = Path(path)
    if not p.is_absolute():
        p = _root / p
    if not p.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"  Copy from example: cp outreach.example.yaml outreach.yaml\n"
            f"  Or use existing:    python main.py -c outreach.example.yaml"
        )
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return {k: v for k, v in data.items() if k in _CONFIG_KEYS}


def _apply_config(args: argparse.Namespace, config: dict) -> None:
    if "attach" in config and config["attach"] is not None:
        config["attach"] = list(config["attach"])
    for key, value in config.items():
        if value is not None:
            setattr(args, key, value)


def _print_result(result: OutreachResult) -> None:
    print("\n--- Discovered ---")
    if not result.discovered and not result.skipped_already_emailed:
        print("  (none – discovery returned 0 startups; check API key, model, and criteria)")
    elif not result.discovered and result.skipped_already_emailed:
        print("  (none new – all discovered companies were already emailed)")
    for s in result.discovered:
        print(f"  {s.name} | {s.domain} | {s.one_liner or '-'}")
    print("\n--- With email ---")
    for startup, email in result.with_email:
        print(f"  {startup.name} -> {email}")
    if result.skipped_no_email:
        print("\n--- Skipped (no email) ---")
        for name in result.skipped_no_email:
            print(f"  {name}")
    if result.skipped_already_emailed:
        print("\n--- Skipped (already emailed) ---")
        for name in result.skipped_already_emailed:
            print(f"  {name}")
    print("\n--- Drafts ---")
    for d in result.drafts:
        print(f"\n  To: {d.to_email} | {d.startup.name}")
        print(f"  Subject: {d.subject}")
        if result.attachments:
            print(f"  Attachments: {result.attachments}")
        print(f"  Body:\n{d.body}")
    if result.sent:
        print("\n--- Sent ---")
        for e in result.sent:
            print(f"  {e}")
    if result.failed_send:
        print("\n--- Failed to send ---")
        for e in result.failed_send:
            print(f"  {e}")


async def main_async(args: argparse.Namespace) -> None:
    purpose = args.purpose or "outreach"
    tone = args.tone or "professional"
    manager = OutreachManager(
        confirm_before_send=not args.no_confirm,
        headless=getattr(args, "headless", False),
    )

    if args.list_file:
        with open(args.list_file) as f:
            criteria = f.read().strip()
        if not criteria:
            criteria = "Startups listed in the attached context (use the list below)."
    else:
        criteria = args.criteria or ""

    if not criteria:
        print("Provide --criteria or --list-file.", file=sys.stderr)
        sys.exit(1)

    attachments = list(args.attach) if args.attach else []

    def confirm_callback(draft: DraftWithMeta) -> bool:
        print(f"\n  To: {draft.to_email} | {draft.startup.name}")
        print(f"  Subject: {draft.subject}")
        if attachments:
            print(f"  Attachments: {attachments}")
        print(f"  Body:\n{draft.body}")
        try:
            answer = input("\nSend this email? [y/N]: ").strip().lower()
            return answer in ("y", "yes")
        except EOFError:
            return False

    result = await manager.run(
        criteria=criteria,
        purpose=purpose,
        tone=tone,
        extra_notes=args.notes or "",
        subject_hint=getattr(args, "subject", None) or "",
        max_startups=args.max_startups,
        attachments=attachments,
        confirm_callback=confirm_callback if manager.confirm_before_send else None,
    )

    _print_result(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Startup outreach: discover, find emails, draft and optionally send via Outlook Web (Playwright)."
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        metavar="FILE",
        help="Path to YAML config file with criteria/list_file, purpose, tone, notes, max_startups, no_confirm, attach.",
    )
    parser.add_argument(
        "--criteria",
        type=str,
        help="Search criteria for startups (e.g. 'Seed-stage B2B SaaS in fintech').",
    )
    parser.add_argument(
        "--list-file",
        type=str,
        metavar="FILE",
        help="Path to file containing startup names/domains or criteria.",
    )
    parser.add_argument(
        "--purpose",
        type=str,
        default="outreach",
        help="Purpose of the email (e.g. 'partnership intro').",
    )
    parser.add_argument(
        "--subject",
        type=str,
        help="Optional subject line or hint for the email (writer uses or adapts it).",
    )
    parser.add_argument(
        "--tone",
        type=str,
        default="professional",
        help="Tone: professional, friendly, etc.",
    )
    parser.add_argument(
        "--notes",
        type=str,
        help="Optional template or bullet points for the draft.",
    )
    parser.add_argument(
        "--max-startups",
        type=int,
        default=None,
        metavar="N",
        help="Cap number of startups to process.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Send emails without asking for confirmation (default: ask before send).",
    )
    parser.add_argument(
        "--attach",
        type=str,
        nargs="+",
        metavar="FILE",
        help="Paths to files to attach to each email (e.g. --attach pitch.pdf deck.docx).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (requires valid session; first login must be headed).",
    )
    args = parser.parse_args()

    if args.config:
        config = _load_config(args.config)
        _apply_config(args, config)

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
