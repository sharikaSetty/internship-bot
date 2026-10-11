"""Settings come from environment variables (GitHub Secrets / Variables). Nothing is hard-coded."""
import os


def _get(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _need(name):
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required setting: {name}")
    return v


class Config:
    # ----- secrets (read lazily so tests and --help work without them)
    @property
    def gemini_key(self): return _need("GEMINI_API_KEY")
    @property
    def tg_token(self): return _need("TELEGRAM_BOT_TOKEN")
    @property
    def tg_chat(self): return str(_need("TELEGRAM_CHAT_ID"))
    @property
    def sheet_id(self): return _need("SHEET_ID")
    @property
    def sa_json(self): return _need("GOOGLE_SERVICE_ACCOUNT_JSON")
    @property
    def gmail_address(self): return _need("GMAIL_ADDRESS")
    @property
    def gmail_password(self): return _need("GMAIL_APP_PASSWORD")

    # ----- tunables (all optional)
    @property
    def flash(self): return _get("GEMINI_FLASH_MODEL", "gemini-2.5-flash")
    @property
    def pro(self): return _get("GEMINI_PRO_MODEL", "gemini-2.5-pro")
    @property
    def min_gap(self): return float(_get("GEMINI_MIN_GAP_SECONDS", "6"))
    @property
    def gmail_label(self): return _get("GMAIL_LABEL", "internships")
    @property
    def lookback_days(self): return int(_get("GMAIL_LOOKBACK_DAYS", "7"))
    @property
    def allowed_modes(self): return [m.strip().lower() for m in _get("ALLOWED_MODES", "remote").split(",") if m.strip()]
    @property
    def keep_unknown_stipend(self): return _get("KEEP_UNKNOWN_STIPEND", "false").lower() == "true"
    @property
    def max_jobs(self): return int(_get("MAX_JOBS_PER_DAY", "5"))
    @property
    def min_kw_coverage(self): return float(_get("MIN_KW_COVERAGE", "0.4"))
    @property
    def pass_threshold(self): return float(_get("PASS_THRESHOLD", "7"))
    @property
    def job_gap(self): return float(_get("JOB_GAP_SECONDS", "15"))


cfg = Config()
