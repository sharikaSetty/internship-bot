import html as _html
import re


def esc(s):
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_A = re.compile(r"""<a\s[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)</a>""", re.I)


def html_to_text(h):
    """HTML -> readable text, keeping links as 'text [url]' so apply links survive."""
    h = str(h or "")
    h = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", h, flags=re.I)
    h = _A.sub(lambda m: " " + re.sub(r"<[^>]+>", " ", m.group(2)).strip() + " [" + m.group(1) + "] ", h)
    h = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = _html.unescape(h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def job_hash(key):
    """Same hash as the n8n version, so job codes stay identical across both."""
    h = 5381
    for ch in key:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    n = h
    while n:
        n, r = divmod(n, 36)
        s = digits[r] + s
    return (s or "0").upper().rjust(6, "0")[-6:]
