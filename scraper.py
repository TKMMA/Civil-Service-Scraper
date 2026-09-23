#!/usr/bin/env python3
"""Read DLNR civil service openings from the State of Hawaii NeoGov feed.

Output shape is unchanged from the original, so the careers page widget needs
no adjustment. The one behavioural change: a failed or empty fetch now raises
instead of quietly returning an empty list, because an empty list downstream
would blank the listings on the live page.

Division: the feed often puts the department ("DLNR") in the division field.
infer_division() replaces that with the real division when the feed, the job
title, or the duties text makes it clear, and says "Division not listed" when
nothing does. Each job also gets "division_source" (feed, title, duties or
none) so any odd result can be traced back to the rule that produced it.
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

# --- DIVISIONS ---
# One label per division. The careers page filters on these exact strings,
# and it treats AQUATIC as DAR, so keep them in sync with the page.
AQUATIC = "Aquatic Resources"
FORESTRY = "Forestry and Wildlife"
PARKS = "State Parks"
BOATING = "Boating and Ocean Recreation"
CONVEYANCES = "Bureau of Conveyances"
ENFORCEMENT = "Conservation and Resources Enforcement"
LAND = "Land Division"
ENGINEERING = "Engineering"
WATER = "Water Resource Management"
HISTORIC = "Historic Preservation"
COASTAL = "Conservation and Coastal Lands"
ADMIN = "Administration"
NOT_LISTED = "Division not listed"

AND = r"(?:and|&|&amp;)"

# Feed values that just repeat the department, so they say nothing about
# the division.
DEPARTMENT_ONLY = re.compile(
    rf"^(?:dlnr|(?:department of )?land {AND} natural resources)?$", re.I)

# When the feed does name a division, map its wording to our label.
FEED_RULES = [
    (r"aquatic", AQUATIC),
    (r"forestry|wildlife", FORESTRY),
    (r"state parks|\bparks\b", PARKS),
    (r"boating|ocean recreation", BOATING),
    (r"conveyance", CONVEYANCES),
    (r"enforcement", ENFORCEMENT),
    (r"^land\b|land division|land management", LAND),
    (r"engineering", ENGINEERING),
    (r"water resource", WATER),
    (r"historic", HISTORIC),
    (r"coastal lands", COASTAL),
]

# Title words that give the job family away. First match wins, so the more
# specific families come first (an enforcement title can mention wildlife).
TITLE_RULES = [
    (r"aquatic|fisher(?:y|ies)?\b|\bdar\b", AQUATIC),
    (rf"conservation\s*{AND}\s*resources enforcement|\bdocare\b", ENFORCEMENT),
    (r"forestry|forester|wildlife|trails and access|\bhunter\b|na ala hele",
     FORESTRY),
    (r"park caretaker|state parks|park maintenance|park ranger|\bparks?\b",
     PARKS),
    (r"boating|harbor|ocean recreation", BOATING),
    (r"abstract(?:or|ing)|conveyanc|land court|registrar", CONVEYANCES),
    (r"land agent", LAND),
    (r"engineer", ENGINEERING),
    (r"hydrolog|water resource", WATER),
    (r"archaeolog|historic preservation|historian", HISTORIC),
    (r"coastal lands|\boccl\b", COASTAL),
]

# Division names written out in full in the duties text. Only full names
# count here, because duties often mention other programs in passing.
DUTIES_RULES = [
    (r"division of aquatic resources", AQUATIC),
    (rf"division of forestry {AND} wildlife", FORESTRY),
    (r"natural area reserves? (?:system|management|program)", FORESTRY),
    (r"division of state parks", PARKS),
    (rf"division of boating {AND} ocean recreation", BOATING),
    (r"bureau of conveyances", CONVEYANCES),
    (rf"division of conservation {AND} resources enforcement", ENFORCEMENT),
    (r"\bland division\b", LAND),
    (r"\bengineering division\b", ENGINEERING),
    (r"commission on water resource management", WATER),
    (r"historic preservation division", HISTORIC),
    (rf"office of conservation {AND} coastal lands", COASTAL),
    (r"personnel office|administrative services office", ADMIN),
]


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


def first_match(rules, text):
    for pattern, label in rules:
        if re.search(pattern, text or "", re.I):
            return label
    return None


def infer_division(feed_division, title, duties):
    """Return (division label, which source decided it)."""
    feed = " ".join((feed_division or "").split())
    if not DEPARTMENT_ONLY.match(feed):
        return first_match(FEED_RULES, feed) or clean_text(feed), "feed"
    label = first_match(TITLE_RULES, title)
    if label:
        return label, "title"
    label = first_match(DUTIES_RULES, duties)
    if label:
        return label, "duties"
    return NOT_LISTED, "none"


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
        title = clean_text(title_part)
        duties = clean_text(
            item.findtext("joblisting:examplesofduties", namespaces=NS)
            or "View listing for details."
        )
        division, division_source = infer_division(
            item.findtext("joblisting:division", namespaces=NS), title, duties)
        jobs.append({
            "title": title,
            "job_number": item.findtext("joblisting:jobNumberSingle", namespaces=NS) or "N/A",
            "division": division,
            "division_source": division_source,
            "location": clean_text(loc_part),
            "yearly_salary": parse_salary(item.findtext("description") or ""),
            "posted": pub[:16] if pub else "",
            "closing": item.findtext("joblisting:advertiseToDateTime", namespaces=NS) or "Continuous",
            "link": item.findtext("link"),
            "duties": duties,
        })

    print(f"Feed had {len(items)} total listings; {len(jobs)} matched "
          f"{DEPARTMENT_MATCHES}.", flush=True)
    unplaced = [j["title"] for j in jobs if j["division_source"] == "none"]
    if unplaced:
        print(f"{len(unplaced)} listing(s) with no clear division: "
              + "; ".join(unplaced), flush=True)
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
