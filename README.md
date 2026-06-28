# Fiji job digest agent

A small script that scans **Fiji** job postings every day, keeps the ICT/tech
roles that fit your stack (Laravel / PHP / full-stack plus broader ICT terms),
lists **Suva** roles first, and emails you a tidy digest. No paid services.

## Where the jobs come from
- **MyjobsFiji** (the backbone) — scraped via its public `sitemap.xml`, then each
  job page's embedded schema.org `JobPosting` data (title, employer, location,
  posting date). MyjobsFiji already aggregates Government, SPC, Digicel,
  Carpenters, KPMG, banks and more, so it covers most Fiji vacancies. No API key.
- **Optional supplement** — Google Programmable Search across `careers.spc.int`,
  `careers.digicelpacific.com`, and a Suva tech-company watchlist (see
  `fiji_sources.py`). Only runs if you set `GOOGLE_API_KEY` + `GOOGLE_CX`.

Everything is filtered to **Fiji only**; anything not based in Fiji is dropped,
and Suva-area roles are sorted to the top.

> Note: MyjobsFiji sits behind a WAF that blocks plain `requests` (TLS
> fingerprinting). The agent uses `curl_cffi` to impersonate a browser, which is
> why it's in `requirements.txt`.

## What you need
- A Gmail **App Password** (not your normal password). Turn on 2-Step
  Verification, then go to Google Account → Security → App passwords → generate
  one for "Mail". You'll get a 16-character code.

## Option A — GitHub Actions (recommended: runs in the cloud, even when your laptop is off)
1. Push these files: `job_agent.py`, `fiji_sources.py`, `requirements.txt`, and
   `.github/workflows/job-digest.yml`.
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret.**
   Add three secrets:
   - `RECIPIENT` = where the digest goes (e.g. `tuilagivousolo@gmail.com`)
   - `EMAIL_USER` = the Gmail address you'll send from
   - `EMAIL_APP_PASSWORD` = the 16-character app password
   - *(optional)* `GOOGLE_API_KEY` + `GOOGLE_CX` to enable the SPC/Digicel +
     Suva-watchlist supplement.
3. Go to the **Actions** tab → "Job digest" → **Run workflow** to test it now.
   After that it runs itself every morning (8am Fiji time) until you disable it.

## Option B — Run locally on a schedule
```bash
pip install -r requirements.txt
export RECIPIENT="tuilagivousolo@gmail.com"
export EMAIL_USER="youraddress@gmail.com"
export EMAIL_APP_PASSWORD="xxxxxxxxxxxxxxxx"
python job_agent.py            # test once

# then add to crontab (runs 8am daily):
# 0 8 * * * cd /path/to/folder && /usr/bin/python3 job_agent.py >> log.txt 2>&1
```

## Tuning
Open `job_agent.py` and edit:
- `KEYWORDS` — what counts as a tech/ICT match. Empty it out (or add `"*"`-style
  broad terms) if you want *all* Fiji vacancies, not just tech.
- `EXCLUDE` — titles to drop (seniority/management noise by default).
- `MAX_AGE_DAYS` — how recent a posting must be (default 7).
- `SUVA_AREA` / `FIJI_CITIES` — which places count as Suva vs. the rest of Fiji.
- `MYJOBSFIJI_MAX` — how many of the newest postings to inspect per run.

Run with no email credentials set and it prints the digest to the terminal
instead of sending — handy for testing your keyword changes.

## Optional: SPC / Digicel + Suva company watchlist
To also pull from SPC and Digicel careers (JavaScript single-page apps that can't
be scraped directly) plus a Suva data/software company watchlist:
1. Make an engine at https://programmablesearchengine.google.com/ (set "Search the
   entire web"), copy the Search engine ID (`cx`).
2. Get an API key: https://developers.google.com/custom-search/v1/overview
3. Add two more secrets/env vars: `GOOGLE_CX` and `GOOGLE_API_KEY`.

Without the keys it skips silently, so nothing breaks. Edit the company watchlist
in `fiji_sources.py`.

**Reliable backstop:** also turn on **MyjobsFiji's own email alerts** (sign up on
the site) as a zero-maintenance safety net alongside this agent.
