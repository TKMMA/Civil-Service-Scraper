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

import html
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


# --- TEXT CLEANUP ---
# The feed sends titles and locations in ALL CAPS, and duties as ordinary
# sentences. Duties are left as written. Titles and locations are recased
# with title_case(), which keeps acronyms and Roman numerals in capitals,
# lowercases short joining words, and capitalizes after "(", "/" and "-".
ACRONYMS = {"dlnr", "dar", "docare", "dobor", "dofaw", "ohhi", "scuba",
            "hcri", "himb", "noaa", "rcuh", "uh", "id", "it", "pma", "cls",
            "ucc", "orma", "hrs", "har"}
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on",
               "or", "the", "to", "with"}
ROMAN = re.compile(r"^(?:x{0,2})(?:ix|iv|v?i{0,3})$")
WORD = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")


def clean_text(text):
    """Strip HTML, decode entities, and collapse whitespace. Casing is kept."""
    if not text:
        return ""
    text = re.sub(r"<[^<]+?>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def title_case(text):
    """Recase an ALL CAPS title or location. Mixed case text is left alone."""
    text = clean_text(text)
    upper = sum(c.isupper() for c in text)
    lower = sum(c.islower() for c in text)
    if lower > upper:
        return text

    def fix(match):
        word = match.group(0)
        low = word.lower()
        before = text[:match.start()].rstrip()
        starts_phrase = not before or before[-1] in "(/-,:"
        if low in ACRONYMS or ROMAN.match(low):
            return word.upper()
        if low in SMALL_WORDS and not starts_phrase:
            return low
        # An apostrophe before a vowel in a place name is an okina
        # (MOLOKA'I, HONOKA'A); keep apostrophes like STATE'S as they are.
        low = re.sub(r"['\u2019](?=[aeiou])", "\u02bb", low)
        return low[0].upper() + low[1:]

    return WORD.sub(fix, text)


def split_title(raw_title):
    """Split "TITLE - LOCATION". A spaced dash wins, so PART-TIME or
    PARA-MEDICAL in a title is not mistaken for the separator."""
    parts = re.split(r"\s+-\s*|\s*-\s+", raw_title, maxsplit=1)
    if len(parts) == 1 and "-" in raw_title:
        parts = raw_title.split("-", 1)
    if len(parts) == 1:
        return raw_title, "Hawaii"
    # Any further " - " in the location reads better as a comma
    # (KAMUELA/KOHALA/WAIKOLOA - HAWAII ISLAND).
    return parts[0], re.sub(r"\s+-\s+", ", ", parts[1].strip())


# --- SALARY ---
# The feed has salary in two places. Some listings fill minimumSalary and
# maximumSalary with numbers; most say "See Position Description" there and
# put the pay in the description text instead, e.g.
# "$5,107 to $6,221 per month (SR-20, Step D to I)". salaryInterval says
# "Month" even when the text says "per hour", so the text's own unit wins.
PER_YEAR = {"month": 12, "hour": 2080, "year": 1}
# Sane yearly bounds, so a stray dollar figure (a fee, a bonus) is ignored.
YEARLY_MIN, YEARLY_MAX = 15000, 500000
MONEY = r"\$\s*([\d,]+(?:\.\d+)?)"
SALARY_TEXT = re.compile(
    MONEY + r"(?:\s*(?:to|-|\u2013)\s*" + MONEY + r")?"
    r"\s*(?:/|p\s?er|a)?\s*(month|hour|hr|year|annual)", re.I)
PAY_GRADE = re.compile(r"\b(SR|EM|BC|WS|WB)-?(\d{1,2})\b")


def to_number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def yearly(amount, unit):
    unit = unit.lower()
    if unit.startswith(("hour", "hr")):
        factor = PER_YEAR["hour"]
    elif unit.startswith(("year", "annual")):
        factor = PER_YEAR["year"]
    else:
        factor = PER_YEAR["month"]
    value = round(amount * factor)
    return value if YEARLY_MIN <= value <= YEARLY_MAX else None


def parse_salary(item_min, item_max, item_interval, description):
    """Return (lowest, highest) yearly pay, or (None, None).

    Numeric minimumSalary/maximumSalary win. Otherwise every "$X per unit"
    or "$X to $Y per unit" in the description counts, so a posting for
    levels III and IV reports the bottom of III to the top of IV."""
    low, high = to_number(item_min), to_number(item_max)
    if low:
        unit = item_interval or "month"
        low = yearly(low, unit)
        high = yearly(high, unit) if high else low
        if low:
            return low, max(low, high or low)

    text = clean_text(description)
    values = []
    for start, end, unit in SALARY_TEXT.findall(text):
        for amount in (start, end):
            value = yearly(to_number(amount), unit) if amount else None
            if value:
                values.append(value)
    if not values:
        return None, None
    return min(values), max(values)


def parse_pay_grade(description, classspec):
    """First pay grade found, formatted like SR-20, or None."""
    for text in (clean_text(description), classspec or ""):
        match = PAY_GRADE.search(text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}"
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
        return first_match(FEED_RULES, feed) or title_case(feed), "feed"
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

        title_part, loc_part = split_title(item.findtext("title") or "")

        pub = item.findtext("pubDate")
        title = title_case(title_part)
        duties = clean_text(
            item.findtext("joblisting:examplesofduties", namespaces=NS)
            or "View listing for details."
        )
        description = item.findtext("description") or ""
        salary_low, salary_high = parse_salary(
            item.findtext("joblisting:minimumSalary", namespaces=NS),
            item.findtext("joblisting:maximumSalary", namespaces=NS),
            item.findtext("joblisting:salaryInterval", namespaces=NS),
            description)
        division, division_source = infer_division(
            item.findtext("joblisting:division", namespaces=NS), title, duties)
        jobs.append({
            "title": title,
            "job_number": item.findtext("joblisting:jobNumberSingle", namespaces=NS) or "N/A",
            "division": division,
            "division_source": division_source,
            "location": title_case(loc_part),
            "yearly_salary": salary_low,
            "yearly_salary_max": salary_high,
            "pay_grade": parse_pay_grade(
                description,
                item.findtext("joblisting:classspec", namespaces=NS)),
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
