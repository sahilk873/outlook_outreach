"""Pydantic models for structured agent outputs."""
from pydantic import BaseModel, Field


class StartupItem(BaseModel):
    """A single startup from discovery or list file."""

    name: str = Field(description="Company or startup name")
    domain: str = Field(description="Primary domain, e.g. example.com")
    one_liner: str = Field(
        default="",
        max_length=120,
        description="Short description or tagline (one sentence, max 120 chars)",
    )
    email: str | None = Field(
        default=None,
        description="Optional contact email; when set (e.g. from list file), email-finding is skipped",
    )


class DiscoveryOutput(BaseModel):
    """Structured output from the discovery agent: list of startups."""

    startups: list[StartupItem] = Field(
        default_factory=list,
        description="List of startups matching the criteria",
    )


class EmailDraft(BaseModel):
    """Structured output from the writer agent: subject and body."""

    subject: str = Field(description="Email subject line")
    body_plain: str = Field(description="Plain-text email body")
