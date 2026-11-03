#!/usr/bin/env python3
"""
Job Radar
Fetch remote design / design-engineering jobs, keep only ones open to India-based
candidates, score them against profile.json, and write a CSV + daily digest.

No installs needed (Python 3.9+ standard library only).
Run:  python3 jobradar.py
Out:  data/runs/YYYY-MM-DD_HHMMSS/ (report.html, digest.md, jobs.csv, rejected.csv, raw.json, summary.json)
      data/seen.json (remembers what you've seen across runs)
"""
import csv, datetime, email.utils, html, json, pathlib, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).parent
PROFILE = json.loads((ROOT / "profile.json").read_text())
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
SEEN_FILE = DATA_DIR / "seen.json"
UA = {"User-Agent": "Mozilla/5.0 (personal job-search agent)"}


# ---------- helpers ----------
def get(url, as_json=True):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return json.loads(data) if as_json else data


def clean(t):
    t = html.unescape(re.sub(r"<[^>]+>", " ", str(t or "")))
    return re.sub(r"\s+", " ", t).strip()


def job_type(jtype, title):
    # one label per job across sources; a contract/part-time word in the title wins
    t, ti = clean(jtype).lower(), clean(title).lower()
    if re.search(r"\b(freelance|contract(or)?|fractional)\b", ti):
        return "Contract"
    if re.search(r"\bpart[- ]time\b", ti):
        return "Part-time"
    if re.search(r"\bintern(ship)?s?\b", t):
        return "Internship"
    if re.search(r"\bpart[- _]?time\b", t):
        return "Part-time"
    if re.search(r"\b(contract(or)?|freelance|temp(orary)?)\b", t):
        return "Contract"
    if re.search(r"\bfull[- _]?time\b", t):
        return "Full-time"
    return "Not stated"


def job(source, title, company, url, location, desc, salary="", posted="", jtype=""):
    return dict(source=source, title=clean(title), company=clean(company), url=url or "",
                location=clean(location), description=clean(desc), salary=str(salary or ""),
                posted=str(posted or ""), type=job_type(jtype, title), type_raw=clean(jtype))


# ---------- sources ----------
def remotive():
    out = []
    for cat in ("design", "software-dev"):
        d = get(f"https://remotive.com/api/remote-jobs?category={cat}")
        for j in d.get("jobs", []):
            out.append(job("Remotive", j.get("title"), j.get("company_name"), j.get("url"),
                           j.get("candidate_required_location"), j.get("description"),
                           j.get("salary"), j.get("publication_date"), j.get("job_type")))
    return out


def remoteok():
    d = get("https://remoteok.com/api")
    out = []
    for j in d[1:]:  # first item is a legal notice
        if not isinstance(j, dict):
            continue
        sal = ""
        try:
            lo, hi = int(j.get("salary_min") or 0), int(j.get("salary_max") or 0)
            if lo:
                sal = f"${lo:,}-${hi:,}" if hi > lo else f"${lo:,}+"
        except (TypeError, ValueError):
            pass
        out.append(job("RemoteOK", j.get("position"), j.get("company"), j.get("url"),
                       j.get("location"), j.get("description"), sal, j.get("date")))
    return out


def himalayas(max_pages=5):
    # search with country=IN returns only roles open to India, worldwide ones included
    out = []
    for q in PROFILE["search_queries"]:
        for p in range(1, max_pages + 1):
            try:
                d = get(f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(q)}"
                        f"&country=IN&page={p}")
            except Exception as e:
                print(f"  Himalayas '{q}' p{p}: skipped ({e})", file=sys.stderr)
                break
            jobs = d.get("jobs", [])
            for j in jobs:
                locs = [x if isinstance(x, str) else (x or {}).get("name", "")
                        for x in (j.get("locationRestrictions") or [])]
                loc = ", ".join(l for l in locs if l) or "Anywhere (no restriction listed)"
                lo, hi, cur = j.get("minSalary"), j.get("maxSalary"), j.get("currency") or "USD"
                if not (lo and hi):
                    sal = ""
                elif cur == "INR":
                    sal = f"₹{lo / 1e5:g}–{hi / 1e5:g} lakh" if hi > lo else f"₹{lo / 1e5:g} lakh"
                elif cur == "USD":
                    sal = f"${lo:,}-${hi:,}"
                else:
                    sal = f"{cur} {lo:,}-{hi:,}"
                posted = j.get("pubDate")
                if isinstance(posted, (int, float)):
                    posted = datetime.date.fromtimestamp(posted).isoformat()
                out.append(job("Himalayas", j.get("title"), j.get("companyName"),
                               j.get("applicationLink") or j.get("guid"), loc,
                               j.get("description") or j.get("excerpt"), sal,
                               posted, j.get("employmentType")))
            if len(jobs) < 20:
                break
            time.sleep(0.3)
    return out


def wwr():
    out = []
    for cat in ("remote-design-jobs", "remote-front-end-programming-jobs",
                "remote-full-stack-programming-jobs"):
        try:
            root = ET.fromstring(get(f"https://weworkremotely.com/categories/{cat}.rss", as_json=False))
        except Exception as e:
            print(f"  WWR {cat}: skipped ({e})", file=sys.stderr)
            continue
        for it in root.iter("item"):
            t = it.findtext("title", "")
            company, _, title = t.partition(":")
            if not title:
                title, company = t, ""
            # WWR tags most jobs "Anywhere in the World"; the company HQ line often says US
            desc = it.findtext("description", "")
            loc = it.findtext("region", "")
            hq = re.search(r"Headquarters:\s*(?:</strong>)?\s*([^<\n]+)", desc)
            if hq and re.search(rf"\b({US_PLACES})\b", hq.group(1), re.I):
                loc = f"{loc} (HQ: {hq.group(1).strip()})"
            out.append(job("WeWorkRemotely", title, company, it.findtext("link", ""),
                           loc, desc, "", it.findtext("pubDate", ""), it.findtext("type", "")))
    return out


def jobicy():
    out, urls = [], set()
    for tag in ("", "design", "designer", "frontend"):
        d = get("https://jobicy.com/api/v2/remote-jobs?count=100" + (f"&tag={tag}" if tag else ""))
        for j in d.get("jobs", []):
            if j.get("url") in urls:
                continue
            urls.add(j.get("url"))
            out.append(job("Jobicy", j.get("jobTitle"), j.get("companyName"), j.get("url"), j.get("jobGeo"),
                           j.get("jobDescription") or j.get("jobExcerpt"), "", j.get("pubDate"),
                           ", ".join(j.get("jobType") or [])))
    return out


def workingnomads():
    return [job("WorkingNomads", j.get("title"), j.get("company_name"), j.get("url"), j.get("location"),
                j.get("description"), "", j.get("pub_date"))
            for j in get("https://www.workingnomads.com/api/exposed_jobs/")]


def jobspresso():
    root = ET.fromstring(get("https://jobspresso.co/feed/?post_type=job_listing", as_json=False))
    out = []
    for it in root.iter("item"):
        # creator looks like "RapidAI<br>⚲&nbsp;United States"
        company, _, loc = html.unescape(it.findtext("{http://purl.org/dc/elements/1.1/}creator", "")).partition("<br>")
        out.append(job("Jobspresso", it.findtext("title", ""), company, it.findtext("link", ""),
                       loc.replace("⚲", ""), it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "")
                       or it.findtext("description", ""), "", it.findtext("pubDate", "")))
    return out


def dribbble():
    root = ET.fromstring(get("https://dribbble.com/jobs.rss", as_json=False))
    out = []
    for it in root.iter("item"):
        # "Pirxe is hiring for a position of Forward Deployed Designer in Kraków, Poland"
        company, _, title = it.findtext("title", "").partition(" is hiring for a position of ")
        if not title:
            title, company = company, ""
        title, _, loc = title.rpartition(" in ") if " in " in title else (title, "", "")
        if not re.search(r"remote|anywhere|worldwide|india", loc, re.I):  # most Dribbble jobs are on-site
            continue
        out.append(job("Dribbble", title, company, it.findtext("link", ""), loc,
                       it.findtext("description", ""), "", it.findtext("pubDate", "")))
    return out


def hn_hiring(threads=2):
    # top-level comments on the monthly "Ask HN: Who is hiring?" threads; first line is
    # "Company | Role | Location | REMOTE | ..."
    d = get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10")
    stories = [h for h in d.get("hits", []) if h["title"].lower().startswith("ask hn: who is hiring")][:threads]
    out = []
    for s in stories:
        c = get(f"https://hn.algolia.com/api/v1/search?tags=comment,story_{s['objectID']}&hitsPerPage=1000")
        for h in c.get("hits", []):
            text = h.get("comment_text") or ""
            if str(h.get("parent_id")) != s["objectID"] or not text:
                continue
            head = clean(re.split(r"<p>", text, maxsplit=1)[0])
            if not re.search(r"\bremote\b", head, re.I):
                continue
            parts = [p.strip() for p in head.split("|") if p.strip()]
            role = next((p for p in parts[1:] if role_tier(p)), parts[1] if len(parts) > 1 else head)
            rest = " | ".join(p for p in parts[1:] if p != role)
            out.append(job("HN Who's Hiring", role[:150], parts[0][:80],
                           f"https://news.ycombinator.com/item?id={h['objectID']}", rest, text,
                           "", h.get("created_at"), head))
    return out


def watchlist():
    # company career boards list every job they have, so keep only remote roles you target
    wl, out = PROFILE.get("company_watchlist", {}), []
    wanted = lambda title, loc: role_tier(title or "") and re.search(r"remote|anywhere", loc, re.I)

    for slug in wl.get("greenhouse", []):
        try:
            for j in get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").get("jobs", []):
                loc = (j.get("location") or {}).get("name", "")
                if not wanted(j.get("title"), loc):
                    continue
                full = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}")
                out.append(job("Greenhouse", j.get("title"), full.get("company_name") or slug,
                               j.get("absolute_url"), loc, html.unescape(full.get("content") or ""),
                               "", j.get("first_published") or j.get("updated_at")))
        except Exception as e:
            print(f"  Greenhouse {slug}: skipped ({e})", file=sys.stderr)

    for slug in wl.get("ashby", []):
        try:
            d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
            for j in d.get("jobs", []):
                locs = [j.get("location") or ""] + [x.get("location", "") if isinstance(x, dict) else str(x)
                                                    for x in j.get("secondaryLocations") or []]
                loc = ", ".join(l for l in locs if l)
                if j.get("workplaceType") == "Remote" and "remote" not in loc.lower():
                    loc = f"Remote: {loc}"
                if not wanted(j.get("title"), loc):
                    continue
                out.append(job("Ashby", j.get("title"), slug.capitalize(), j.get("jobUrl"), loc,
                               j.get("descriptionPlain") or j.get("descriptionHtml"),
                               (j.get("compensation") or {}).get("compensationTierSummary") or "",
                               j.get("publishedAt"), j.get("employmentType")))
        except Exception as e:
            print(f"  Ashby {slug}: skipped ({e})", file=sys.stderr)

    for slug in wl.get("lever", []):
        try:
            for j in get(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
                cat = j.get("categories") or {}
                loc = ", ".join(cat.get("allLocations") or [cat.get("location") or ""])
                if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
                    loc = f"Remote: {loc}"
                if not wanted(j.get("text"), loc):
                    continue
                posted = j.get("createdAt")
                if isinstance(posted, (int, float)):
                    posted = datetime.date.fromtimestamp(posted / 1000).isoformat()
                out.append(job("Lever", j.get("text"), slug.capitalize(), j.get("hostedUrl"), loc,
                               j.get("descriptionPlain") or j.get("description"), "", posted,
                               cat.get("commitment")))
        except Exception as e:
            print(f"  Lever {slug}: skipped ({e})", file=sys.stderr)
    return out


SOURCES = [("Remotive", remotive), ("RemoteOK", remoteok), ("Himalayas", himalayas),
           ("WeWorkRemotely", wwr), ("Jobicy", jobicy), ("WorkingNomads", workingnomads),
           ("Jobspresso", jobspresso), ("Dribbble", dribbble), ("HN Who's Hiring", hn_hiring),
           ("Company watchlist", watchlist)]


# ---------- matching ----------
def role_tier(title):
    t = title.lower()
    if any(re.search(rf"\b{re.escape(x)}\b", t) for x in PROFILE["title_exclude"]):
        return None
    for tier in ("primary", "secondary"):
        if any(re.search(rf"\b{re.escape(x)}\b", t) for x in PROFILE["target_titles"][tier]):
            return tier
    return None


LOC_INDIA = re.compile(r"\b(india|apac|asia|ist|bengaluru|bangalore|mumbai|delhi|new delhi|hyderabad|pune|"
                       r"chennai|ahmedabad|gurgaon|gurugram|noida|kolkata)\b", re.I)
LOC_ANY = re.compile(r"\b(worldwide|anywhere|global|no restriction)\b", re.I)
US_PLACES = (r"usa?|u\.s\.a?\.?|united states|north america|americas|canada|california|new york|nyc|"
             r"san francisco|sf|bay area|seattle|austin|boston|chicago|denver|los angeles|washington|texas|"
             r"florida|miami|atlanta|toronto|vancouver")
LOC_BLOCK = re.compile(rf"\b({US_PLACES}|uk|united kingdom|london|europe|emea|eu|latam|latin america|"
                       r"germany|berlin|france|paris|spain|madrid|barcelona|portugal|lisbon|netherlands|"
                       r"amsterdam|ireland|dublin|sweden|stockholm|norway|denmark|finland|poland|warsaw|"
                       r"krak[oó]w|switzerland|zurich|italy|austria|belgium|czech|romania|greece|israel|"
                       r"tel aviv|brazil|mexico|argentina|colombia|chile|australia|sydney|melbourne|"
                       r"new zealand|japan|tokyo|singapore|south africa)\b", re.I)
US = r"(us|u\.s\.?|usa|united states)(?!\w)"
TEXT_BLOCK = [
    rf"\b{US}([- ](residents?|citizens?|candidates|applicants|based))?[- ]only\b",
    rf"\bonly (considering|accepting|hiring|open to)[^.]{{0,60}}\b(in|from|of) the {US}",
    rf"\b(open|available|limited|restricted) (only )?to (candidates |applicants |residents |people )?"
    rf"(based |located |living )?(in |of |from )?(the )?{US}(?!\s*(and|or|,|/|time|hours|business|-based hours))",
    r"(authori[sz]ed|eligible|legally able) to work in the (us|u\.s\.|united states)",
    r"must (be|reside|live)( located| based)? in the (us|u\.s\.|united states)",
    r"\bus citizen(s|ship)?\b",
    r"\bgreen card\b",
    r"\bsecurity clearance\b",
    r"\bw-?2 (only|employees?)\b",
    r"\bno (visa )?sponsorship[^.]{0,80}\b(us|united states) residents\b",
]
TEXT_OK = re.compile(r"\b(deel|remote\.com|oyster|employer of record|eor|independent contractor|"
                     r"contractors? welcome|any time ?zone|fully async|india|apac)\b", re.I)


def eligibility(j):
    for p in TEXT_BLOCK:
        m = re.search(p, j["description"], re.I)
        if m:
            return "No", f'says "{m.group(0)}"'
    for paren in re.findall(r"\(([^)]*)\)", j.get("title", "")):  # e.g. "(Remote in Europe)"
        if LOC_BLOCK.search(paren) and not LOC_INDIA.search(paren) and not LOC_ANY.search(paren):
            return "No", f'title says "({paren})"'
    loc = j["location"]
    if LOC_INDIA.search(loc):
        return "Eligible", f"location: {loc}"
    if LOC_ANY.search(loc):
        if LOC_BLOCK.search(loc):  # e.g. "Global (US time zones)"
            return "Check", f"location: {loc} (worldwide, but names a region)"
        return "Eligible", f"location: {loc}"
    if loc and LOC_BLOCK.search(loc):
        return "No", f"location: {loc}"
    m = TEXT_OK.search(j["description"])
    if m:
        return "Likely", f'mentions "{m.group(0)}"'
    return "Check", f"location unclear ({loc or 'not given'})"


def score(j, tier, elig):
    text = f'{j["title"]} {j["description"]}'.lower()
    s = 40 if tier == "primary" else 25
    hits = []
    for pat, w in PROFILE["skill_weights"].items():
        m = re.search(rf"\b(?:{pat})\b", text)
        if m:
            hits.append(m.group(0))
            s += w
    s = min(s, 80)
    hb = PROFILE["hybrid_bonus"]  # a design role that also asks you to code with AI / build agents
    if re.search(hb["title"], j["title"], re.I) and re.search(rf"\b(?:{hb['skills']})\b", text):
        hits.insert(0, "designer who builds")
        s += hb["points"]
    if re.search(r"\b(senior|sr|lead|staff|principal|head|founding)\b", j["title"], re.I):
        s += 10
    s += {"Eligible": 10, "Likely": 5}.get(elig, 0)
    return min(s, 100), hits


# ---------- fit ----------
YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?years?(?=[^.]{0,50}"
                   r"(experience|designing|working|of (ux|ui|product|design|relevant|professional)|in (ui|ux|product|design)))", re.I)
LATE_HOURS = re.compile(r"night[- ]shift|us[- ]based hours|\b(est|pst|et|pt) hours|core us hours|east coast workday|"
                        r"overlap\w*\s+(with\s+)?(the\s+)?(us|u\.s\.|east coast|pacific)|attendance in that window is required", re.I)
DEGREE = re.compile(r"(bachelor|master)'?s?( degree)? (in|required)|\bdegree in\b|\b(msc|btech|b\.tech)\b", re.I)
DOMAIN = re.compile(r"(experience|familiarity|background)[^.]{0,40}\b(fintech|crypto|blockchain|web3|defi|healthcare|"
                    r"telehealth|ehr|edtech|investing|insurance|real estate|cybersecurity)\b", re.I)
AI_BUILD = re.compile(r"vibe[- ]cod\w*|cursor|lovable|\bv0\b|claude|replit|copilot|bolt\.new|prototyp\w* in code", re.I)
TEST_TASK = re.compile(r"test task|short task|take[- ]home|trial task", re.I)
SOFT = re.compile(r"prefer|nice to have|bonus|a plus|ideally|is valuable|highly valuable", re.I)


def required(pattern, text):
    # first match whose surrounding sentence doesn't soften it ("preferred", "a plus", ...)
    for m in pattern.finditer(text):
        start = max(text.rfind(".", 0, m.start()), m.start() - 120)
        if not SOFT.search(text[start:m.end() + 60]):
            return m
    return None


def posted_date(s):
    s = str(s or "")
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except (TypeError, ValueError, IndexError):
        return None


def assess(j, today):
    """Rejection risk for you specifically: level, hours, degree, domain, freshness, applicant pool."""
    d, pros, cons = j["description"], [], []
    mine = PROFILE["years_experience"]
    asks = [(int(m[0]), int(m[1] or m[0]), bool(m[1])) for m in YEARS.findall(d)]
    asks = [a for a in asks if a[1] <= 20]
    if asks:
        need = max(lo for lo, _, _ in asks)
        if need > mine:
            cons.append(f"asks {need}+ years")
        elif any(ranged and hi <= mine - 4 for _, hi, ranged in asks):
            cons.append(f"pitched at {max(hi for _, hi, _ in asks)} years or less, may read as overqualified")
        else:
            pros.append(f"asks {need}+ years, you have {mine}")
    test_task = TEST_TASK.search(d)
    if test_task:
        pros.append("apply with a test task, not a résumé screen")
    many_countries = j["location"].count(",") > 8
    if LOC_INDIA.search(j["location"]) and not many_countries:
        pros.append("hires in India")
    elif (many_countries or LOC_ANY.search(j["location"])) and not test_task:
        cons.append("open to many countries, so more competition")
    if j["eligibility"] == "Check":
        cons.append("location unclear")
    if AI_BUILD.search(d):
        pros.append("values AI coding tools")
    m = LATE_HOURS.search(d)
    if m:
        cons.append(f'needs US hours ("{m.group(0)}")')
    if required(DEGREE, d):
        cons.append("asks for a degree")
    m = required(DOMAIN, d)
    if m:
        cons.append(f"wants {m.group(2).lower()} background")
    stale = False
    pd = posted_date(j["posted"])
    if pd:
        age = (today - pd).days
        if age <= 7:
            pros.append(f"posted {age} days ago")
        elif age > 60:
            cons.append(f"posted {age} days ago, likely closed")
            stale = True
        elif age > 30:
            cons.append(f"posted {age} days ago, may be filling")
    risk = "High" if stale or len(cons) > 1 else "Medium" if cons else "Low"
    return risk, pros, cons


def shortlist(results):
    # best role per company, so you never send several applications to one company
    used = set()
    for tier, ok, cap in (("Apply first", lambda r: r["score"] >= 85 and r["risk"] == "Low", 8),
                          ("Apply next", lambda r: r["score"] >= 70 and r["risk"] != "High", 12),
                          ("Stretch", lambda r: r["score"] >= 80, 6)):
        n = 0
        for r in results:
            if (n < cap and not r["shortlist"] and r["company"].lower() not in used and ok(r)
                    and "likely closed" not in r["cons"]):
                r["shortlist"] = tier
                used.add(r["company"].lower())
                n += 1


# ---------- report ----------
REPORT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Report __DATE__</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--text:#1d1d1b;--muted:#6d6d67;--line:#e3e3dd;--hover:#fafaf7;--accent:#2f5bea;
--g-bg:#e5f3e9;--g-t:#1c7239;--a-bg:#fdf1d3;--a-t:#855600;--n-bg:#efefeb;--n-t:#62625c}
@media (prefers-color-scheme:dark){:root{--bg:#131312;--panel:#1c1c1a;--text:#ebebe8;--muted:#9a9a93;--line:#2e2e2b;
--hover:#222220;--accent:#88a5ff;--g-bg:#163a22;--g-t:#8ddca5;--a-bg:#3a2e0f;--a-t:#f0c768;--n-bg:#282825;--n-t:#a3a39c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1440px;margin:0 auto;padding:28px 16px 56px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;margin:0}
.sub{color:var(--muted);margin:0 0 20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:20px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin-bottom:12px}
input,select{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
#q{flex:1 1 260px}
#min{width:72px}
label{display:inline-flex;gap:6px;align-items:center;color:var(--muted)}
.count{color:var(--muted);margin-left:auto}
.wrap{overflow:auto;max-height:78vh;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:1100px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;z-index:1;background:var(--panel);font-size:11px;font-weight:600;text-transform:uppercase;
letter-spacing:.05em;color:var(--muted);white-space:nowrap;cursor:pointer;user-select:none}
th[data-dir=asc]::after{content:" \\25B2"}
th[data-dir=desc]::after{content:" \\25BC"}
tbody tr:hover{background:var(--hover)}
tbody tr:last-child td{border-bottom:0}
a{color:var(--accent);text-decoration:none;font-weight:500}
a:hover{text-decoration:underline}
.score{display:inline-block;min-width:38px;text-align:center;border-radius:6px;padding:2px 6px;font-weight:600;font-variant-numeric:tabular-nums}
.pill{display:inline-block;font-size:12px;border-radius:999px;padding:1px 9px;white-space:nowrap}
.g{background:var(--g-bg);color:var(--g-t)}.a{background:var(--a-bg);color:var(--a-t)}.n{background:var(--n-bg);color:var(--n-t)}
.new{font-size:11px;font-weight:700;letter-spacing:.04em;color:var(--accent)}
.muted{color:var(--muted)}
.small{font-size:12px}
.loc{max-width:220px}
.skills{max-width:280px}
.nowrap{white-space:nowrap}
#short h2{margin:22px 0 10px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card-top{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.card h3{font-size:15px;margin:0 0 2px}
.card p{margin:0 0 8px}
.why{list-style:none;margin:0;padding:0;font-size:13px}
.why li{padding-left:18px;position:relative;margin:2px 0}
.why li::before{position:absolute;left:0;font-weight:700}
.why .pro::before{content:"\\2713";color:var(--g-t)}
.why .con::before{content:"!";color:var(--a-t);left:4px}
.r-High{background:#fde4e1;color:#9b2c1f}
@media (prefers-color-scheme:dark){.r-High{background:#3d1a16;color:#f5a99d}}
.all{margin-top:28px}
details{margin-top:28px}
summary{cursor:pointer;margin-bottom:10px}
summary h2{display:inline}
</style></head><body><main>
<h1>Job report</h1>
<p class="sub" id="sub"></p>
<div class="stats" id="stats"></div>
<section id="short"></section>
<h2 class="all">All matches</h2>
<div class="controls">
<input type="search" id="q" placeholder="Search role, company, location or skill">
<select id="elig"><option value="">All eligibility</option><option>Eligible</option><option>Likely</option><option>Check</option></select>
<select id="jtype"><option value="">Any job type</option><option>Full-time</option><option>Contract</option><option>Not stated</option></select>
<select id="src"><option value="">All sources</option></select>
<select id="tier"><option value="">All roles</option><option value="primary">Primary roles</option><option value="secondary">Secondary roles</option></select>
<label>Min score <input type="number" id="min" value="0" min="0" max="100" step="5"></label>
<label><input type="checkbox" id="onlyNew"> New only</label>
<span class="count" id="count"></span>
</div>
<div class="wrap"><table id="jobs"><thead></thead><tbody></tbody></table></div>
<details><summary><h2>Dropped: region-only, part-time or internship (<span id="rejn"></span>)</h2></summary>
<div class="wrap"><table id="rej"><thead><tr><th>Reason</th><th>Role</th><th>Company</th><th>Location</th><th>Type</th><th>Source</th></tr></thead><tbody></tbody></table></div>
</details>
</main>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const link = r => /^https?:/i.test(r.url || "") ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title);
const shortLoc = l => { const p = String(l || "").split(", "); return p.length > 4 ? `${esc(p.slice(0, 3).join(", "))} <span class="muted">+${p.length - 3} more</span>` : esc(l); };
const day = s => { const d = new Date(s); return isNaN(d) ? 0 : d.getTime(); };
const fmtDay = s => day(s) ? new Date(s).toISOString().slice(0, 10) : esc(s);
const pay = s => +((String(s || "").replace(/,/g, "").match(/\\d+/) || [0])[0]);
const ELIG = {Eligible: "g", Likely: "a", Check: "n"};
const RISK = {Low: "g", Medium: "a", High: "r-High"}, RISK_ORDER = {Low: 0, Medium: 1, High: 2};
const items = s => String(s || "").split("; ").filter(Boolean);
D.jobs.forEach(r => r.score = +r.score);

const card = r => `<article class="card"><div class="card-top">
  <span class="score ${r.score >= 80 ? "g" : r.score >= 60 ? "a" : "n"}">${r.score}</span>
  <span class="pill ${RISK[r.risk] || "n"}">${esc(r.risk)} risk</span>${r.new ? '<span class="new">NEW</span>' : ""}</div>
  <h3>${link(r)}</h3>
  <p class="muted small">${[esc(r.company), shortLoc(r.location), esc(r.type), esc(r.salary)].filter(Boolean).join(" · ")}</p>
  <ul class="why">${items(r.pros).map(p => `<li class="pro">${esc(p)}</li>`).join("")}${items(r.cons).map(c => `<li class="con">${esc(c)}</li>`).join("")}</ul>
</article>`;
const TIERS = {"Apply first": "best fit, lowest rejection risk", "Apply next": "strong fit, one thing to watch", "Stretch": "high score, but check the flags"};
$("#short").innerHTML = Object.entries(TIERS).map(([t, note]) => {
  const rs = D.jobs.filter(r => r.shortlist === t);
  return rs.length ? `<h2>${t} <span class="muted small">· ${note}</span></h2><div class="cards">${rs.map(card).join("")}</div>` : "";
}).join("");

const cols = [
  {k: "score", label: "Score", num: true, html: r => `<span class="score ${r.score >= 80 ? "g" : r.score >= 60 ? "a" : "n"}">${r.score}</span>`},
  {k: "new", label: "New", html: r => r.new ? '<span class="new">NEW</span>' : ""},
  {k: "title", label: "Role", html: r => link(r) + (r.type ? `<div class="muted small">${esc(r.type)}</div>` : "")},
  {k: "company", label: "Company", html: r => esc(r.company)},
  {k: "eligibility", label: "Eligibility", html: r => `<span class="pill ${ELIG[r.eligibility] || "n"}" title="${esc(r.why)}">${esc(r.eligibility)}</span>`},
  {k: "risk", label: "Rejection risk", key: r => RISK_ORDER[r.risk] ?? 3,
   html: r => `<span class="pill ${RISK[r.risk] || "n"}" title="${esc(r.cons || "no red flags found")}">${esc(r.risk)}</span>`},
  {k: "location", label: "Location", cls: "loc", html: r => `<span title="${esc(r.location)}">${shortLoc(r.location)}</span>`},
  {k: "salary", label: "Salary", cls: "nowrap", num: true, key: r => pay(r.salary), html: r => r.salary ? esc(r.salary) : '<span class="muted">—</span>'},
  {k: "matched", label: "Matched skills", cls: "skills small", html: r => esc(r.matched) || '<span class="muted">—</span>'},
  {k: "source", label: "Source", html: r => esc(r.source)},
  {k: "posted", label: "Posted", cls: "nowrap", num: true, key: r => day(r.posted), html: r => fmtDay(r.posted)},
];
const state = {k: "score", dir: -1};
$("#jobs thead").innerHTML = "<tr>" + cols.map(c => `<th data-k="${c.k}">${c.label}</th>`).join("") + "</tr>";
document.querySelectorAll("#jobs th").forEach(th => th.onclick = () => {
  const c = cols.find(c => c.k === th.dataset.k);
  state.dir = state.k === c.k ? -state.dir : (c.num ? -1 : 1);
  state.k = c.k;
  render();
});
const bySrc = {};
D.jobs.forEach(r => bySrc[r.source] = (bySrc[r.source] || 0) + 1);
$("#src").innerHTML += Object.entries(bySrc).sort((a, b) => b[1] - a[1])
  .map(([s, n]) => `<option value="${esc(s)}">${esc(s)} (${n})</option>`).join("");
["#q", "#elig", "#jtype", "#src", "#tier", "#min", "#onlyNew"].forEach(s => $(s).addEventListener("input", render));

function render() {
  const q = $("#q").value.trim().toLowerCase(), e = $("#elig").value, t = $("#tier").value, jt = $("#jtype").value;
  const src = $("#src").value, min = +$("#min").value || 0, onlyNew = $("#onlyNew").checked;
  const rows = D.jobs.filter(r => (!e || r.eligibility === e) && (!t || r.tier === t) && (!jt || r.type === jt) &&
    (!src || r.source === src) &&
    r.score >= min && (!onlyNew || r.new) &&
    (!q || [r.title, r.company, r.location, r.matched].join(" ").toLowerCase().includes(q)));
  const c = cols.find(c => c.k === state.k);
  const key = c.key || (r => c.num ? +r[c.k] : String(r[c.k] || "").toLowerCase());
  rows.sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : 0) * state.dir; });
  $("#jobs tbody").innerHTML = rows.map(r => "<tr>" + cols.map(c => `<td class="${c.cls || ""}">${c.html(r)}</td>`).join("") + "</tr>").join("")
    || `<tr><td colspan="${cols.length}" class="muted">No roles match these filters.</td></tr>`;
  document.querySelectorAll("#jobs th").forEach(th => th.dataset.dir = th.dataset.k === state.k ? (state.dir > 0 ? "asc" : "desc") : "");
  $("#count").textContent = `${rows.length} of ${D.jobs.length} roles`;
}

const S = D.summary;
const stat = (n, l) => `<div class="stat"><b>${n}</b><span>${l}</span></div>`;
$("#sub").textContent = `Run ${String(S.started).replace("T", " ")} · ` +
  Object.entries(S.fetched).map(([k, v]) => `${k}: ${v}`).join(" · ");
$("#stats").innerHTML = stat(S.total_fetched, "jobs fetched") + stat(S.matches, "matches open to India") +
  stat(S.new, "new since last run") + stat(D.jobs.filter(r => r.risk === "Low").length, "low rejection risk") +
  stat(D.jobs.filter(r => r.score >= 80).length, "score 80+") +
  stat(D.jobs.filter(r => r.matched.includes("designer who builds")).length, "designer who builds") +
  stat(S.rejected, "dropped (region or job type)");
$("#rejn").textContent = D.rejected.length;
$("#rej tbody").innerHTML = D.rejected.map(r => `<tr><td>${esc(r.why)}</td><td>${link(r)}</td><td>${esc(r.company)}</td>` +
  `<td class="loc">${shortLoc(r.location)}</td><td>${esc(r.type)}</td><td>${esc(r.source)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">None</td></tr>';
render();
</script></body></html>
"""


def write_html(path, results, dropped, summary):
    slim = lambda rows: [{k: v for k, v in r.items() if k != "description"} for r in rows]
    data = json.dumps(dict(jobs=slim(results), rejected=slim(dropped), summary=summary), ensure_ascii=False)
    page = REPORT_HTML.replace("__DATE__", str(summary["started"])[:10]).replace("__DATA__", data.replace("</", "<\\/"))
    path.write_text(page, encoding="utf-8")


# ---------- run ----------
def write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    started = datetime.datetime.now()
    today = started.date().isoformat()
    run_dir = RUNS_DIR / started.strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

    raw, fetched = [], {}
    with ThreadPoolExecutor(len(SOURCES)) as pool:  # sources are independent sites, fetch together
        futures = [(name, pool.submit(fn)) for name, fn in SOURCES]
        for name, fut in futures:
            try:
                got = fut.result()
                raw += got
                fetched[name] = len(got)
                print(f"{name}: {len(got)} jobs fetched")
            except Exception as e:
                fetched[name] = f"failed: {e}"
                print(f"{name}: failed ({e})", file=sys.stderr)
    (run_dir / "raw.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")

    allowed_types = {t.capitalize() for t in PROFILE["employment_types"]} | {"Not stated"}
    results, dropped, keys = [], [], set()
    for j in raw:
        tier = role_tier(j["title"])
        if not tier:
            continue
        key = (j["company"].lower(), re.sub(r"\W+", " ", j["title"].lower()).strip())
        if key in keys:
            continue
        keys.add(key)
        elig, why = eligibility(j)
        if elig != "No" and j["type"] not in allowed_types:
            elig, why = "No", f"type: {j['type']}"
        if elig == "No":
            dropped.append(dict(j, eligibility=elig, why=why, tier=tier))
            continue
        sc, hits = score(j, tier, elig)
        j.update(score=sc, eligibility=elig, why=why, tier=tier,
                 matched=", ".join(hits), new="yes" if j["url"] not in seen else "")
        risk, pros, cons = assess(j, started.date())
        j.update(risk=risk, pros="; ".join(pros), cons="; ".join(cons), shortlist="")
        results.append(j)
    rejected = len(dropped)

    # ties: designer-who-builds roles first, then primary titles
    results.sort(key=lambda x: (-x["score"], "designer who builds" not in x["matched"], x["tier"] != "primary"))
    shortlist(results)

    cols = ["score", "shortlist", "risk", "new", "eligibility", "why", "title", "company", "location",
            "salary", "type", "tier", "matched", "pros", "cons", "source", "posted", "url"]
    write_csv(run_dir / "jobs.csv", results, cols)
    write_csv(run_dir / "rejected.csv", dropped,
              ["why", "title", "company", "location", "salary", "type", "tier", "source", "posted", "url"])

    all_new = [r for r in results if r["new"]]
    new = all_new[:15]
    lines = [f"# Job digest — {today}", "",
             f"{len(results)} matching roles open to India · {len(all_new)} new · "
             f"{rejected} relevant roles dropped (US/region-only, part-time or internship)", ""]
    for r in new:
        lines += [f"### {r['score']} · {r['title']} — {r['company']}",
                  f"- {r['eligibility']}: {r['why']}",
                  f"- {r['risk']} rejection risk" + (f": {r['cons']}" if r['cons'] else ""),
                  f"- {r['salary'] or 'Salary not listed'} · {r['type'] or 'type n/a'} · via {r['source']}",
                  f"- Matched: {r['matched'] or '—'}",
                  f"- {r['url']}", ""]
    (run_dir / "digest.md").write_text("\n".join(lines), encoding="utf-8")

    summary = dict(started=started.isoformat(timespec="seconds"),
                   finished=datetime.datetime.now().isoformat(timespec="seconds"),
                   fetched=fetched, total_fetched=len(raw), matches=len(results),
                   new=len(all_new), rejected=rejected)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_html(run_dir / "report.html", results, dropped, summary)

    SEEN_FILE.write_text(json.dumps(sorted(seen | {r["url"] for r in results})))
    print(f"\n{len(results)} matches ({len(all_new)} new), {rejected} dropped (region-only or wrong job type).")
    print(f"Saved to {run_dir.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
