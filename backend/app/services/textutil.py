"""Shared text helpers: date parsing, tokenisation, section splitting."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
               "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})

_MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_TOKEN = rf"(?:{_MONTH_RE}\.?\s*,?\s*\d{{4}}|\d{{1,2}}[/\-.]\d{{4}}|\d{{4}}[/\-.]\d{{1,2}}|\d{{4}})"
PRESENT = r"(?:present|current|now|till date|to date|ongoing)"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{DATE_TOKEN})\s*(?:-|–|—|to|until|till)\s*(?P<end>{DATE_TOKEN}|{PRESENT})", re.IGNORECASE)

STOPWORDS = set("""a an the and or of to in for on with at by from as is are be been being this that these those it its
we you your our their they he she will would can could should may might must shall do does did have has had not no
nor but if then than so such into about over under within across per via etc also any all each other more most
some very well who whom which what when where why how able including include includes using use used work working
job role team teams company candidate candidates experience years year strong good excellent ability skills skill
knowledge understanding plus preferred required requirements responsibilities qualifications new across like
make ensure help join looking based day days time part full own one two three e.g i.e
equivalent degree bachelor bachelors master masters development develop build building design designing
position hiring opportunity related relevant field computer science minimum nice have hands-on proven solid
deep great passion passionate environment fast-paced world-class""".split())


def parse_month_year(tok: str) -> tuple[int, int | None] | None:
    """Return (year, month|None). Month None means year-only (imprecise)."""
    t = tok.strip().lower().replace(",", " ").replace(".", " ")
    m = re.match(rf"({_MONTH_RE})\s*(\d{{4}})", t)
    if m:
        mon = MONTHS.get(m.group(1)[:3] if m.group(1)[:4] != "sept" else "sep")
        return int(m.group(2)), mon
    m = re.match(r"(\d{1,2})[/\-\s](\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.match(r"(\d{4})[/\-\s](\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d{4})", t)
    if m:
        return int(m.group(1)), None
    return None


def normalize_date(tok: str | None) -> str | None:
    """Normalise to 'YYYY-MM' or 'YYYY' (never invents a month)."""
    if not tok:
        return None
    if re.fullmatch(PRESENT, tok.strip(), re.IGNORECASE):
        return "present"
    p = parse_month_year(tok)
    if not p:
        return None
    y, m = p
    if y < 1950 or y > date.today().year + 6:
        return None
    return f"{y:04d}-{m:02d}" if m else f"{y:04d}"


def months_between(start: str | None, end: str | None, today: date | None = None) -> tuple[int | None, bool]:
    """Months between normalised dates. Returns (months, precise)."""
    today = today or date.today()
    if not start:
        return None, False
    precise = True

    def to_ym(s: str, is_end: bool) -> tuple[int, int] | None:
        nonlocal precise
        if s == "present":
            return today.year, today.month
        parts = s.split("-")
        y = int(parts[0])
        if len(parts) == 1:
            precise = False
            return (y, 12) if is_end else (y, 1)
        return y, int(parts[1])

    s = to_ym(start, False)
    e = to_ym(end or "present", True) if end else None
    if e is None:
        return None, False
    months = (e[0] - s[0]) * 12 + (e[1] - s[1]) + 1
    return max(0, months), precise


def merge_intervals_months(intervals: list[tuple[str, str]], today: date | None = None) -> tuple[int, bool]:
    """Total months across possibly-overlapping (start, end) intervals."""
    today = today or date.today()
    spans = []
    precise = True

    def idx(s: str, is_end: bool) -> int:
        nonlocal precise
        if s == "present":
            return today.year * 12 + today.month
        parts = s.split("-")
        if len(parts) == 1:
            precise = False
            return int(parts[0]) * 12 + (12 if is_end else 1)
        return int(parts[0]) * 12 + int(parts[1])

    for s, e in intervals:
        if not s or not e:
            continue
        a, b = idx(s, False), idx(e, True)
        if b >= a:
            spans.append((a, b))
    spans.sort()
    total = 0
    cur = None
    for a, b in spans:
        if cur is None:
            cur = [a, b]
        elif a <= cur[1] + 1:
            cur[1] = max(cur[1], b)
        else:
            total += cur[1] - cur[0] + 1
            cur = [a, b]
    if cur:
        total += cur[1] - cur[0] + 1
    return total, precise


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z][a-z0-9+#.\-]{1,}", (text or "").lower())
            if t not in STOPWORDS and not t.isdigit() and len(t) > 2]


def clean_text(text: str) -> str:
    text = (text or "").replace("\r", "\n").replace(" ", " ").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


BULLET_RE = re.compile(r"^\s*(?:[•●▪◦‣∙·\-\*–]|\d+[.)])\s+")


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line).strip()
