#!/usr/bin/env python3
"""
Send the latest Job Radar run to a Google Sheet (setup: README, "Track jobs in a Google Sheet").
Needs GOOGLE_SHEET_URL and GOOGLE_SHEET_TOKEN in the environment; does nothing without them.

Run:  python3 sheets.py        after jobradar.py
"""
import csv, json, os, pathlib, re, sys, urllib.error, urllib.request

RUNS_DIR = pathlib.Path(__file__).parent / "data" / "runs"
FIELDS = {"Shortlist": "shortlist", "Risk": "risk", "Role": "title", "Company": "company",
          "Location": "location", "Salary": "salary", "Type": "type", "Matched skills": "matched",
          "Why it fits": "pros", "Watch out": "cons", "Eligibility": "eligibility", "Source": "source",
          "Posted": "posted", "url": "url"}


def sheet_row(r):
    row = {name: r[k] for name, k in FIELDS.items()}
    row["Score"] = int(r["score"])
    # same identity jobradar.py uses to drop duplicates, so a job keeps its row if its link changes
    title = re.sub(r"\W+", " ", r["title"].lower()).strip()
    row["Key"] = f'{r["company"].lower()}|{title}'
    if re.match(r"\d{4}-\d\d-\d\d", row["Posted"]):
        row["Posted"] = row["Posted"][:10]
    return row


def send(url, token, date, jobs):
    body = json.dumps({"token": token, "date": date, "jobs": jobs}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # Apps Script redirects to its result; urllib follows
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google answered {e.code}; check the web app URL and that access is 'Anyone'") from None
    try:
        res = json.loads(text)
    except ValueError:  # a Google sign-in or error page
        raise RuntimeError("Google sent a web page instead of a result; check the web app URL and that "
                           "access is 'Anyone'") from None
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "unknown error"))
    return res


def main():
    url = re.sub(r"\s+", "", os.environ.get("GOOGLE_SHEET_URL", ""))
    token = re.sub(r"\s+", "", os.environ.get("GOOGLE_SHEET_TOKEN", ""))
    if not (url and token):
        print("Google Sheet: not set up, skipped")
        return
    if not re.fullmatch(r"https://script\.google\.com/\S+/exec", url):  # never print the URL, it's a secret
        sys.exit("Google Sheet: GOOGLE_SHEET_URL should start with https://script.google.com/ and end with /exec")
    runs = sorted(p for p in RUNS_DIR.glob("*") if (p / "summary.json").exists())
    if not runs:
        sys.exit("Google Sheet: no run found; run jobradar.py first")
    run = runs[-1]
    date = json.loads((run / "summary.json").read_text(encoding="utf-8"))["started"][:10]
    with open(run / "jobs.csv", newline="", encoding="utf-8") as f:
        jobs = [sheet_row(r) for r in csv.DictReader(f)]
    try:
        res = send(url, token, date, jobs)
    except Exception as e:
        sys.exit(f"Google Sheet: failed ({e})")
    print(f"Google Sheet: {res['added']} added, {res['updated']} updated")


if __name__ == "__main__":
    main()
