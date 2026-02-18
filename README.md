# Outlook Automation – Startup Outreach

Agentic pipeline built with the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python): discover startups, find contact emails, draft personalized emails, and **send via Outlook Web** using Playwright browser automation.

This project mirrors the Gmail-based [gmail-automation](../gmail-automation) pipeline but uses Outlook Web instead of the Gmail API. Ideal if you primarily use Outlook (e.g. work/school accounts).

## Flow

1. **Discovery** – Describe criteria (e.g. “Seed-stage B2B SaaS in fintech”) and the discovery agent uses web search to produce a structured list of startups (name, domain, one-liner). If you set `list_file` to a path (one company per line; format `Name | domain.com | one-liner` with optional 4th column `email`), discovery is skipped and that list is used as-is. When the optional email column is provided, email-finding is skipped for that row.
2. **Per company** – For each startup: use pre-filled email from the list (if any), otherwise run email-finder to find contact email; then writer drafts personalized subject + body.
3. **Review & send** – By default you are prompted “Send this email? [y/N]” after each draft. Sending is done via **Playwright**: it opens Outlook Web, fills To/Subject/Body, attaches files, and clicks Send.

## Setup

### 1. Python and dependencies

- Python 3.10+
- From the project root:

  ```bash
  python3 -m venv .venv
  source .venv/bin/activate   # Windows: .venv\Scripts\activate
  pip install -r requirements.txt
  playwright install
  ```

### 2. Environment variables

Copy `.env.example` to `.env` and set:

- **`OPENAI_API_KEY`** (required) – OpenAI API key for the Agents SDK.
- Optional: per-agent models, session path, emailed-companies path.

### 3. First run (Outlook login)

The first time you run the pipeline:

- A **browser window** opens (not headless).
- You are taken to Outlook Web (`https://outlook.office.com/mail/`).
- If not logged in, **log in manually** in the browser.
- Once the mail inbox loads, the script saves your session to `outlook_session.json`.
- On subsequent runs, the session is reused and you typically won’t need to log in again.

> **Important:** Run the first time with a visible browser (no `--headless`). After the session is saved, you can use `--headless` if desired.

## Usage

From the project root:

**Using a config file (recommended):** copy `outreach.example.yaml` to e.g. `outreach.yaml`, edit it, then:

```bash
python main.py --config outreach.yaml
# or
python main.py -c outreach.yaml
```

**Using CLI only:**

```bash
# Discover startups, draft emails, confirm before send
python main.py --criteria "Y Combinator W25 fintech startups" --purpose "partnership intro"

# Use a company list file (skips discovery; one per line: Name | domain.com | one-liner, optional 4th: email)
python main.py --list-file startups.txt --purpose "sales intro" --tone friendly

# Limit to 3 startups, send without confirmation
python main.py --criteria "B2B SaaS seed stage" --max-startups 3 --no-confirm

# Attach files to each email
python main.py --criteria "Seed-stage B2B SaaS" --attach pitch.pdf deck.docx --purpose "partnership"

# Run headless (only after session is saved)
python main.py -c outreach.validate.yaml --headless
```

- **Default**: After each draft you are prompted “Send this email? [y/N]”.
- **`--no-confirm`**: Sends each draft immediately without asking.
- **`--attach FILE [FILE ...]`**: Attach files to each email (paths relative to current directory or absolute). In YAML config use `attach: [path/to/resume.pdf, path/to/deck.pdf]`.
- **`--headless`**: Run browser headless (requires existing session).

**Validate pipeline (single email):**

```bash
python main.py -c outreach.validate.yaml
```

## Project layout

- `main.py` – CLI entry; parses args and runs the manager.
- `manager.py` – Orchestrator: discovery → find email → draft → (optional confirm) → send via Outlook Web.
- `config.py` – Loads env (OpenAI key, Outlook session path, emailed-companies path).
- `outlook/send.py` – Playwright automation: login, compose, fill To/Subject/Body, attach, send.
- `outreach_agents/` – Discovery, email-finder, writer agents (same as Gmail project).
- `outreach.example.yaml` – Example config (same structure as Gmail project).

## Outlook Web selectors

Outlook Web’s UI can change. If the automation fails to find elements (e.g. “New mail”, “Send”), update the selectors in `outlook/send.py`. The current selectors target common aria-labels and placeholders.

## Safety and compliance

- **Human-in-the-loop**: Prefer the default (confirm before send). Use `--no-confirm` only in controlled settings.
- **Secrets**: Keep `OPENAI_API_KEY` and `outlook_session.json` out of version control.
- **Session**: `outlook_session.json` contains auth cookies. Treat it like a credential file.
