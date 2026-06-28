#!/usr/bin/env python3
"""
job_agent.py — a tiny automated job-search agent, FIJI-ONLY (Suva preferred).

What it does, on every run:
  1. Scrapes fresh Fiji postings from MyjobsFiji (no API key — sitemap + the
     schema.org JobPosting data embedded in each job page). Optionally also
     searches SPC/Digicel careers + a Suva tech watchlist via Google CSE.
  2. Keeps only Fiji-based roles that match your skill profile (Laravel / PHP /
     full-stack and the broader ICT terms in KEYWORDS), and lists Suva first.
  3. Removes duplicates, and (optionally) hides jobs it already emailed you before.
  4. Emails you one tidy HTML digest.

Designed to be set up once and left to run on a schedule (GitHub Actions cron or
local cron) until you don't need it anymore.

No paid services. The only credential you need is a Gmail App Password.
"""

import os
import re
import json
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# MyjobsFiji sits behind a WAF that blocks plain `requests` by TLS fingerprint
# (returns 403). curl_cffi impersonates a real browser handshake and gets through.
# If it isn't installed we fall back to requests so the rest of the agent still runs.
try:
    from curl_cffi import requests as _cffi
except Exception:  # pragma: no cover - optional dependency
    _cffi = None

# --------------------------------------------------------------------------- #
# CONFIG  — edit these, or override any of them with environment variables.
# --------------------------------------------------------------------------- #

# Words that mark a job as relevant. A job matches if ANY of these appears in
# its title or tags (case-insensitive). Tuned to the attached CV, then broadened
# to catch the way Fiji employers phrase ICT/tech roles.
KEYWORDS = [
    # core dev stack (from the CV)
    "laravel", "php", "livewire", "full stack", "full-stack", "fullstack",
    "backend", "back end", "back-end", "software engineer", "software developer",
    "web developer", "developer", "programmer", "postgres", "postgresql",
    "typescript", "javascript", "angular", "ionic", "api developer", "python",
    # broader ICT phrasing common on Fiji job boards
    "ict", "i.t", "information technology", "it officer", "it support",
    "systems", "system administrator", "sysadmin", "network", "database",
    "dba", "helpdesk", "help desk", "technical support", "it technician",
    "applications", "web", "data analyst", "business intelligence", "power bi",
    "power platform", "dynamics", "erp", "digital", "cyber", "devops",
    "qa engineer", "tester", "automation",
]

# If any of these appears in the title, the job is dropped (avoid obvious noise).
# Trimmed to seniority/management only — in Fiji's smaller market we don't want
# to exclude roles just because of the stack (.NET, C#, etc. are all worth seeing).
EXCLUDE = [
    "senior staff", "principal", "director", "head of", "chief ",
]

# Recipient + Gmail sender. Override with env vars / GitHub secrets in production.
RECIPIENT          = os.environ.get("RECIPIENT", "tuilagivousolo@gmail.com")
EMAIL_USER         = os.environ.get("EMAIL_USER", "")          # your gmail address
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")  # 16-char app password

# Only consider jobs posted within this many days (where a date is available).
# Fiji posts less frequently than global boards, so the window is a little wider;
# cross-run de-dupe means a wider window still won't re-email the same role.
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "7"))

# Hide jobs already sent in previous runs (keeps the seen-cache file below).
DEDUPE_ACROSS_RUNS = os.environ.get("DEDUPE_ACROSS_RUNS", "true").lower() == "true"
SEEN_FILE = os.environ.get("SEEN_FILE", "seen.json")

# Send an email even when there are zero new matches? Default: skip (no noise).
SEND_WHEN_EMPTY = os.environ.get("SEND_WHEN_EMPTY", "false").lower() == "true"

HEADERS = {"User-Agent": "Mozilla/5.0 (job-digest-agent; personal use)"}
TIMEOUT = 25


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get(url, params=None):
    """HTTP GET that prefers curl_cffi (browser TLS) and falls back to requests."""
    if _cffi is not None:
        return _cffi.get(url, params=params, headers=HEADERS,
                         timeout=TIMEOUT, impersonate="chrome")
    return requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)

def _now():
    return dt.datetime.now(dt.timezone.utc)


def _matches(text: str) -> bool:
    t = (text or "").lower()
    if any(bad in t for bad in EXCLUDE):
        return False
    return any(kw in t for kw in KEYWORDS)


# Fiji towns/cities we recognise. Suva (incl. its suburbs) is the priority region.
SUVA_AREA = ["suva", "nasinu", "nausori", "lami", "raiwaqa", "samabula",
             "valelevu", "central division"]
FIJI_CITIES = SUVA_AREA + ["nadi", "lautoka", "ba", "labasa", "sigatoka",
                           "rakiraki", "savusavu", "tavua", "nasinu", "navua",
                           "western division", "northern division"]


def _is_fiji(location: str, text: str = "") -> bool:
    """True only for roles based in Fiji — this digest is Fiji-only."""
    hay = f"{location} {text}".lower()
    if "fiji" in hay:
        return True
    return any(c in hay for c in FIJI_CITIES)


def _is_suva(location: str, text: str = "") -> bool:
    hay = f"{location} {text}".lower()
    return any(s in hay for s in SUVA_AREA)


def _region(location: str) -> str:
    """Suva-first label so you can scan by where the role sits inside Fiji."""
    loc = (location or "").strip()
    low = loc.lower()
    if _is_suva(low):
        return "Suva"
    for c in FIJI_CITIES:
        if c in low:
            return f"Fiji — {c.title()}"
    return loc[:40] if loc else "Fiji"


def _parse_date(value):
    """Best-effort parse of the many date formats these APIs return."""
    if not value:
        return None
    if isinstance(value, (int, float)):  # unix timestamp
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except Exception:
            return None
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None,):  # try fromisoformat first
        try:
            d = dt.datetime.fromisoformat(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d
        except Exception:
            continue
    return None


def _fresh_enough(date_obj) -> bool:
    if date_obj is None:
        return True  # keep undated jobs; cross-run dedupe handles repeats
    return (_now() - date_obj).days <= MAX_AGE_DAYS


def _norm(title, company):
    return re.sub(r"\s+", " ", f"{title} @ {company}".lower()).strip()


# --------------------------------------------------------------------------- #
# Sources — each returns a list of normalized job dicts. All wrapped so one
# failing source never kills the whole run. This digest is FIJI-ONLY, so every
# source here returns Fiji-based postings (Suva preferred).
# --------------------------------------------------------------------------- #

# How many of the newest MyjobsFiji postings to inspect per run. The site lists
# every open job in its sitemap; we fetch each candidate page to read its
# posting date and location, then the pipeline keeps only the fresh Fiji matches.
MYJOBSFIJI_MAX = int(os.environ.get("MYJOBSFIJI_MAX", "200"))

_LDJSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE)


def _job_posting_from_html(html: str):
    """Pull the schema.org JobPosting JSON-LD block out of a MyjobsFiji page."""
    for blob in _LDJSON_RE.findall(html or ""):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def from_myjobsfiji():
    """Scrape MyjobsFiji via its sitemap + per-job schema.org JSON-LD.

    MyjobsFiji aggregates most Fiji vacancies (Govt, SPC, Digicel, Carpenters,
    KPMG, banks...), so it's the backbone of the Fiji-only digest. Each job page
    embeds a clean JobPosting block with title, employer, location and dates —
    no API key needed, just a normal browser User-Agent.
    """
    out = []
    try:
        sm = _get("https://www.myjobsfiji.com/sitemap.xml")
        urls = re.findall(r"https://myjobsfiji\.com/job/[^<\s]+", sm.text)
    except Exception as e:
        print(f"[MyjobsFiji:sitemap] {e}")
        return out

    # De-dupe while preserving order, then cap the per-run crawl.
    seen, ordered = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    ordered = ordered[:MYJOBSFIJI_MAX]

    for url in ordered:
        try:
            r = _get(url)
            jp = _job_posting_from_html(r.text)
            if not jp:
                continue

            org = jp.get("hiringOrganization") or {}
            company = org.get("name", "") if isinstance(org, dict) else str(org)

            place = jp.get("jobLocation") or {}
            if isinstance(place, list):
                place = place[0] if place else {}
            addr = (place or {}).get("address", {}) if isinstance(place, dict) else {}
            locality = (addr.get("addressLocality") or "").strip()
            region = (addr.get("addressRegion") or "").strip()
            country = (addr.get("addressCountry") or "").strip()
            location = ", ".join(p for p in (locality, region, country) if p) or "Fiji"

            out.append({
                "title": (jp.get("title") or "").strip(),
                "company": company.strip(),
                "location": location,
                "url": jp.get("url") or url,
                "tags": " ".join(filter(None, [
                    jp.get("industry", ""),
                    " ".join(jp.get("occupationalCategory", []) or []),
                ])),
                "date": _parse_date(jp.get("datePosted")),
                "valid_through": _parse_date(jp.get("validThrough")),
                "source": "MyjobsFiji",
            })
        except Exception as e:
            print(f"[MyjobsFiji:{url}] {e}")
    return out


SOURCES = [from_myjobsfiji]

# Optional supplement: Google Programmable Search across SPC / Digicel careers +
# a Suva tech-company watchlist (see fiji_sources.py). Only runs if GOOGLE_API_KEY
# and GOOGLE_CX are set; otherwise it's skipped and nothing breaks.
try:
    from fiji_sources import from_fiji_local
    SOURCES.append(from_fiji_local)
except Exception as e:  # pragma: no cover - import guard
    print(f"[fiji_sources] not loaded: {e}")


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def collect():
    raw = []
    for fn in SOURCES:
        got = fn()
        print(f"  {fn.__name__}: {len(got)} pulled")
        raw.extend(got)

    seen_urls, seen_keys, kept = set(), set(), []
    for j in raw:
        haystack = f"{j['title']} {j['tags']}"
        # FIJI-ONLY: drop anything not based in Fiji, whatever else it matches.
        if not _is_fiji(j.get("location", ""), haystack):
            continue
        if not _matches(haystack):
            continue
        if not _fresh_enough(j["date"]):
            continue
        # Skip postings whose closing date has already passed.
        vt = j.get("valid_through")
        if vt is not None and vt < _now():
            continue
        key = j["url"].strip() or _norm(j["title"], j["company"])
        nk = _norm(j["title"], j["company"])
        if key in seen_urls or nk in seen_keys:
            continue
        seen_urls.add(key)
        seen_keys.add(nk)
        j["region"] = _region(j["location"])
        j["is_suva"] = _is_suva(j.get("location", ""), haystack)
        kept.append(j)
    # Suva roles first, then the rest of Fiji.
    kept.sort(key=lambda x: (0 if x.get("is_suva") else 1, x["title"].lower()))
    return kept


def filter_already_sent(jobs):
    if not DEDUPE_ACROSS_RUNS:
        return jobs, set()
    previously = set()
    if os.path.exists(SEEN_FILE):
        try:
            previously = set(json.load(open(SEEN_FILE)))
        except Exception:
            previously = set()
    new = [j for j in jobs if (j["url"] or _norm(j["title"], j["company"])) not in previously]
    updated = previously | {(j["url"] or _norm(j["title"], j["company"])) for j in jobs}
    return new, updated


def build_html(jobs):
    today = _now().strftime("%A %d %B %Y")
    by_source = {}
    for j in jobs:
        by_source.setdefault(j["source"], []).append(j)

    rows = []
    for source in sorted(by_source):
        rows.append(
            f'<tr><td colspan="3" style="background:#0f2942;color:#fff;'
            f'padding:10px 14px;font-weight:600;font-size:15px;">'
            f'{source} &nbsp;<span style="opacity:.7;font-weight:400;">'
            f'({len(by_source[source])})</span></td></tr>')
        for j in by_source[source]:
            title = (j["title"] or "Untitled").strip()
            company = (j["company"] or "").strip()
            rows.append(
                '<tr style="border-bottom:1px solid #e6e6e6;">'
                f'<td style="padding:10px 14px;"><a href="{j["url"]}" '
                f'style="color:#0a66c2;text-decoration:none;font-weight:600;">{title}</a>'
                f'<div style="color:#555;font-size:13px;">{company}</div></td>'
                f'<td style="padding:10px 14px;color:#333;font-size:13px;white-space:nowrap;">'
                f'{j["region"]}</td>'
                f'<td style="padding:10px 14px;font-size:13px;white-space:nowrap;">'
                f'<a href="{j["url"]}" style="color:#0a66c2;">open &rarr;</a></td></tr>')

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:680px;margin:auto;">
  <h2 style="color:#0f2942;margin-bottom:2px;">Your Fiji job digest &mdash; {len(jobs)} new match{'es' if len(jobs)!=1 else ''}</h2>
  <p style="color:#777;margin-top:0;font-size:14px;">{today} &middot; Fiji vacancies, Suva first</p>
  <table style="border-collapse:collapse;width:100%;border:1px solid #e6e6e6;font-size:14px;">
    <tr><th align="left" style="padding:8px 14px;color:#888;font-size:12px;">ROLE / COMPANY</th>
        <th align="left" style="padding:8px 14px;color:#888;font-size:12px;">WHERE</th>
        <th align="left" style="padding:8px 14px;color:#888;font-size:12px;">LINK</th></tr>
    {''.join(rows)}
  </table>
  <p style="color:#aaa;font-size:12px;margin-top:18px;">
    Fiji-only digest. Source: MyjobsFiji (+ SPC/Digicel & Suva tech watchlist when
    Google search keys are set). Edit KEYWORDS in job_agent.py to retune.
  </p>
</div>"""


def send_email(html, count):
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        print("!! EMAIL_USER / EMAIL_APP_PASSWORD not set — printing instead of sending.\n")
        print(html[:1500])
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Fiji jobs] {count} new match{'es' if count!=1 else ''} — {_now():%d %b}"
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(EMAIL_USER, EMAIL_APP_PASSWORD)
        s.sendmail(EMAIL_USER, [RECIPIENT], msg.as_string())
    print(f"Sent {count} jobs to {RECIPIENT}")


def main():
    print("Collecting jobs...")
    jobs = collect()
    print(f"Matched {len(jobs)} relevant jobs across all sources.")

    new, updated = filter_already_sent(jobs)
    print(f"{len(new)} are new since the last run.")

    if not new and not SEND_WHEN_EMPTY:
        print("No new jobs — skipping email.")
    else:
        send_email(build_html(new or jobs), len(new or jobs))

    if DEDUPE_ACROSS_RUNS:
        json.dump(sorted(updated), open(SEEN_FILE, "w"))
        print(f"Saved {len(updated)} keys to {SEEN_FILE}.")


if __name__ == "__main__":
    main()
