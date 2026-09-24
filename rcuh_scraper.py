#!/usr/bin/env python3
"""Read RCUH job openings and keep the ones tied to DLNR.

RCUH posts every RCUH job (all UH projects) on a PeopleSoft Candidate
Gateway page. That page only renders for a client that keeps cookies, so
this uses a requests.Session: the first request picks up PeopleSoft's guest
session cookies and the list comes back as ordinary HTML, one labeled span
per field.

The project name never says "DAR", so each posting's full description is
read and matched against DLNR division names written out in full (the same
idea as DUTIES_RULES in scraper.py). Only postings that name a DLNR division
or the Department of Land and Natural Resources are kept.

Output rows use the same field names as the civil service rows where they
overlap (title, job_number, division, location, yearly_salary,
yearly_salary_max, posted, closing, link, duties), so the careers page can
render both with one script.
"""

import html
import re
import time

import requests

from scraper import (AQUATIC, BOATING, COASTAL, CONVEYANCES, ENFORCEMENT,
                     ENGINEERING, FORESTRY, HISTORIC, LAND, NOT_LISTED, PARKS,
                     WATER, AND, clean_text, first_match)

BASE = ("https://hr.rcuh.com/psc/hcmprd_exapp/EMPLOYEE/HRMS/c/"
        "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL")
LIST_URL = BASE + "?Page=HRS_APP_SCHJOB_FL&Action=U"
DETAIL_URL = (BASE + "?Page=HRS_APP_JBPST_FL&Action=U&FOCUS=Applicant"
              "&SiteId=1&JobOpeningId={id}&PostingSeq=1")
TIMEOUT = 45
PAUSE = 1.0  # seconds between detail requests, to stay polite
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# PeopleSoft field ids on the search results page, one per job row ($0, $1..)
FIELDS = {
    "title": "SCH_JOB_TITLE",
    "job_number": "HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID",
    "project": "LOCATION",
    "fte": "DERIVED_RCUH_RCUH_FTE_DESCR",
    "pay_min": "RCUH_JO_FCOMP_V_RCUH_MIN_PAY_RANGE",
    "pay_max": "RCUH_JO_FCOMP_V_RCUH_MAX_PAY_RANGE",
    "pay_frequency": "RCUH_JO_FCOMP_V_DESCRSHORT",
    "island": "RCUH_JO_FCOMP_V_RCUH_DESCR05",
    "posted": "SCH_OPENED",
    "closing": "HRS_JO_PST_CLS_DT",
}

# DLNR divisions named in full in a posting. Order matters: DAR first, so a
# posting that mentions DAR alongside another division is filed under DAR.
DLNR_RULES = [
    (r"division of aquatic resources|\(DAR\)", AQUATIC),
    (rf"division of conservation {AND} resources enforcement|\(DOCARE\)",
     ENFORCEMENT),
    (rf"division of forestry {AND} wildlife|\(DOFAW\)", FORESTRY),
    (r"division of state parks", PARKS),
    (rf"division of boating {AND} ocean recreation|\(DOBOR\)", BOATING),
    (r"bureau of conveyances", CONVEYANCES),
    (r"dlnr land division", LAND),
    (r"dlnr engineering division", ENGINEERING),
    (r"commission on water resource management", WATER),
    (r"state historic preservation division", HISTORIC),
    (rf"office of conservation {AND} coastal lands", COASTAL),
]
DLNR_NAMED = re.compile(rf"department of land {AND} natural resources", re.I)

PER_YEAR = {"month": 12, "hour": 2080, "year": 1, "annual": 1}
SECTION_END = re.compile(
    r"\b(?:PRIMARY QUALIFICATIONS|SECONDARY QUALIFICATIONS|MINIMUM "
    r"QUALIFICATIONS|POSITION REQUIREMENTS|PHYSICAL/?MEDICAL|"
    r"SUPPLEMENTAL INFORMATION|APPLICATION REQUIREMENTS)\b")


# RCUH's pages show the okina as an inverted question mark (a character set
# mix-up on their side), e.g. "Napu?u". Put the okina back between letters.
BAD_OKINA = re.compile("(?<=[A-Za-z])" + chr(0xBF) + "(?=[A-Za-z])")


def fix_okina(text):
    return BAD_OKINA.sub(chr(0x2BB), text)


def field_values(page, field_id):
    """Every value of one PeopleSoft field, in row order."""
    pattern = re.compile(
        r"id=['\"]" + re.escape(field_id) + r"\$(\d+)['\"]\s*>([^<]*)<")
    rows = {}
    for idx, value in pattern.findall(page):
        rows[int(idx)] = fix_okina(html.unescape(value).strip())
    return rows


def money(text):
    try:
        return float(str(text).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def yearly(amount, frequency):
    if not amount:
        return None
    freq = (frequency or "").lower()
    factor = next((v for k, v in PER_YEAR.items() if freq.startswith(k)), 12)
    return round(amount * factor)


def page_text(page):
    """Readable text of a detail page, from the job summary onward."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    text = fix_okina(clean_text(text))
    start = text.find("Job Summary")
    return text[start:] if start != -1 else text


def duties_from(text):
    match = re.search(r"DUTIES:\s*(.+)", text)
    if not match:
        return ""
    duties = SECTION_END.split(match.group(1), maxsplit=1)[0].strip()
    if len(duties) > 700:
        duties = duties[:700].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return duties


def dlnr_division(text):
    """(division label, rule source) for a posting, or (None, None)."""
    label = first_match(DLNR_RULES, text)
    if label:
        return label, "posting"
    if DLNR_NAMED.search(text):
        return NOT_LISTED, "posting"
    return None, None


def fetch_list(session):
    for attempt in range(1, 4):
        resp = session.get(LIST_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        titles = field_values(resp.text, FIELDS["title"])
        if titles:
            return resp.text
        time.sleep(2 * attempt)
    raise RuntimeError("RCUH search page returned no job rows after 3 tries. "
                       "The page layout or session handling may have changed.")


def scrape_rcuh():
    with requests.Session() as session:
        session.headers.update(HEADERS)
        page = fetch_list(session)

        columns = {key: field_values(page, fid) for key, fid in FIELDS.items()}
        found = re.search(r"(\d+)\D{0,8}jobs? found", page)
        total = len(columns["title"])
        if found and int(found.group(1)) != total:
            print(f"::warning::RCUH says {found.group(1)} jobs but {total} rows "
                  "were parsed.", flush=True)

        jobs = []
        for idx in sorted(columns["title"]):
            row = {key: col.get(idx, "") for key, col in columns.items()}
            job_id = row["job_number"]
            if not job_id:
                continue
            time.sleep(PAUSE)
            link = DETAIL_URL.format(id=job_id)
            try:
                detail = session.get(link, timeout=TIMEOUT)
                detail.raise_for_status()
                text = page_text(detail.text)
            except requests.RequestException as exc:
                print(f"::warning::RCUH job {job_id} detail failed: {exc}",
                      flush=True)
                continue

            division, source = dlnr_division(text)
            if not division:
                continue

            low, high = money(row["pay_min"]), money(row["pay_max"])
            jobs.append({
                "title": row["title"] or "Untitled position",
                "job_number": job_id,
                "project": row["project"],
                "division": division,
                "division_source": source,
                "location": row["island"] or "Hawaii",
                "fte": row["fte"],
                "pay_frequency": row["pay_frequency"],
                "yearly_salary": yearly(low, row["pay_frequency"]),
                "yearly_salary_max": yearly(high or low, row["pay_frequency"]),
                "posted": row["posted"],
                "closing": row["closing"] or "Continuous",
                "link": link,
                "duties": duties_from(text) or "View the full listing for details.",
            })

    print(f"RCUH had {total} listings; {len(jobs)} name a DLNR division or "
          "DLNR.", flush=True)
    for job in jobs:
        print(f"  RCUH {job['job_number']}: {job['title']} -> {job['division']}",
              flush=True)
    return jobs


if __name__ == "__main__":
    import json
    print(json.dumps(scrape_rcuh(), indent=2))
