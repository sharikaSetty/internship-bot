"""Step 1: Gmail alerts -> Gemini extraction -> keep remote + paid -> dedupe -> append to the sheet."""
import datetime
import time

from . import gemini, telegram
from .config import cfg
from .gmail_imap import fetch_emails
from .prompts import EXTRACT_SCHEMA, EXTRACT_SYS
from .util import esc, job_hash, slug


def extract_listings(em, call=None):
    call = call or gemini.call
    user = (f"EMAIL SUBJECT: {em['subject']}\nFROM: {em['from']}\n\n<email>\n{em['text']}\n</email>")
    data = call(cfg.flash, EXTRACT_SYS, user, EXTRACT_SCHEMA, 0.1, 8192)
    return data.get("internships") or []


def filter_listings(per_email, existing_keys, existing_codes, allowed, keep_unknown, today=None):
    """per_email: list of (subject, [listing dicts]). Returns new sheet rows."""
    today = today or datetime.date.today().isoformat()
    keys, codes = set(existing_keys), set(existing_codes)
    rows = []
    for subject, listings in per_email:
        for j in listings:
            if not j.get("company") or not j.get("title"):
                continue
            mode = str(j.get("work_mode") or "").lower()
            if mode not in allowed:
                continue
            amount = j.get("stipend_amount")
            paid = j.get("is_paid") is True or (isinstance(amount, (int, float)) and amount > 0)
            if j.get("is_paid") is False or (not paid and not keep_unknown):
                continue
            key = slug(j["company"]) + "|" + slug(j["title"])
            code = "J-" + job_hash(key)
            if key in keys or code in codes:
                continue
            keys.add(key)
            codes.add(code)
            rows.append({
                "job_key": key, "job_code": code, "added_date": today,
                "company": j["company"], "title": j["title"], "location": j.get("location") or "",
                "work_mode": mode, "stipend_text": j.get("stipend_text") or "",
                "stipend_amount": "" if amount is None else amount, "currency": j.get("currency") or "",
                "apply_url": j.get("apply_url") or "", "description_snippet": j.get("description_snippet") or "",
                "deadline": j.get("deadline") or "", "source_subject": subject or "",
                "status": "new", "attempts": 0,
            })
    return rows


def summary_text(rows):
    lines = [f"• <b>{esc(r['company'])}</b> — {esc(r['title'])}" + (f" · {esc(r['stipend_text'])}" if r["stipend_text"] else "")
             + f"\n   <code>{esc(r['job_code'])}</code>" for r in rows[:25]]
    n = len(rows)
    return (f"📥 <b>{n} new remote paid internship{'' if n == 1 else 's'} today</b>\n\n" + "\n".join(lines)
            + "\n\nTailored bullets for the top picks follow shortly.")


def run_ingest(sheet, fetch=fetch_emails, call=None, tg=telegram):
    emails = list(fetch(cfg.lookback_days, cfg.gmail_label))
    # Gemini quota: only the newest GMAIL_MAX_EMAILS message(s) are extracted.
    # fetch() returns messages oldest -> newest, so keep the tail.
    if len(emails) > cfg.max_emails:
        skipped = len(emails) - cfg.max_emails
        emails = emails[-cfg.max_emails:]
        print(f"ingest: skipping {skipped} older emails, extracting the newest {cfg.max_emails}", flush=True)
    per, failed = [], 0
    for idx, em in enumerate(emails, 1):
        print(f"Extracting email {idx}/{len(emails)}: '{em.get('subject', '')[:40]}'...", flush=True)
        try:
            per.append((em["subject"], extract_listings(em, call)))
        except Exception as e:
            failed += 1
            print(f"extract failed for email '{em.get('subject', '')[:40]}': {type(e).__name__}: {e}", flush=True)
    if emails and failed == len(emails):
        raise RuntimeError("Every email failed during extraction")
    existing = sheet.read("jobs")
    rows = filter_listings(per, {r.get("job_key") for r in existing if r.get("job_key")},
                           {r.get("job_code") for r in existing if r.get("job_code")},
                           cfg.allowed_modes, cfg.keep_unknown_stipend)
    sheet.append("jobs", rows)
    if rows:
        tg.send_message(summary_text(rows))
    # counts only: logs are public
    print(f"ingest: {len(emails)} email{'' if len(emails) == 1 else 's'}, {failed} failed, {len(rows)} new jobs")
    return rows
