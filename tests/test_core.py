"""Offline tests: no network, no Google, no Telegram. Run with:  python -m pytest -q"""
import json
import os

os.environ["GEMINI_MIN_GAP_SECONDS"] = "0"
os.environ["JOB_GAP_SECONDS"] = "0"
os.environ["TELEGRAM_CHAT_ID"] = "123"

from src import bullets, ingest, resume_check  # noqa: E402
from src.util import html_to_text, job_hash, slug  # noqa: E402


# ------------------------------------------------------------------ fakes
class FakeSheet:
    def __init__(self, jobs=None, profile=None):
        self.tabs = {"jobs": jobs or [], "profile": profile or []}
        self.state = {}

    def read(self, tab):
        return [dict(r) for r in self.tabs[tab]]

    def append(self, tab, rows):
        self.tabs[tab].extend(rows)

    def update(self, tab, key_col, key, fields):
        for r in self.tabs[tab]:
            if r[key_col] == key:
                r.update(fields)
                return
        raise KeyError(key)

    def get_state(self, k, default=""):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = str(v)


class FakeTG:
    def __init__(self, updates=None, files=None):
        self.sent, self.docs = [], []
        self.updates, self.files = updates or [], files or {}

    def send_message(self, text, chat=None):
        self.sent.append(text)

    def send_document(self, file_id, caption="", chat=None):
        self.docs.append((file_id, caption))

    def get_updates(self, offset=None):
        self.offset_seen = offset
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def download_file(self, file_id):
        return self.files.get(file_id, b"")


FACTS = [
    {"fact_id": "P01", "item": "QuantumTrade", "type": "project", "fact": "Built a backtesting engine with FastAPI and Supabase, tested on 5 years of data", "tools": "FastAPI, Supabase", "metric": "5 years"},
    {"fact_id": "P02", "item": "Bhumi", "type": "project", "fact": "Designed a GIS land governance platform", "tools": "PostGIS", "metric": ""},
]
JOB = {"job_key": "acme ai|ml intern", "job_code": "J-FZ57FU", "company": "Acme AI", "title": "ML Intern",
       "apply_url": "", "description_snippet": "Build APIs with FastAPI", "status": "new", "attempts": "0",
       "deadline": "", "stipend_amount": "15000"}

GOOD_S4 = {"sections": [
    {"item_name": "QuantumTrade", "bullets": [{"text": "Built a FastAPI backtesting engine on Supabase, replaying 5 years of market data [ADD METRIC: runtime per backtest]", "source_id": "P01", "req_id": "R1", "needs_metric": True}]},
    {"item_name": "Bhumi", "bullets": [{"text": "Designed a PostGIS-backed land governance platform for district officers", "source_id": "P02", "req_id": "R1", "needs_metric": False}]}]}
BAD_S4 = {"sections": [{"item_name": "QuantumTrade", "bullets": [{"text": "Leveraged FastAPI to cut latency by 40% on a backtesting engine", "source_id": "P01", "req_id": "R1", "needs_metric": False}]}]}


def make_llm(s4_sequence):
    """Canned Gemini: answers by the schema it is asked for."""
    seq = list(s4_sequence)
    calls = []

    def call(model, system, user, schema, temperature=0.2, max_tokens=8192):
        props = set(schema.get("properties", {}))
        calls.append(sorted(props)[0])
        if "real_priorities" in props:
            return {"role_title": "ML Intern", "real_priorities": ["APIs", "Data", "Speed"],
                    "requirements": [{"id": "R1", "text": "Backend APIs", "type": "must"}, {"id": "R2", "text": "Rust", "type": "nice"}],
                    "keywords": ["FastAPI", "Supabase", "PostGIS", "Rust"], "focus": "engineering", "jd_quality": "good"}
        if "matches" in props:
            return {"matches": [{"req_id": "R1", "strength": "strong", "evidence_ids": ["P01", "P99"]}, {"req_id": "R2", "strength": "none", "evidence_ids": []}], "gaps": ["R2"]}
        if "plan" in props:
            return {"positioning_line": "Backend-focused student.", "ordering_rationale": "API proof first.",
                    "plan": [{"item_name": "QuantumTrade", "angle": "a", "targets": ["R1"], "fact_ids": ["P01", "ZZ"], "bullet_count": 9},
                             {"item_name": "Bhumi", "angle": "b", "targets": ["R1"], "fact_ids": ["P02"], "bullet_count": 2}], "omit": []}
        if "sections" in props:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        if "voice_score" in props:
            return {"voice_score": 6, "specificity_score": 7, "rhythm_score": 5, "alignment_score": 7,
                    "ai_tells": [{"quote": "improve workflows", "why": "vague"}], "improvements": ["Add numbers to bullet 4"],
                    "strengths": ["Concrete stack"], "verdict": "ok"}
        raise AssertionError(props)

    call.calls = calls
    return call


# ------------------------------------------------------------------ util
def test_job_hash_matches_n8n_version():
    assert job_hash("acme ai|ml intern") == "FZ57FU"
    assert job_hash("beta labs|data science intern") == "6V5SW3"
    assert job_hash("x|y") == "377MQA"


def test_html_to_text_keeps_links():
    t = html_to_text('<p>Apply <a href="https://x.com/a?b=1">here</a> now</p><script>bad()</script>')
    assert "here [https://x.com/a?b=1]" in t and "bad()" not in t


# ------------------------------------------------------------------ ingest
def test_filter_listings():
    per = [("Digest A", [
        {"company": "Acme AI", "title": "ML Intern", "work_mode": "remote", "is_paid": True, "stipend_amount": 15000, "stipend_text": "15k"},
        {"company": "Beta", "title": "Web Intern", "work_mode": "onsite", "is_paid": True, "stipend_amount": 9000},
        {"company": "Gamma", "title": "Data Intern", "work_mode": "remote", "is_paid": False},
        {"company": "Delta", "title": "NLP Intern", "work_mode": "remote", "is_paid": None}]),
        ("Digest B", [{"company": "ACME  AI", "title": "ML intern", "work_mode": "remote", "is_paid": True, "stipend_amount": 1}])]
    rows = ingest.filter_listings(per, set(), set(), ["remote"], False, today="2026-10-11")
    assert [r["job_key"] for r in rows] == ["acme ai|ml intern"]
    assert rows[0]["job_code"] == "J-FZ57FU" and rows[0]["status"] == "new"
    # already in the sheet -> skipped
    assert ingest.filter_listings(per, {"acme ai|ml intern"}, set(), ["remote"], False) == []
    # unknown stipend kept only when allowed
    assert len(ingest.filter_listings(per, set(), set(), ["remote"], True)) == 2


def test_run_ingest_end_to_end():
    sheet, tg = FakeSheet(), FakeTG()
    emails = [{"msg_id": "1", "subject": "S", "from": "f", "text": "t"}]

    def call(model, system, user, schema, temperature=0.2, max_tokens=8192):
        return {"internships": [{"company": "Acme AI", "title": "ML Intern", "work_mode": "remote", "is_paid": True, "stipend_amount": 10000}]}

    rows = ingest.run_ingest(sheet, fetch=lambda d, l: emails, call=call, tg=tg)
    assert len(rows) == 1 and len(sheet.tabs["jobs"]) == 1 and "1 new remote paid internship" in tg.sent[0]
    # second run: nothing new, no message
    tg.sent.clear()
    assert ingest.run_ingest(sheet, fetch=lambda d, l: emails, call=call, tg=tg) == [] and tg.sent == []


def test_ingest_extracts_only_the_newest_email():
    """Gemini quota: older unread alerts are not sent to the extractor."""
    sheet, tg = FakeSheet(), FakeTG()
    emails = [{"msg_id": str(i), "subject": f"S{i}", "from": "f", "text": "t"} for i in range(5)]
    seen = []

    def call(model, system, user, schema, temperature=0.2, max_tokens=8192):
        seen.append(user)
        return {"internships": [{"company": "Acme AI", "title": "ML Intern", "work_mode": "remote",
                                  "is_paid": True, "stipend_amount": 10000}]}

    os.environ["GMAIL_MAX_EMAILS"] = "1"
    try:
        rows = ingest.run_ingest(sheet, fetch=lambda d, l: emails, call=call, tg=tg)
    finally:
        del os.environ["GMAIL_MAX_EMAILS"]
    assert len(seen) == 1 and "EMAIL SUBJECT: S4" in seen[0]  # only the latest email
    assert len(rows) == 1 and len(sheet.tabs["jobs"]) == 1


# ------------------------------------------------------------------ bullets
def _state():
    return {"facts": FACTS, "s3": {"plan": [{"item_name": "QuantumTrade"}, {"item_name": "Bhumi"}]},
            "keywords_supported": ["FastAPI", "Supabase", "PostGIS"]}


def test_verify_accepts_good_and_rejects_invented_numbers():
    fails, _, cov = bullets.verify(_state(), GOOD_S4, 0.4)
    assert fails == [] and cov["ratio"] == 1
    fails, _, _ = bullets.verify(_state(), BAD_S4, 0.4)
    text = " ".join(fails)
    assert "40" in text and "leveraged" in text and "Keyword coverage" in text


def test_chain_retries_then_succeeds_and_formats():
    llm = make_llm([BAD_S4, GOOD_S4])
    res = bullets.run_chain(dict(JOB), "jd", "job page", FACTS, call=llm, flash="f", pro="p", min_cov=0.4)
    assert res["verified"] and res["state"]["retry"] == 1
    assert res["state"]["s2"]["matches"][0]["evidence_ids"] == ["P01"]  # P99 filtered
    assert res["state"]["s3"]["plan"][0]["fact_ids"] == ["P01"] and res["state"]["s3"]["plan"][0]["bullet_count"] == 4
    msgs = bullets.format_messages(res)
    assert msgs[0].startswith("<b>#J-FZ57FU</b> (1/") and all(len(m) < 4000 for m in msgs)
    assert "Gaps" in "".join(msgs) and "ADD METRIC" not in "".join(msgs).split("Fill in real numbers for:")[0].split("Bullets")[0]


def test_chain_gives_up_after_two_retries_with_warning():
    res = bullets.run_chain(dict(JOB), "jd", "job page", FACTS, call=make_llm([BAD_S4]), flash="f", pro="p", min_cov=0.4)
    assert not res["verified"] and res["state"]["retry"] == 2
    assert "Some checks still failed" in "".join(bullets.format_messages(res))


def test_run_generate_end_to_end_and_attempt_counter():
    sheet = FakeSheet(jobs=[dict(JOB), {**JOB, "job_key": "old|job", "attempts": "3"}], profile=FACTS)
    tg = FakeTG()
    n = bullets.run_generate(sheet, call=make_llm([GOOD_S4]), tg=tg, page_fetcher=lambda u: "", sleep=lambda s: None)
    row = sheet.tabs["jobs"][0]
    assert n == 1 and row["status"] == "bullets_sent" and row["attempts"] == 1
    assert json.loads(row["strategy_json"])["keywords_supported"] == ["FastAPI", "Supabase", "PostGIS"]
    assert sheet.tabs["jobs"][1]["status"] == "new"  # 3-attempt job skipped
    assert tg.sent and "#J-FZ57FU" in tg.sent[0]


def test_failed_job_does_not_block_others_but_raises():
    j2 = {**JOB, "job_key": "b|b", "job_code": "J-BBBBBB", "stipend_amount": "99999"}
    sheet = FakeSheet(jobs=[dict(JOB), j2], profile=FACTS)
    state = {"n": 0}
    good = make_llm([GOOD_S4])

    def flaky(model, system, user, schema, temperature=0.2, max_tokens=8192):
        if "real_priorities" in schema["properties"]:
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("boom")
        return good(model, system, user, schema, temperature, max_tokens)

    try:
        bullets.run_generate(sheet, call=flaky, tg=FakeTG(), page_fetcher=lambda u: "", sleep=lambda s: None)
        assert False, "expected an error so the failure alert fires"
    except RuntimeError as e:
        assert "1 job(s) failed" in str(e)
    statuses = sorted(r["status"] for r in sheet.tabs["jobs"])
    assert statuses == ["bullets_sent", "new"]
    assert all(int(r["attempts"]) == 1 for r in sheet.tabs["jobs"])


def test_pick_jobs_orders_by_deadline_then_stipend():
    rows = [{"job_key": "a", "status": "new", "attempts": "0", "deadline": "2026-10-20", "stipend_amount": "100"},
            {"job_key": "b", "status": "new", "attempts": "0", "deadline": "2026-10-05", "stipend_amount": "50"},
            {"job_key": "c", "status": "new", "attempts": "0", "deadline": "", "stipend_amount": "900"},
            {"job_key": "d", "status": "bullets_sent", "attempts": "0"}]
    assert [r["job_key"] for r in bullets.pick_jobs(rows, 5)] == ["b", "a", "c"]


def test_private_addresses_are_refused():
    assert bullets.fetch_page("http://127.0.0.1/admin") == ""
    assert bullets.fetch_page("http://169.254.169.254/latest/meta-data") == ""
    assert bullets.fetch_page("file:///etc/passwd") == ""


# ------------------------------------------------------------------ resume check
RESUME = """SHARIKA
PROJECTS
• Built a FastAPI backtesting engine on Supabase, replaying 5 years of market data for strategy tests
• Designed a PostGIS land governance platform used in a state hackathon final round of 40 teams
• Wrote unit tests
  covering the order-matching module and caught 3 race conditions before deployment to production users
• Leveraged AI to improve workflows and synergy across teams in a dynamic environment for stakeholders
EDUCATION
PES University, B.Tech CSE. """ + "Coursework in machine learning and algorithms. " * 6


def _job_row():
    return {**JOB, "bullets_json": json.dumps(GOOD_S4["sections"]),
            "strategy_json": json.dumps({"keywords_supported": ["FastAPI", "Supabase", "PostGIS"], "real_priorities": ["a", "b"]})}


def test_extract_bullets_joins_wrapped_lines():
    b = resume_check.extract_bullets(RESUME)
    assert len(b) == 4 and "race conditions" in b[2] and b[2].startswith("Wrote unit tests covering")


def test_scoring_flags_cliches_and_copying():
    det = resume_check.score_resume(RESUME, _job_row())
    assert any("Replace stock phrases" in t for t in det["tips"]) and det["overlap"] < 0.25
    copied = "\n".join("• " + b["text"].replace("[ADD METRIC: runtime per backtest]", "") for s in GOOD_S4["sections"] for b in s["bullets"]) * 1
    copied = ("• " + GOOD_S4["sections"][0]["bullets"][0]["text"].replace("[ADD METRIC: runtime per backtest]", "") + "\n") * 4 + "x " * 200
    det2 = resume_check.score_resume(copied, _job_row())
    assert det2["overlap"] > 0.5
    jd = {"voice_score": 9, "specificity_score": 9, "rhythm_score": 9, "alignment_score": 9, "ai_tells": [], "improvements": [], "strengths": []}
    res = resume_check.combine(det2, jd, _job_row(), "F1")
    assert res["composite"] <= 5.5 and not res["passed"]  # copying the bot's bullets always fails


def test_unreadable_pdf_is_reported():
    try:
        resume_check.score_resume("short", _job_row())
        assert False
    except RuntimeError as e:
        assert "text-based PDF" in str(e)


def test_parse_upload():
    msg = {"chat": {"id": 123}, "document": {"file_name": "cv.pdf", "mime_type": "application/pdf", "file_id": "F1"},
           "reply_to_message": {"text": "#J-FZ57FU (1/2)\nAcme"}}
    assert resume_check.parse_upload(msg, "123") == (True, "", "J-FZ57FU", "F1")
    assert resume_check.parse_upload({**msg, "chat": {"id": 999}}, "123") is None            # stranger ignored
    assert resume_check.parse_upload({"chat": {"id": 123}, "text": "hi"}, "123") is None      # plain text ignored
    no_code = {**msg, "reply_to_message": None}
    assert resume_check.parse_upload(no_code, "123")[0] is False
    assert resume_check.parse_upload({**no_code, "caption": "for #j-fz57fu"}, "123")[2] == "J-FZ57FU"
    docx = {**msg, "document": {"file_name": "cv.docx", "mime_type": "application/msword", "file_id": "F2"}}
    assert resume_check.parse_upload(docx, "123")[1].startswith("Please send your resume as a PDF")


def test_check_uploads_end_to_end_and_offset_state():
    sheet = FakeSheet(jobs=[_job_row()])
    upd = {"update_id": 41, "message": {"chat": {"id": 123}, "document": {"file_name": "cv.pdf", "mime_type": "application/pdf", "file_id": "F1"},
                                         "reply_to_message": {"text": "#J-FZ57FU (1/2)"}}}
    stranger = {"update_id": 42, "message": {"chat": {"id": 7}, "document": {"file_name": "x.pdf", "mime_type": "application/pdf", "file_id": "F9"}}}
    tg = FakeTG(updates=[upd, stranger], files={"F1": b"pdfbytes"})
    handled = resume_check.check_uploads(sheet, tg=tg, call=make_llm([GOOD_S4]), extract=lambda b: RESUME)
    row = sheet.tabs["jobs"][0]
    assert handled == 1 and row["status"] in ("resume_passed", "resume_needs_work") and row["resume_checks"] == 1
    assert tg.docs and tg.docs[0][0] == "F1" and "Score:" in tg.sent[-1]
    assert sheet.state["telegram_offset"] == "43"
    # next poll starts after the last update, so nothing is processed twice
    tg.sent.clear(); tg.docs.clear()
    assert resume_check.check_uploads(sheet, tg=tg, call=make_llm([GOOD_S4]), extract=lambda b: RESUME) == 0
    assert tg.offset_seen == 43 and not tg.sent


def test_one_bad_upload_does_not_block_the_queue():
    sheet = FakeSheet(jobs=[_job_row()])
    upd = {"update_id": 5, "message": {"chat": {"id": 123}, "document": {"file_name": "cv.pdf", "mime_type": "application/pdf", "file_id": "F1"},
                                        "reply_to_message": {"text": "#J-FZ57FU"}}}
    tg = FakeTG(updates=[upd], files={"F1": b"x"})
    resume_check.check_uploads(sheet, tg=tg, call=make_llm([GOOD_S4]), extract=lambda b: "too short")
    assert sheet.state["telegram_offset"] == "6" and "could not process" in tg.sent[-1]
