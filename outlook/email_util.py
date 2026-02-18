"""Email helpers for send flow (normalization, etc.)."""


def normalize_email(email: str) -> str:
    """
    Normalize an email address for sending: strip whitespace and trailing/leading
    punctuation (e.g. period, comma) that can cause "unable to send" errors.
    """
    if not email:
        return email
    s = email.strip()
    # Strip trailing punctuation that sometimes gets included (e.g. "user@domain.com.")
    while len(s) > 0 and s[-1] in ".,;:\"'":
        s = s[:-1]
    while len(s) > 0 and s[0] in ".,;:\"'":
        s = s[1:]
    return s.strip()
