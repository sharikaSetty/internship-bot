"""Step 3: poll Telegram for resume PDFs, score them, send feedback."""
import datetime
import io
import json
import math
import re

from . import gemini, telegram
from .config import cfg
from .prompts import JUDGE_SCHEMA, JUDGE_SYS
from .util import esc

BANNED = ["leveraged", "spearheaded", "utilized", "utilised", "results-driven", "passionate", "dynamic",
          "cutting-edge", "synergy", "responsible for", "worked on", "hardworking", "team player"]
BULLET = re.compile(r"^\s*[•▪●◦·\-–—*]\s+")


def pdf_to_text(data):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def parse_upload(message, authorized_chat):
    """Returns (ok, reason, job_code, file_id) for a Telegram message, or None to ignore it."""
    if str((message.get("chat") or {}).get("id")) != str(authorized_chat) or not message.get("document"):
        return None
    doc = message["document"]
    reply = message.get("reply_to_message") or {}
    src = " ".join(x for x in [reply.get("text"), reply.get("caption"), message.get("caption")] if x)
    hit = re.search(r"J-([A-Z0-9]{6})", src, flags=re.I)
    name = str(doc.get("file_name") or "")
    is_pdf = bool(re.search("pdf", doc.get("mime_type") or "", re.I)) or name.lower().endswith(".pdf")
    if not is_pdf:
        return (False, "Please send your resume as a PDF file.", "", "")
    if not hit:
        return (False, "I could not tell which job this resume is for. Reply directly to the bot message for that "
                       "job (or add its #J-CODE as the caption).", "", "")
    return (True, "", "J-" + hit.group(1).upper(), doc.get("file_id", ""))


def extract_bullets(resume):
    lines = [l.rstrip() for l in resume.replace("\r", "").split("\n")]
    bullets, cur = [], None
    for l in lines:
        if BULLET.match(l):
            if cur is not None:
                bullets.append(cur)
            cur = BULLET.sub("", l, count=1).strip()
        elif cur is not None:
            if not l.strip() or (len(l.strip()) < 40 and l == l.upper()):
                bullets.append(cur)
                cur = None
            else:
                cur += " " + l.strip()
    if cur is not None:
        bullets.append(cur)
    bullets = [b for b in bullets if len(b.split()) >= 6]
    if len(bullets) < 4:
        bullets = [l.strip() for l in lines if 8 <= len(l.split()) <= 45]
    return bullets


def _tok(t):
    t = re.sub(r"\[add metric[^\]]*\]", " ", str(t).lower())
    return [w for w in re.sub(r"[^a-z0-9%\s]", " ", t).split() if w]


def _grams(t):
    w = _tok(t)
    return {" ".join(w[i:i + 4]) for i in range(len(w) - 3)}


def _clamp(x, a, b):
    return min(b, max(a, x))


def score_resume(resume, job):
    """Deterministic writing-pattern checks. Returns dict(score, parts, overlap, tips, notes)."""
    if len(resume.strip()) < 200:
        raise RuntimeError("Could not read text from the PDF (it may be a scanned image). Please send a text-based PDF.")
    bullets = extract_bullets(resume)
    n = max(len(bullets), 1)
    lens = [len(b.split()) for b in bullets]
    mean = sum(lens) / len(lens) if lens else 0
    sd = math.sqrt(sum((l - mean) ** 2 for l in lens) / len(lens)) if lens else 0
    cv = sd / mean if mean else 0
    num_share = sum(1 for b in bullets if re.search(r"\b\d", b)) / n
    verbs = {}
    for b in bullets:
        v = re.sub(r"[^a-z]", "", (b.split() or [""])[0].lower())
        if v:
            verbs[v] = verbs.get(v, 0) + 1
    max_repeat = max(verbs.values()) if verbs else 0
    low = resume.lower()
    cliche = [b for b in BANNED if b in low]
    try:
        sent_text = " ".join(b.get("text", "") for s in json.loads(job.get("bullets_json") or "[]")
                             for b in (s.get("bullets") or []))
    except Exception:
        sent_text = ""
    g1, g2 = _grams(" ".join(bullets)), _grams(sent_text)
    overlap = len(g1 & g2) / len(g1) if g1 else 0
    try:
        strategy = json.loads(job.get("strategy_json") or "{}")
    except Exception:
        strategy = {}
    supported = strategy.get("keywords_supported") or []
    kw_used = [k for k in supported if str(k).lower() in low]
    kw_ratio = len(kw_used) / len(supported) if supported else None
    total = len(resume.split())
    parts = {
        "variation": _clamp(cv / 0.30, 0, 1) * 10,
        "numbers": _clamp(num_share / 0.5, 0, 1) * 10,
        "verbs": _clamp(10 - max(0, max_repeat - 2) * 2.5, 0, 10),
        "cliches": _clamp(10 - len(cliche) * 3, 0, 10),
        "originality": _clamp((0.5 - overlap) / 0.4, 0, 1) * 10,
        "length": 10 if 250 <= total <= 700 else _clamp(10 - abs(250 - total if total < 250 else total - 700) / 50, 0, 10),
    }
    weights = {"variation": 0.15, "numbers": 0.2, "verbs": 0.1, "cliches": 0.15, "originality": 0.25, "length": 0.05}
    if kw_ratio is not None:
        parts["keywords"] = kw_ratio * 10
        weights["keywords"] = 0.10
    score = sum(parts[k] * weights[k] for k in weights) / sum(weights.values())
    tips = []
    if overlap > 0.25:
        tips.append(f"About {round(overlap * 100)}% of your phrases match the bot bullets word-for-word. Rewrite them fully in your own words.")
    if num_share < 0.35:
        tips.append(f"Only {round(num_share * 100)}% of bullets contain a real number. Add measurable outcomes (users, %, time saved, size of data).")
    if cv < 0.2:
        tips.append("Bullet lengths are very uniform. Mix short punchy bullets with longer ones.")
    if max_repeat > 2:
        tips.append("You start several bullets with the same verb. Vary the openings.")
    if cliche:
        tips.append("Replace stock phrases: " + ", ".join(cliche) + ".")
    if kw_ratio is not None and kw_ratio < 0.5:
        tips.append("Work in job keywords you can honestly back up: " + ", ".join(k for k in supported if k not in kw_used) + ".")
    if total > 700:
        tips.append(f"The resume is long ({total} words). Aim for one page.")
    notes = [f"Overlap with bot bullets: {round(overlap * 100)}%", f"Bullets with numbers: {round(num_share * 100)}%",
             f"Bullet length variation: {cv:.2f}", f"Stock phrases found: {len(cliche)}",
             "Job keywords used: " + ("n/a" if kw_ratio is None else f"{len(kw_used)}/{len(supported)}")]
    return {"score": score, "parts": parts, "overlap": overlap, "tips": tips, "notes": notes, "strategy": strategy}


def judge(resume, job, det, call=None):
    call = call or gemini.call
    user = (f"JOB: {job.get('title')} at {job.get('company')}\nREAL PRIORITIES:\n"
            + "\n".join(det["strategy"].get("real_priorities") or []) + f"\n\n<resume>\n{resume[:9000]}\n</resume>")
    return call(cfg.pro, JUDGE_SYS, user, JUDGE_SCHEMA, 0, 16384)


def combine(det, jd, job, file_id):
    num = lambda x: float(x or 0)
    human = (num(jd.get("voice_score")) + num(jd.get("specificity_score")) + num(jd.get("rhythm_score"))) / 3
    composite = 0.4 * det["score"] + 0.4 * human + 0.2 * num(jd.get("alignment_score"))
    if det["overlap"] > 0.5:
        composite = min(composite, 5.5)
    composite = round(composite * 10) / 10
    thr = cfg.pass_threshold
    passed = composite >= thr and det["overlap"] <= 0.35
    L = [("✅" if passed else "❌") + f" <b>{esc(job.get('company'))} — {esc(job.get('title'))}</b>",
         f"<code>{esc(job.get('job_code'))}</code>",
         f"Score: <b>{composite}/10</b> — {'PASS' if passed else 'NOT YET'} (pass mark {thr:g})", "",
         "<b>Breakdown</b>",
         f"• Voice {jd.get('voice_score')} · Specificity {jd.get('specificity_score')} · Rhythm {jd.get('rhythm_score')} (out of 10)",
         f"• Fit to the job: {jd.get('alignment_score')}/10",
         f"• Writing-pattern checks: {det['score']:.1f}/10"] + ["   – " + esc(n) for n in det["notes"]]
    if jd.get("strengths"):
        L += ["", "<b>What works</b>"] + ["• " + esc(s) for s in jd["strengths"][:3]]
    fixes = list(jd.get("improvements") or [])[:5] + det["tips"]
    if fixes:
        L += ["", "<b>" + ("Optional polish" if passed else "Work on these") + "</b>"]
        L += [f"{i}. {esc(f)}" for i, f in enumerate(fixes[:8], 1)]
    if jd.get("ai_tells"):
        L += ["", "<b>Lines that read as generic</b>"] + [f"• “{esc(t.get('quote'))}” — {esc(t.get('why'))}" for t in jd["ai_tells"][:4]]
    L += ["", "<i>This score is a proxy for how human and job-fit the resume reads. Real AI detectors are proprietary "
              "and inconsistent, so no score guarantees how any of them will rate it.</i>"]
    text = "\n".join(L)
    if len(text) > 3900:
        text = text[:3900] + "…"
    return {"text": text, "passed": passed, "composite": composite,
            "caption": ("✅ " if passed else "❌ ") + f"{job.get('job_code')} · {composite}/10 — {'pass' if passed else 'needs work'}",
            "file_id": file_id}


def handle_update(u, sheet, tg, call, extract=pdf_to_text):
    msg = u.get("message")
    if not msg:
        return 0
    parsed = parse_upload(msg, cfg.tg_chat)
    if parsed is None:
        return 0
    ok, reason, code, file_id = parsed
    if not ok:
        tg.send_message(esc(reason))
        return 1
    job = next((r for r in sheet.read("jobs") if r.get("job_code") == code), None)
    if not job:
        tg.send_message(f"I could not find job {esc(code)} in the sheet.")
        return 1
    resume = extract(tg.download_file(file_id))
    det = score_resume(resume, job)
    jd = judge(resume, job, det, call)
    res = combine(det, jd, job, file_id)
    tg.send_document(file_id, res["caption"])
    tg.send_message(res["text"])
    sheet.update("jobs", "job_key", job["job_key"], {
        "status": "resume_passed" if res["passed"] else "resume_needs_work",
        "resume_checks": int(float(job.get("resume_checks") or 0)) + 1, "last_score": res["composite"],
        "last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "last_feedback": res["text"][:1500]})
    return 1


def check_uploads(sheet, tg=telegram, call=None, extract=pdf_to_text):
    raw = sheet.get_state("telegram_offset", "")
    offset = int(raw) if str(raw).strip().lstrip("-").isdigit() else None
    updates = tg.get_updates(offset)
    handled = 0
    for u in updates:
        try:
            handled += handle_update(u, sheet, tg, call, extract)
        except Exception as e:
            print(f"check-uploads: update failed ({type(e).__name__})")  # no details: logs are public
            try:
                tg.send_message("⚠️ I could not process that upload: " + esc(str(e)[:250]))
            except Exception:
                pass
        sheet.set_state("telegram_offset", u["update_id"] + 1)
    print(f"check-uploads: {len(updates)} updates, {handled} handled")
    return handled
