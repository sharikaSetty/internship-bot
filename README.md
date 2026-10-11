# Internship bot on GitHub Actions (free, no laptop needed)

Same pipeline as the n8n version, written as a small Python project that GitHub runs on a schedule:

```
Gmail label ──► daily run (08:00 IST) ──► Google Sheet (jobs tab) ──► Telegram: strategy + bullets
                  Gemini extracts listings            ▲
                  keeps remote + paid, dedupes        │
                  4-call Gemini chain + verifier      │
                                                      │
you write resume ─► reply with PDF ─► check run (every 15 min) ─► score + feedback ─► Telegram
```

| Part | What runs | When |
|---|---|---|
| `daily.yml` | read Gmail → extract → filter → dedupe → bullets → Telegram | 08:00 IST daily |
| `check-uploads.yml` | look for your resume PDF replies on Telegram → score → feedback | every 15 min |
| `keepalive.yml` | one empty commit a month so GitHub doesn't switch the schedules off | 1st of each month |

Failures send you a Telegram message with a link to the run.

---

## Before you start

1. **Unpublish WF1, WF2 and WF3 in n8n** (and stop the local n8n). Otherwise both systems would send you messages, and Telegram will not let a bot use polling while an n8n webhook is registered.
2. You can **reuse your Google Sheet** (`jobs` and `profile` tabs). The job codes are computed the same way as in n8n, so existing rows keep working.

## Step 1: Google service account (lets the bot read and write your Sheet)

1. Go to console.cloud.google.com → create or pick a project.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google Drive API**.
3. **IAM & Admin → Service Accounts → Create service account** (any name, no roles needed).
4. Open it → **Keys → Add key → Create new key → JSON**. A `.json` file downloads. Keep it private.
5. Open that JSON and copy the `client_email` value. **Share your Google Sheet with that email as Editor.**

The whole JSON file's contents become the secret `GOOGLE_SERVICE_ACCOUNT_JSON` (Step 5).

## Step 2: Gmail app password (lets the bot read the `internships` label)

1. Your Google account needs **2-Step Verification** turned on.
2. Go to myaccount.google.com/apppasswords → create one (name it "internship bot") → copy the 16 characters.
3. Make sure the label `internships` exists and your alert emails get it (see the n8n guide, section 4). The label name is case-sensitive.
4. If login fails later, check that IMAP is enabled in Gmail → Settings → Forwarding and POP/IMAP.

## Step 3: Telegram and Gemini

- Reuse your bot token and your chat ID (send your bot a message first).
- Reuse your Gemini API key from Google AI Studio.
- **Remove the old n8n webhook once:** open this in a browser (put your token in):
  `https://api.telegram.org/bot<YOUR_TOKEN>/deleteWebhook`
  It should answer `{"ok":true,...}`. Do not share that URL, it contains your token.

## Step 4: Create the public repo

1. On github.com → **New repository** → name it (for example `internship-bot`) → **Public** → create.
2. Upload this project. Easiest with git:
   ```bash
   cd internship-bot
   git init -b main
   git add .
   git commit -m "Internship bot"
   git remote add origin https://github.com/<your-username>/internship-bot.git
   git push -u origin main
   ```
   The hidden `.github/workflows` folder must be included. Check that the **Actions** tab shows three workflows.

## Step 5: Add secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add all seven:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | your Gemini key |
| `TELEGRAM_BOT_TOKEN` | your bot token |
| `TELEGRAM_CHAT_ID` | your chat ID (digits only) |
| `SHEET_ID` | the ID from your Sheet's URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the full contents of the downloaded JSON file |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password |

Optional **Variables** (same page, Variables tab); none are needed to start:
`GEMINI_FLASH_MODEL`, `GEMINI_PRO_MODEL` (defaults `gemini-2.5-flash` and `gemini-2.5-pro`), `GMAIL_LABEL`, `GMAIL_LOOKBACK_DAYS` (7), `GMAIL_MAX_EMAILS` (1), `ALLOWED_MODES` (remote), `KEEP_UNKNOWN_STIPEND` (false), `MAX_JOBS_PER_DAY` (5), `MIN_KW_COVERAGE` (0.4), `PASS_THRESHOLD` (7).

> **Gemini quota:** only the **newest `GMAIL_MAX_EMAILS` email (default 1)** is sent to Gemini on each run. Older alerts in the label are skipped, so raise `GMAIL_MAX_EMAILS` (for example to 3) if you are fine spending more quota and want deeper backfill. Gmail still reads the whole label; only extraction is capped.

## Step 6: First runs (do these by hand)

1. Make sure the `profile` tab is filled in (15–25 real facts; see `sheet_templates/profile_template.csv`).
2. **Actions → Daily internships and bullets → Run workflow.** Watch the log: you should see lines like `ingest: 1 email, 0 failed, 3 new jobs` (only the latest alert is extracted) and `generate: 3 picked, 3 sent, 0 failed`. You then get Telegram messages.
3. Reply to one bot message with a PDF. Then **Actions → Check resume uploads → Run workflow** (or wait up to 15 minutes). You should get your PDF back with the score.

## Schedules and timing

- GitHub uses **UTC**. `30 2 * * *` is 08:00 IST. Change the `cron:` line in `.github/workflows/daily.yml` to move it (IST = UTC + 5:30).
- GitHub can start scheduled runs a few minutes late, sometimes more at busy times.
- Resume feedback arrives within about 15 minutes of your upload. To speed it up, lower the `*/15` in `check-uploads.yml` (GitHub's minimum is 5 minutes).
- The bot works through the Telegram messages it has not seen yet and remembers its place in a small `state` tab of your Sheet (created automatically).

## Public repo: keep it safe

- **Secrets stay secret.** They live in GitHub Secrets, not in the code, and are hidden in logs.
- **Logs are public.** The bot prints only counts and job codes, never email text, resume text or company details. If you edit the code, keep it that way.
- **Never commit** the service-account JSON, `.env` files or screenshots with tokens. If a token leaks, revoke it (BotFather `/revoke`, Google account → app passwords, AI Studio → API keys) and update the secret.
- Your profile facts, jobs and resume stay in your private Google Sheet. The code and prompts are public, which is fine.
- GitHub switches off scheduled workflows in a public repo after 60 days without activity. `keepalive.yml` prevents that with one empty commit a month. If schedules ever stop, open the Actions tab and re-enable them.

## Run the tests (optional)

```bash
pip install -r requirements.txt pytest
python -m pytest -q      # 17 offline tests, no keys needed
```

## What I tested, and what you should check first

Tested offline with fake Sheets, Telegram and Gemini: filtering and dedupe, the four-call chain with retry, the verifier (invented numbers, stock phrases, keyword coverage), message chunking, resume scoring, upload matching, one bad upload not blocking the queue, a failed job not blocking others, and job-code compatibility with the n8n version.

**Not tested against the live services**, so check these on the first runs:

1. **Gmail over IMAP:** the label folder name must match `GMAIL_LABEL` exactly.
2. **Google Sheets access:** the Sheet must be shared with the service account's `client_email`, and both APIs enabled.
3. **Telegram:** the old n8n webhook must be removed (Step 3) or you will see a "409 conflict" error.
4. **Gemini model names:** if Google retires the default names, set `GEMINI_FLASH_MODEL` and `GEMINI_PRO_MODEL` as variables.
5. **Action versions:** the workflows use `actions/checkout@v4` and `actions/setup-python@v5`. If GitHub shows a deprecation warning, bump the version numbers.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Missing required setting: …` | A secret is not added or is misspelled |
| `Gmail label "internships" was not found` | The label name differs, or IMAP is off |
| `gspread … PermissionError / 403` | Sheet not shared with the service account email |
| `Column "…" missing in tab "jobs"` | The `jobs` header row doesn't match `sheet_templates/jobs_header.csv` |
| `Telegram getUpdates: 409 …` | The n8n webhook is still set, run the `deleteWebhook` link again |
| Nothing arrives after 08:00 | Check the Actions tab: scheduled runs can be late, or the schedule was disabled |
| A job keeps failing | After 3 failed attempts it is skipped. Fix the cause, then set its `status` to `new` and `attempts` to `0` |

## Back to n8n later

The Google Sheet is the single source of truth, so you can switch back to the n8n workflows (or a server) at any time. Just unpublish this repo's workflows first so they don't run twice.
