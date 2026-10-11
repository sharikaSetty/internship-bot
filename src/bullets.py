"""Step 2: per job, a 4-call Gemini chain (JD analyst -> evidence matcher -> strategist -> bullet writer)
plus a code verifier that rejects invented numbers, clichés, repeated verbs and low keyword coverage."""
import datetime
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import requests

from . import gemini, telegram
from .config import cfg
from .prompts import S1_SCHEMA, S1_SYS, S2_SCHEMA, S2_SYS, S3_SCHEMA, S3_SYS, S4_SCHEMA, S4_SYS
from .util import esc, html_to_text

BANNED = ["leveraged", "spearheaded", "utilized", "utilised", "results-driven", "passionate",
          "dynamic", "cutting-edge", "synergy", "responsible for", "worked on"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124 Safari/537.36")


# ---------------------------------------------------------------- job page fetching
def _public_host(host):
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def fetch_page(url):
    """GET a job page, following up to 5 redirects and refusing private/internal addresses."""
    try:
        for _ in range(6):
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
                return ""
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            return r.text[:300000] if r.status_code == 200 else ""
    except Exception:
        return ""
    return ""


# ---------------------------------------------------------------- helpers
def facts_text(facts, only=None):
    out = []
    for f in facts:
        if only is not None and f["fact_id"] not in only:
            continue
        out.append(f"[{f['fact_id']}] ({f.get('item', '')} | {f.get('type', '')}) {f['fact']}"
                   + (f" | tools: {f['tools']}" if f.get("tools") else "")
                   + (f" | metric: {f['metric']}" if f.get("metric") else ""))
    return "\n".join(out)


def parse_deadline(s):
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d-%m-%Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt).timestamp()
        except ValueError:
            pass
    return float("inf")


def pick_jobs(rows, max_jobs):
    rows = [r for r in rows if r.get("job_key") and r.get("status") == "new" and int(float(r.get("attempts") or 0)) < 3]
    rows.sort(key=lambda r: (parse_deadline(r.get("deadline", "")), -float(r.get("stipend_amount") or 0)))
    return rows[:max_jobs]


def _nums(text):
    t = re.sub(r"\[ADD METRIC:[^\]]*\]", "", str(text), flags=re.I)
    return [x.replace(",", "").replace("%", "") for x in re.findall(r"\b\d[\d,]*(?:\.\d+)?%?", t)]


def verify(st, s4, min_cov):
    facts = {f["fact_id"]: f for f in st["facts"]}
    plan_items = {p["item_name"] for p in st["s3"]["plan"]}
    fails, bullets, verbs = [], [], {}
    for sec in s4.get("sections") or []:
        if sec.get("item_name") not in plan_items:
            fails.append(f'Section "{sec.get("item_name")}" is not in the plan.')
        for b in sec.get("bullets") or []:
            bullets.append({**b, "item_name": sec.get("item_name")})
    if not bullets:
        fails.append("No bullets were returned.")
    if len(bullets) > 14:
        fails.append(f"Too many bullets ({len(bullets)}); keep the planned counts (max 12).")
    for b in bullets:
        text = str(b.get("text", ""))
        short = text[:50]
        src = facts.get(b.get("source_id"))
        if not src:
            fails.append(f'Bullet "{short}..." cites unknown source_id "{b.get("source_id")}".')
        else:
            src_text = " ".join(str(v) for v in src.values()).lower().replace(",", "")
            for n in _nums(text):
                if n not in src_text:
                    fails.append(f'Bullet "{short}..." uses the number {n} which is not in {b.get("source_id")}. '
                                 "Remove it or use [ADD METRIC: ...].")
        words = len(re.sub(r"\[ADD METRIC:[^\]]*\]", "", text, flags=re.I).split())
        if words > 32:
            fails.append(f'Bullet "{short}..." is too long ({words} words).')
        low = text.lower()
        for bad in BANNED:
            if bad in low:
                fails.append(f'Bullet "{short}..." contains the banned phrase "{bad}".')
        v = re.sub(r"[^a-z]", "", (low.split() or [""])[0])
        verbs[v] = verbs.get(v, 0) + 1
    for v, n in verbs.items():
        if n > 2:
            fails.append(f'The opening verb "{v}" is used {n} times; vary the verbs.')
    supported = st.get("keywords_supported") or []
    all_text = " ".join(str(b.get("text", "")) for b in bullets).lower()
    used = [k for k in supported if str(k).lower() in all_text]
    missing = [k for k in supported if k not in used]
    ratio = len(used) / len(supported) if supported else 1
    if len(supported) >= 3 and ratio < min_cov:
        fails.append("Keyword coverage is low. Work these in where a fact supports them: " + ", ".join(missing) + ".")
    return fails, bullets, {"supported": supported, "used": used, "missing": missing, "ratio": ratio}


# ---------------------------------------------------------------- the chain
def run_chain(job, jd, jd_source, facts, call=None, flash=None, pro=None, min_cov=None):
    call = call or gemini.call
    flash, pro = flash or cfg.flash, pro or cfg.pro
    min_cov = cfg.min_kw_coverage if min_cov is None else min_cov
    ftext = facts_text(facts)
    ids = {f["fact_id"] for f in facts}
    st = {"job": job, "jd_source": jd_source, "facts": facts}

    s1 = call(flash, S1_SYS, f"COMPANY: {job['company']}\nTITLE: {job['title']}\nSOURCE OF TEXT: {jd_source}\n\n"
              f"<job_posting>\n{jd}\n</job_posting>", S1_SCHEMA, 0.2, 8192)
    if not s1.get("requirements"):
        raise RuntimeError("JD analysis returned no requirements")
    st["s1"] = s1

    s2 = call(pro, S2_SYS, "REQUIREMENTS:\n" + json.dumps(s1["requirements"]) + "\n\nCANDIDATE FACTS:\n" + ftext,
              S2_SCHEMA, 0.1, 16384)
    for m in s2.get("matches") or []:
        m["evidence_ids"] = [x for x in m.get("evidence_ids") or [] if x in ids]
    st["s2"] = s2
    fl = ftext.lower()
    st["keywords_supported"] = [k for k in s1.get("keywords") or [] if str(k).lower() in fl]

    user3 = (f"JOB: {job['title']} at {job['company']}\nREAL PRIORITIES:\n" + "\n".join(s1["real_priorities"])
             + "\n\nREQUIREMENTS:\n" + json.dumps(s1["requirements"]) + "\n\nMATCHES:\n"
             + json.dumps(s2.get("matches") or []) + "\n\nCANDIDATE FACTS:\n" + ftext)
    s3 = call(pro, S3_SYS, user3, S3_SCHEMA, 0.4, 16384)
    plan = []
    for p in (s3.get("plan") or [])[:4]:
        p["fact_ids"] = [x for x in p.get("fact_ids") or [] if x in ids]
        p["bullet_count"] = min(4, max(2, int(p.get("bullet_count") or 3)))
        if p["fact_ids"]:
            plan.append(p)
    if not plan:
        raise RuntimeError("Strategist returned an empty plan")
    s3["plan"] = plan
    st["s3"] = s3

    used_ids = {x for p in plan for x in p["fact_ids"]}
    base4 = (f"JOB: {job['title']} at {job['company']}\nREAL PRIORITIES:\n" + "\n".join(s1["real_priorities"])
             + "\nJOB KEYWORDS (use only where a fact supports them): " + ", ".join(s1.get("keywords") or [])
             + "\nREQUIREMENTS: " + json.dumps(s1["requirements"])
             + "\n\nPLAN (write exactly these sections, in this order):\n" + json.dumps(plan)
             + "\n\nSOURCE FACTS:\n" + facts_text(facts, used_ids))
    retry, feedback, prev = 0, [], []
    while True:
        user4 = base4
        if feedback:
            user4 += ("\n\nYOUR PREVIOUS ATTEMPT HAD THESE PROBLEMS - FIX ALL OF THEM:\n- " + "\n- ".join(feedback)
                      + "\n\nPREVIOUS BULLETS:\n" + "\n".join(prev))
        s4 = call(pro, S4_SYS, user4, S4_SCHEMA, 0.6, 16384)
        fails, bullets, coverage = verify(st, s4, min_cov)
        if not fails or retry >= 2:
            break
        retry += 1
        feedback, prev = fails, [b.get("text", "") for b in bullets]
    st.update({"s4": s4, "bullets": bullets, "coverage": coverage, "retry": retry})
    return {"state": st, "verified": not fails, "fails": fails}


# ---------------------------------------------------------------- Telegram message
def format_messages(result):
    st, fails = result["state"], result["fails"]
    job, s1, s2, s3, cov = st["job"], st["s1"], st["s2"], st["s3"], st["coverage"]
    req = {r["id"]: r["text"] for r in s1["requirements"]}
    head = [f"<b>{esc(job['company'])}</b> — {esc(job['title'])}"]
    if job.get("apply_url"):
        head.append(esc(job["apply_url"]))
    head += ["", "<b>Strategy</b>", esc(s3["positioning_line"]), esc(s3["ordering_rationale"]), "",
             "<b>What this role really hinges on</b>"]
    head += [f"{i}. {esc(p)}" for i, p in enumerate(s1["real_priorities"], 1)]
    head += ["", f"<b>Keyword coverage:</b> {len(cov['used'])}/{len(cov['supported'])} supported keywords"
             + (f" ({esc(', '.join(cov['used']))})" if cov["used"] else "")]
    if cov["missing"]:
        head.append("Supported but unused: " + esc(", ".join(cov["missing"])))
    gaps = [req[g] for g in s2.get("gaps") or [] if g in req]
    if gaps:
        head += ["", "<b>Gaps (no honest evidence in your profile)</b>"] + ["• " + esc(g) for g in gaps]
    if st["jd_source"] != "job page":
        head += ["", "⚠️ The job page could not be read; this is based on the email snippet only."]
    if not result["verified"]:
        head += ["", "⚠️ Some checks still failed after 2 retries. Double-check these:"] + ["• " + esc(f) for f in fails]
    body, refd = [], set()
    for sec in st["s4"].get("sections") or []:
        body += ["", f"<b>{esc(sec.get('item_name'))}</b>"]
        for b in sec.get("bullets") or []:
            body.append(f"• {esc(b.get('text'))} <i>[{esc(b.get('req_id') or '')}]</i>")
            if b.get("req_id"):
                refd.add(b["req_id"])
    metric = []
    for b in st["bullets"]:
        m = re.search(r"\[ADD METRIC:([^\]]*)\]", str(b.get("text", "")), flags=re.I)
        if m:
            metric.append("• " + esc(m.group(1).strip()))
    tail = []
    if metric:
        tail += ["", "<b>Fill in real numbers for:</b>"] + metric
    if refd:
        tail += ["", "<b>Requirement key</b>"] + [f"{esc(i)} — {esc(req[i])}" for i in sorted(refd) if i in req]
    tail += ["", "↩️ Write the resume in your own words, then <b>reply to any message of this job with your PDF</b>."]
    lines = head + ["", "<b>Bullets</b>"] + body + tail
    chunks, cur = [], ""
    for line in lines:
        if len(cur + "\n" + line) > 3400:
            chunks.append(cur)
            cur = line
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        chunks.append(cur)
    code = esc(job["job_code"])
    return [f"<b>#{code}</b> ({i + 1}/{len(chunks)})\n{c}" for i, c in enumerate(chunks)]


def strategy_json(result):
    st = result["state"]
    return json.dumps({
        "positioning_line": st["s3"]["positioning_line"], "ordering_rationale": st["s3"]["ordering_rationale"],
        "real_priorities": st["s1"]["real_priorities"], "keywords": st["s1"].get("keywords"),
        "keywords_supported": st["keywords_supported"], "plan": st["s3"]["plan"],
        "gaps": st["s2"].get("gaps"), "coverage": st["coverage"]["ratio"]})


# ---------------------------------------------------------------- the daily run
def run_generate(sheet, call=None, tg=telegram, page_fetcher=fetch_page, sleep=time.sleep):
    facts = [f for f in sheet.read("profile") if f.get("fact_id") and f.get("fact")]
    if not facts:
        raise RuntimeError('The "profile" tab has no rows with fact_id and fact filled in.')
    picked = pick_jobs(sheet.read("jobs"), cfg.max_jobs)
    done, failed = 0, 0
    for n, job in enumerate(picked):
        code = job.get("job_code", "?")
        try:
            # count the attempt first, so one bad job can never block the queue forever
            sheet.update("jobs", "job_key", job["job_key"], {"attempts": int(float(job.get("attempts") or 0)) + 1})
            page = html_to_text(page_fetcher(job.get("apply_url", ""))) if job.get("apply_url") else ""
            page = page[:9000]
            useful = len(page) > 600
            jd = (page if useful else (str(job.get("description_snippet", "")) + "\n" + page)).strip()[:9000]
            result = run_chain(job, jd, "job page" if useful else "email snippet only", facts, call=call)
            for chunk in format_messages(result):
                tg.send_message(chunk)
            sheet.update("jobs", "job_key", job["job_key"], {
                "status": "bullets_sent", "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "strategy_json": strategy_json(result),
                "bullets_json": json.dumps(result["state"]["s4"].get("sections") or [])})
            done += 1
        except Exception as e:
            failed += 1
            print(f"generate: job {code} failed ({type(e).__name__})")  # no details: logs are public
        if n < len(picked) - 1:
            sleep(cfg.job_gap)
    print(f"generate: {len(picked)} picked, {done} sent, {failed} failed")
    if failed:
        raise RuntimeError(f"{failed} job(s) failed during generation")
    return done
