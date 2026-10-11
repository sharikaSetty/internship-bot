import argparse
import sys

from . import telegram
from .bullets import run_generate
from .ingest import run_ingest
from .resume_check import check_uploads
from .sheets import Sheet


def main(argv=None):
    p = argparse.ArgumentParser(description="Internship + resume bot")
    p.add_argument("command", choices=["daily", "ingest", "generate", "check-uploads", "reset-webhook"])
    cmd = p.parse_args(argv).command
    if cmd == "reset-webhook":  # one-time: removes the n8n webhook so getUpdates works
        telegram.delete_webhook()
        print("Telegram webhook removed.")
        return 0
    sheet = Sheet()
    errors = []
    if cmd in ("daily", "ingest"):
        try:
            run_ingest(sheet)
        except Exception as e:
            errors.append(f"ingest: {type(e).__name__}: {str(e)[:200]}")
    if cmd in ("daily", "generate"):
        try:
            run_generate(sheet)
        except Exception as e:
            errors.append(f"generate: {type(e).__name__}: {str(e)[:200]}")
    if cmd == "check-uploads":
        check_uploads(sheet)
    if errors:
        for e in errors:
            print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
