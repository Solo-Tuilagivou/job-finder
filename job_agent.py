#!/usr/bin/env python3
"""
job_agent.py — a tiny automated job-search agent.

What it does, on every run:
  1. Pulls fresh postings from several FREE, no-API-key job sources.
  2. Keeps only the ones that match your skill profile (Laravel / PHP / full-stack...).
  3. Removes duplicates, and (optionally) hides jobs it already emailed you before.
  4. Emails you one tidy HTML digest, grouped by the SITE each job came from.

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

# --------------------------------------------------------------------------- #
# CONFIG  — edit these, or override any of them with environment variables.
# --------------------------------------------------------------------------- #

# Words that mark a job as relevant. A job matches if ANY of these appears in
# its title or tags (case-insensitive). Tuned to the attached CV.
KEYWORDS = [
    "laravel", "php", "livewire", "full stack", "full-stack", "fullstack",
    "backend", "back end", "back-end", "software engineer", "software developer",
    "web developer", "postgres", "postgresql", "typescript", "angular", "ionic",
    "api developer",
]

# If any of these appears in the title, the job is dropped (avoid wrong-stack noise).
EXCLUDE = [
    "senior staff", "principal", "director", "head of", "wordpress only",
    ".net", "c#", "ruby on rails", "salesforce", "sap ", "drupal",
]

# Recipient + Gmail sender. Override with env vars / GitHub secrets in production.
RECIPIENT          = os.environ.get("RECIPIENT", "tuilagivousolo@gmail.com")
EMAIL_USER         = os.environ.get("EMAIL_USER", "")          # your gmail address
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")  # 16-char app password

# Only consider jobs posted within this many days (where a date is available).
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "4"))

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

def _now():
    return dt.datetime.now(dt.timezone.utc)


def _matches(text: str) -> bool:
    t = (text or "").lower()
    if any(bad in t for bad in EXCLUDE):
        return False
    return any(kw in t for kw in KEYWORDS)


def _region(location: str) -> str:
    """Light label so you can scan by where the role sits."""
    loc = (location or "").lower()
    if any(w in loc for w in ["australia", "sydney", "melbourne", "brisbane",
                              "perth", "new zealand", "auckland", "nz", "aus"]):
        return "AU / NZ"
    if any(w in loc for w in ["fiji", "pacific", "vanuatu", "solomon", "samoa",
                              "tonga", "png", "papua"]):
        return "Pacific"
    if "remote" in loc or "anywhere" in loc or "worldwide" in loc or not loc.strip():
        return "Remote"
    return location.strip()[:40]


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
# failing source never kills the whole run.
# --------------------------------------------------------------------------- #

def from_remotive():
    out = []
    for term in ("laravel", "php", "full stack"):
        try:
            r = requests.get("https://remotive.com/api/remote-jobs",
                             params={"search": term, "limit": 50},
                             headers=HEADERS, timeout=TIMEOUT)
            for j in r.json().get("jobs", []):
                out.append({
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("candidate_required_location", "Remote"),
                    "url": j.get("url", ""),
                    "tags": " ".join(j.get("tags", []) or []),
                    "date": _parse_date(j.get("publication_date")),
                    "source": "Remotive",
                })
        except Exception as e:
            print(f"[Remotive:{term}] {e}")
    return out


def from_remoteok():
    out = []
    try:
        r = requests.get("https://remoteok.com/api", headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for j in data:
            if not isinstance(j, dict) or "position" not in j:
                continue  # first element is RemoteOK's legal/metadata blob
            out.append({
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": j.get("location", "Remote"),
                "url": j.get("url", ""),
                "tags": " ".join(j.get("tags", []) or []),
                "date": _parse_date(j.get("date") or j.get("epoch")),
                "source": "RemoteOK",
            })
    except Exception as e:
        print(f"[RemoteOK] {e}")
    return out


def from_arbeitnow():
    out = []
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api",
                         headers=HEADERS, timeout=TIMEOUT)
        for j in r.json().get("data", []):
            out.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", "") or ("Remote" if j.get("remote") else ""),
                "url": j.get("url", ""),
                "tags": " ".join(j.get("tags", []) or []),
                "date": _parse_date(j.get("created_at")),
                "source": "Arbeitnow",
            })
    except Exception as e:
        print(f"[Arbeitnow] {e}")
    return out


def from_jobicy():
    out = []
    for tag in ("php", "laravel"):
        try:
            r = requests.get("https://jobicy.com/api/v2/remote-jobs",
                             params={"count": 50, "tag": tag},
                             headers=HEADERS, timeout=TIMEOUT)
            for j in r.json().get("jobs", []):
                out.append({
                    "title": j.get("jobTitle", ""),
                    "company": j.get("companyName", ""),
                    "location": j.get("jobGeo", "Remote"),
                    "url": j.get("url", ""),
                    "tags": " ".join(j.get("jobIndustry", []) or []),
                    "date": _parse_date(j.get("pubDate")),
                    "source": "Jobicy",
                })
        except Exception as e:
            print(f"[Jobicy:{tag}] {e}")
    return out


def from_himalayas():
    out = []
    try:
        r = requests.get("https://himalayas.app/jobs/api",
                         params={"limit": 50}, headers=HEADERS, timeout=TIMEOUT)
        payload = r.json()
        jobs = payload.get("jobs") or payload.get("data") or []
        for j in jobs:
            locs = j.get("locationRestrictions") or j.get("countries") or []
            out.append({
                "title": j.get("title", ""),
                "company": (j.get("companyName") or j.get("company") or ""),
                "location": ", ".join(locs) if isinstance(locs, list) else str(locs) or "Remote",
                "url": j.get("applicationLink") or j.get("url", ""),
                "tags": " ".join(j.get("categories", []) or []),
                "date": _parse_date(j.get("pubDate") or j.get("publishedDate")),
                "source": "Himalayas",
            })
    except Exception as e:
        print(f"[Himalayas] {e}")
    return out


def from_hackernews():
    """'Who is hiring' comments matching your stack, via the public Algolia API."""
    out = []
    for term in ("laravel remote", "php remote"):
        try:
            r = requests.get("https://hn.algolia.com/api/v1/search_by_date",
                             params={"tags": "comment", "query": term,
                                     "hitsPerPage": 30},
                             headers=HEADERS, timeout=TIMEOUT)
            for h in r.json().get("hits", []):
                txt = (h.get("comment_text") or "").lower()
                if "remote" not in txt:
                    continue
                out.append({
                    "title": f"HN hiring post (matched '{term}')",
                    "company": h.get("author", "unknown"),
                    "location": "Remote",
                    "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    "tags": term,
                    "date": _parse_date(h.get("created_at")),
                    "source": "Hacker News (Who is hiring)",
                })
        except Exception as e:
            print(f"[HackerNews:{term}] {e}")
    return out


SOURCES = [from_remotive, from_remoteok, from_arbeitnow,
           from_jobicy, from_himalayas, from_hackernews]


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
        if not _matches(haystack):
            continue
        if not _fresh_enough(j["date"]):
            continue
        key = j["url"].strip() or _norm(j["title"], j["company"])
        nk = _norm(j["title"], j["company"])
        if key in seen_urls or nk in seen_keys:
            continue
        seen_urls.add(key)
        seen_keys.add(nk)
        j["region"] = _region(j["location"])
        kept.append(j)
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
  <h2 style="color:#0f2942;margin-bottom:2px;">Your job digest &mdash; {len(jobs)} new match{'es' if len(jobs)!=1 else ''}</h2>
  <p style="color:#777;margin-top:0;font-size:14px;">{today} &middot; grouped by source site</p>
  <table style="border-collapse:collapse;width:100%;border:1px solid #e6e6e6;font-size:14px;">
    <tr><th align="left" style="padding:8px 14px;color:#888;font-size:12px;">ROLE / COMPANY</th>
        <th align="left" style="padding:8px 14px;color:#888;font-size:12px;">WHERE</th>
        <th align="left" style="padding:8px 14px;color:#888;font-size:12px;">LINK</th></tr>
    {''.join(rows)}
  </table>
  <p style="color:#aaa;font-size:12px;margin-top:18px;">
    Sources scanned: Remotive, RemoteOK, Arbeitnow, Jobicy, Himalayas, Hacker News.
    Edit KEYWORDS in job_agent.py to retune. Automated personal digest.
  </p>
</div>"""


def send_email(html, count):
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        print("!! EMAIL_USER / EMAIL_APP_PASSWORD not set — printing instead of sending.\n")
        print(html[:1500])
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Job digest] {count} new match{'es' if count!=1 else ''} — {_now():%d %b}"
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
