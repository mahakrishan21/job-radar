# Job Radar

Job Radar is a daily job search for one person. It pulls remote design jobs from ten sources and keeps only the ones open to candidates in India. It scores each job against a profile, rates how likely an application is to be rejected, and writes an HTML report with a shortlist of where to apply first.

It's tuned for a senior **UI/UX designer who vibe codes and builds AI agents**. Everything it looks for lives in `profile.json`, so you can retarget it at any role.

It needs only Python 3.9 or newer: no packages, no API keys, no accounts.

## Quick start

```bash
git clone https://github.com/<you>/job-radar.git
cd job-radar
python3 jobradar.py
```

A run takes about 1.5 minutes. When it finishes, open `report.html` in the new folder under `data/runs/`.

## What a run does

```
profile.json ──► 1. FETCH ──► 2. FILTER ──► 3. SCORE ──► 4. ASSESS RISK ──► 5. REPORT
```

1. **Fetch.** It pulls jobs from all sources at the same time:
   - **Job boards:** Himalayas (searched by role and limited to jobs open to India), We Work Remotely, RemoteOK, Remotive, Jobicy, Working Nomads, Jobspresso, and Dribbble (remote roles only).
   - **Hacker News "Who is hiring?":** remote posts from the last two monthly threads.
   - **Company watchlist:** careers pages of AI and design-tool companies, read straight from Greenhouse, Ashby and Lever. Only remote roles with a target title are kept.
2. **Filter.** It keeps a job only when all of these hold:
   - The title matches `target_titles` and nothing in `title_exclude`.
   - The job is open to India. Jobs are dropped when the location, the title (e.g. "(Remote in Europe)") or the wording ("United States only", "must be authorized to work in the US", security clearance) rules India out.
   - It's full-time or contract. Freelance counts as contract; part-time and internships are dropped.
   - It isn't a duplicate of the same company and title.
3. **Score** from 0 to 100:
   - A base for the role tier: 40 for a primary title, 25 for a secondary one.
   - Points for each skill in `skill_weights` found in the listing, up to 80.
   - A "designer who builds" bonus for design roles that ask for AI coding or agent-building skills.
   - Extra points for senior or lead titles, and for confirmed India eligibility.
4. **Assess rejection risk.** Each job is rated Low, Medium or High, with the reasons:

   | Lowers risk ✓ | Raises risk ! |
   |---|---|
   | Your years of experience meet what's asked | Asks for more years, or caps at a junior range (you'd read as overqualified) |
   | Company hires in India directly | Open to dozens of countries, so a large applicant pool |
   | Values AI coding tools (Cursor, Claude, v0, Lovable…) | Requires US working hours or night shifts |
   | You apply with a test task, not a résumé screen | A degree or an industry background is required ("preferred" doesn't count) |
   | Posted within the last 7 days | Posted 30+ days ago; anything over 60 days is treated as likely closed |

5. **Report.** It picks the best role per company for three shortlist groups:
   - **Apply first:** strong fit, no red flags.
   - **Apply next:** strong fit, one thing to watch.
   - **Stretch:** high score, but several flags.

## Output

Each run gets its own folder, so no run overwrites another:

```
data/
  seen.json                  # every job already shown, so the next run flags only new ones
  runs/2026-11-03_090000/
    report.html              # shortlist and full table: sort, filter, search. Start here.
    digest.md                # top 15 new roles
    jobs.csv                 # every match with score, risk and reasons
    rejected.csv             # what was dropped, and why
    raw.json                 # everything fetched, before filtering
    summary.json             # timings, jobs per source, source errors
```

`report.html` is one self-contained file with no external libraries. It works offline and follows your system's dark mode.

## Make it yours

Edit `profile.json`. No code changes are needed.

| Key | Controls |
|---|---|
| `years_experience` | Your level, used by the rejection-risk check |
| `target_titles` | Roles to keep. `primary` scores higher than `secondary`. |
| `title_exclude` | Roles never wanted (junior, graphic, backend…) |
| `search_queries` | Keywords searched on Himalayas |
| `company_watchlist` | Company careers pages to check. Use the board name from the jobs URL: `boards.greenhouse.io/<name>`, `jobs.ashbyhq.com/<name>` or `jobs.lever.co/<name>`. |
| `employment_types` | Job types to keep: `full-time`, `contract`, and `part-time` if wanted |
| `skill_weights` | Regex → points. What pushes a job up the list. |
| `hybrid_bonus` | What counts as a "designer who builds", and the bonus points |

The India filter lives in `jobradar.py` (`LOC_INDIA`, `LOC_BLOCK`, `TEXT_BLOCK`). Change those to target another country.

## Run it every day

### On GitHub, even when your computer is off

`.github/workflows/daily.yml` runs the search on GitHub's servers every day at 09:00 IST.

- **Get a report:** open the repo's **Actions** tab, click the latest run, and download the `job-radar-…` file under **Artifacts**. Unzip it and open `report.html`. Reports are kept for 30 days.
- **Run it now:** Actions → **Daily job search** → **Run workflow**.
- **Change the time:** edit the `cron` line, which is in UTC. For example, `30 3 * * *` is 09:00 IST.
- **Tracking new jobs:** "NEW" carries over between runs via GitHub's cache.
- **Cost and limits:** scheduled runs are free on public repos and fit well inside the free minutes on private ones. GitHub pauses scheduled workflows after 60 days with no repo activity, and re-enabling them takes one click.

### Get the shortlist on Telegram

After each run, a Telegram bot can message you the shortlist, with every job as a tappable link, and attach `report.html` for the full table.

1. **Create the bot.** In Telegram, open **@BotFather**, send `/newbot`, and pick a name. Copy the **token** it replies with.
2. **Start a chat with it.** Open your new bot and send it any message, e.g. `hi`.
3. **Find your chat ID.** Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser, with your token in place of `<TOKEN>`. Copy the number after `"chat":{"id":`.
4. **Save both as repo secrets.** Go to the repo's **Settings → Secrets and variables → Actions → New repository secret** and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Or, in a terminal in this folder:
   ```bash
   gh secret set TELEGRAM_BOT_TOKEN    # paste the token when asked
   gh secret set TELEGRAM_CHAT_ID      # paste the chat ID when asked
   ```

The next run sends the messages. For local runs, set the same two environment variables before running `jobradar.py`.

### On your own machine
- **Mac/Linux:** run `crontab -e` and add:
  `0 9 * * * cd /path/to/job-radar && python3 jobradar.py`
- **Windows:** Task Scheduler → run `python jobradar.py` daily, with "Start in" set to this folder.

## Limits

- LinkedIn, Indeed, Naukri and Wellfound block automated searching, so they aren't sources. A paid aggregator like JSearch (Google for Jobs) could add them.
- Eligibility and risk come from pattern matching on the listing text. Check each listing before you apply.

## Roadmap

- **A Claude API pass:** reads unclear listings and writes a short note on why each one fits.
- **Tracking:** Google Sheet sync for applications.
- **Applications:** tailored cover notes for shortlisted roles.
