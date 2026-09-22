#!/usr/bin/env python3
"""Read DLNR civil service openings from the State of Hawaii NeoGov feed.

Output shape is unchanged from the original, so the careers page widget needs
no adjustment. The one behavioural change: a failed or empty fetch now raises
instead of quietly returning an empty list, because an empty list downstream
would blank the listings on the live page.
"""

import json
import re
import sys
from datetime import datetime, timezone
from xml.etree import ElementTree

import requests

# --- CONFIGURATION ---
NEOGOV_FEED = "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=hawaii"
DEPARTMENT_MATCHES = ["Land & Natural Resources", "DLNR"]
NS = {"joblisting": "http://www.neogov.com/namespaces/JobListing"}
TIMEOUT = 30


def clean_text(text):
    if not text:
        return ""
    text = re.sub("<[^<]+?>", " ", text)
    text = " ".join(text.split())
    words = text.lower().split()
    caps_list = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
                 "dlnr", "dar", "scuba", "hcri", "himb"]
    return " ".join(w.upper() if w in caps_list else w.capitalize() for w in words)


def parse_salary(text):
    if not text:
        return None
    pattern = r"\$\s*([\d,]+(?:\.\d+)?).*?(month|year|hr|hour|mon)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            amount = float(match.group(1).replace(",", ""))
            unit = match.group(2).lower()
            if "mon" in unit:
                return amount * 12
            if "yr" in unit or "year" in unit:
                return amount
            if "hr" in unit or "hour" in unit:
                return amount * 2080
        except Exception:
            return None
    return None


def scrape_civil_service():
    resp = requests.get(NEOGOV_FEED, timeout=TIMEOUT,
                        headers={"User-Agent": "DAR-careers-feed/1.0"})
    resp.raise_for_status()

    root = ElementTree.fromstring(resp.content)
    items = root.findall("./channel/item")
    if not items:
        raise RuntimeError(
            "NeoGov feed parsed but contained no <item> entries. The feed "
            "format may have changed."
        )

    jobs = []
    for item in items:
        dept = item.findtext("joblisting:department", namespaces=NS) or ""
        if not any(x in dept for x in DEPARTMENT_MATCHES):
            continue

        raw_title = item.findtext("title") or ""
        if "-" in raw_title:
            title_part, loc_part = raw_title.split("-", 1)
        else:
            title_part, loc_part = raw_title, "Hawaii"

        pub = item.findtext("pubDate")
        jobs.append({
            "title": clean_text(title_part),
            "job_number": item.findtext("joblisting:jobNumberSingle", namespaces=NS) or "N/A",
            "division": clean_text(item.findtext("joblisting:division", namespaces=NS) or "DLNR"),
            "location": clean_text(loc_part),
            "yearly_salary": parse_salary(item.findtext("description") or ""),
            "posted": pub[:16] if pub else "",
            "closing": item.findtext("joblisting:advertiseToDateTime", namespaces=NS) or "Continuous",
            "link": item.findtext("link"),
            "duties": clean_text(
                item.findtext("joblisting:examplesofduties", namespaces=NS)
                or "View listing for details."
            ),
        })

    print(f"Feed had {len(items)} total listings; {len(jobs)} matched "
          f"{DEPARTMENT_MATCHES}.", flush=True)
    return jobs


def main():
    try:
        jobs = scrape_civil_service()
    except Exception as exc:
        print(f"::error::Could not read the NeoGov feed: {exc}")
        sys.exit(1)

    if not jobs:
        print("::error::Feed was readable but no DLNR listings matched. "
              "Not writing jobs.json, so the existing data stays in place.")
        sys.exit(1)

    data = {
        "civil_service": jobs,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open("jobs.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"Wrote jobs.json with {len(jobs)} openings.")


if __name__ == "__main__":
    main()
