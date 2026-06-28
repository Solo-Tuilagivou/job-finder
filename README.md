# Job digest agent

A small script that scans several free job sources every day, keeps only the
roles that fit your stack (Laravel / PHP / full-stack / Postgres...), and emails
you a digest grouped by the site each job came from. No paid services.

## What you need
- A Gmail **App Password** (not your normal password). Turn on 2-Step
  Verification, then go to Google Account → Security → App passwords → generate
  one for "Mail". You'll get a 16-character code.

## Option A — GitHub Actions (recommended: runs in the cloud, even when your laptop is off)
1. Create a new repository and add these files:
   `job_agent.py`, `requirements.txt`, and `.github/workflows/job-digest.yml`
   (rename `job-digest.yml` into that folder).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret.**
   Add three secrets:
   - `RECIPIENT` = `tuilagivousolo@gmail.com`
   - `EMAIL_USER` = the Gmail address you'll send from
   - `EMAIL_APP_PASSWORD` = the 16-character app password
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
- `KEYWORDS` — what counts as a match.
- `EXCLUDE` — stacks/levels to drop.
- `MAX_AGE_DAYS` — how recent a posting must be.
- `_region()` — the AU/NZ / Pacific / Remote labels.

Run with no email credentials set and it prints the digest to the terminal
instead of sending — handy for testing your keyword changes.

## Fiji-local sites (myjobsfiji.com, SPC, Digicel) + Suva company watchlist
These local portals can't be scraped reliably (MyjobsFiji blocks bots; SPC and
Digicel careers are JavaScript apps). `fiji_sources.py` instead searches *inside*
those exact sites — plus a Suva data/software company watchlist — using Google's
free Programmable Search API, and folds the results into the same digest email.

Setup (optional, ~5 min):
1. Make an engine at https://programmablesearchengine.google.com/ (set "Search the
   entire web"), copy the Search engine ID (`cx`).
2. Get an API key: https://developers.google.com/custom-search/v1/overview
3. Add two more secrets/env vars: `GOOGLE_CX` and `GOOGLE_API_KEY`.
4. Add two lines near the bottom of `job_agent.py`:
   ```python
   from fiji_sources import from_fiji_local
   SOURCES.append(from_fiji_local)
   ```
Without the keys it skips silently, so nothing breaks.

**Reliable backstop:** also turn on **MyjobsFiji's own email alerts** (sign up on the
site). MyjobsFiji already aggregates SPC, Digicel, KPMG/Acton and Carpenters Fiji
postings, so that single native alert covers most local targets with zero maintenance.

## Sources scanned (all free, no API keys)
Remotive, RemoteOK, Arbeitnow, Jobicy, Himalayas, and Hacker News "Who is hiring".
Add your own by writing another `from_*()` function and appending it to `SOURCES`.
