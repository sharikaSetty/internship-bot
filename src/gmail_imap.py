"""Reads labelled job-alert emails over IMAP using a Gmail app password."""
import datetime
import email
import imaplib
from email import policy

from .config import cfg
from .util import html_to_text

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch_emails(lookback_days, label, limit=40):
    d = datetime.date.today() - datetime.timedelta(days=lookback_days)
    since = f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"
    out = []
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(cfg.gmail_address, cfg.gmail_password)
        typ, _ = imap.select(f'"{label}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f'Gmail label "{label}" was not found over IMAP')
        typ, data = imap.search(None, "SINCE", since)
        ids = (data[0] or b"").split()[-limit:]
        print(f"Gmail IMAP: found {len(ids)} emails in '{label}' since {since}", flush=True)
        for i in ids:
            typ, msg_data = imap.fetch(i, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
            part = msg.get_body(preferencelist=("html", "plain"))
            if part is None:
                continue
            content = part.get_content()
            text = html_to_text(content) if part.get_content_type() == "text/html" else str(content)
            text = text.strip()[:14000]
            if not text:
                continue
            out.append({"msg_id": str(msg.get("Message-ID", "")), "subject": str(msg.get("Subject", "")),
                        "from": str(msg.get("From", "")), "text": text})
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return out
