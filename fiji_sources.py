#!/usr/bin/env python3
"""
fiji_sources.py — add-on for job_agent.py that covers Fiji-LOCAL sources.

Why this exists separately:
  myjobsfiji.com blocks bots, and careers.spc.int / careers.digicelpacific.com
  are JavaScript single-page apps — none can be reliably scraped from a cron job
  with plain requests. Instead, this module asks Google to search *inside* those
  specific sites (and your company watchlist) and returns any new postings.

How it works:
  Uses Google's free Programmable Search JSON API (100 queries/day free).
  Setup once:
    1. Create an engine at https://programmablesearchengine.google.com/
       -> set it to "Search the entire web", then copy the Search engine ID (cx).
    2. Get an API key at https://developers.google.com/custom-search/v1/overview
    3. Add two secrets / env vars: GOOGLE_API_KEY and GOOGLE_CX
  If those aren't set, this module quietly returns nothing, so job_agent still runs.

Wire-up (one line in job_agent.py):
    from fiji_sources import from_fiji_local
    SOURCES.append(from_fiji_local)
"""

import os
import requests

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CX      = os.environ.get("GOOGLE_CX", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (job-digest-agent; personal use)"}
TIMEOUT = 25

# Roles you'd take. Used to narrow the site searches.
ROLE_TERMS = '("software developer" OR "full stack" OR "web developer" OR ' \
             '"application developer" OR "software engineer" OR "developer" OR ' \
             '"data" OR "BI" OR "Power Platform" OR "Dynamics" OR "PHP" OR "Laravel")'

# The three sites you asked to watch. Label = how it shows up in the email.
LOCAL_SITES = {
    "MyjobsFiji":        "myjobsfiji.com",
    "SPC Careers":       "careers.spc.int",
    "Digicel Pacific":   "careers.digicelpacific.com",
}

# Suva data / software / digital-transformation companies to keep an eye on.
# (Acton was acquired by KPMG Fiji in 2023, so it's listed once, as KPMG.)
COMPANIES = [
    "KPMG Fiji digital",          # formerly Acton — MS Dynamics 365 / Power BI / Suva
    "QIT Pacific Fiji",           # business intelligence + software dev, Suva
    "GO2 Solutions Fiji",         # software, Suva
    "Software Factory Fiji",      # software consulting, Suva
    "GC Technologies Fiji",       # software, Suva
    "Interactive Transitions Fiji",
    "DCNetworks Fiji",
    "VT Solutions Fiji",          # ICT infra / managed services / cyber, Suva
    "Datec Fiji",                 # large ICT, Suva
    "Vodafone Fiji careers software",
    "Telecom Fiji careers software",
]


def _cse(query, label, extra_site=None):
    """One Google Programmable Search call -> normalized job dicts."""
    out = []
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CX, "q": query, "num": 10}
    if extra_site:
        params["siteSearch"] = extra_site
        params["siteSearchFilter"] = "i"   # include only this site
        params["sort"] = "date"            # freshest first where Google can tell
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params=params, headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        if "error" in data:
            print(f"[CSE:{label}] {data['error'].get('message')}")
            return out
        for item in data.get("items", []):
            out.append({
                "title": item.get("title", "").strip(),
                "company": (item.get("displayLink", "") or label),
                "location": "Fiji / Suva",
                "url": item.get("link", ""),
                "tags": "fiji local",
                "date": None,                 # CSE rarely gives a clean date
                "source": label,
            })
    except Exception as e:
        print(f"[CSE:{label}] {e}")
    return out


def from_fiji_local():
    """Search the three Fiji sites + the company watchlist, fold into the digest."""
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        print("[fiji_local] GOOGLE_API_KEY / GOOGLE_CX not set — skipping local sites. "
              "Tip: also turn on MyjobsFiji's own email alerts as a reliable backstop.")
        return []

    jobs = []

    # 1) The three named sites, restricted with siteSearch.
    for label, domain in LOCAL_SITES.items():
        jobs += _cse(f"{ROLE_TERMS} jobs", label, extra_site=domain)

    # 2) Company watchlist — catches postings on their own sites or LinkedIn.
    for name in COMPANIES:
        jobs += _cse(f'{name} ("vacancy" OR "careers" OR "we are hiring" OR '
                     f'"job" OR "developer")', "Company watch")

    print(f"  from_fiji_local: {len(jobs)} pulled (pre-filter)")
    return jobs


if __name__ == "__main__":
    for j in from_fiji_local():
        print(f"- [{j['source']}] {j['title']} -> {j['url']}")
