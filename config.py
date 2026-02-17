"""Load configuration from environment. No secrets in code."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")

# Outlook Web: path to store session cookies (reuse across runs to avoid repeated login)
_OUTLOOK_SESSION_PATH = os.getenv("OUTLOOK_SESSION_PATH", "outlook_session.json")
OUTLOOK_SESSION_PATH: Path = Path(_OUTLOOK_SESSION_PATH).expanduser().resolve()

# Path to JSON file tracking companies we have already emailed (domain -> last_emailed_at)
_EMAILED_PATH = os.getenv("EMAILED_COMPANIES_PATH", "emailed_companies.json")
EMAILED_COMPANIES_PATH: Path = Path(_EMAILED_PATH).expanduser().resolve()

# Optional per-agent OpenAI model
OPENAI_DISCOVERY_MODEL: str | None = os.getenv("OPENAI_DISCOVERY_MODEL") or None
OPENAI_EMAIL_FINDER_MODEL: str | None = os.getenv("OPENAI_EMAIL_FINDER_MODEL") or None
OPENAI_WRITER_MODEL: str | None = os.getenv("OPENAI_WRITER_MODEL") or None
