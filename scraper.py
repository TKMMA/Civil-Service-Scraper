import json
import re
import requests
from datetime import datetime, timezone
from xml.etree import ElementTree

# --- CONFIGURATION ---
NEOGOV_FEED = "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=hawaii"


def clean_text(text):
    if not text:
        return ""
    text = re.sub('<[^<]+?>', ' ', text)
    text = " ".join(text.split())
    words = text.lower().split()
    caps_list = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "dlnr", "dar", "scuba", "hcri", "himb"]
    return " ".join(w.upper() if w in caps_list else w.capitalize() for w in words)


def parse_salary(text):
    if not text:
        return None
    pattern = r'\$\s*([\d,]+(?:\.\d+)?).*?(month|year|hr|hour|mon)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            amount = float(match.group(1).replace(',', ''))
            unit = match.group(2).lower()
            if 'mon' in unit:
                return amount * 12
            if 'yr' in unit or 'year' in unit:
                return amount
            if 'hr' in unit or 'hour' in unit:
                return amount * 2080
        except Exception:
            return None
    return None


def scrape_civil_service():
    jobs = []
    ns = {'joblisting': 'http://www.neogov.com/namespaces/JobListing'}
    try:
        r = requests.get(NEOGOV_FEED, timeout=30)
        root = ElementTree.fromstring(r.content)
        for item in root.findall("./channel/item"):
            dept = item.findtext("joblisting:department", namespaces=ns) or ""
            if any(x in dept for x in ["Land & Natural Resources", "DLNR"]):
                raw_title = item.findtext("title") or ""
                title_part, loc_part = raw_title.split("-", 1) if "-" in raw_title else (raw_title, "Hawaii")

                jobs.append({
                    "title": clean_text(title_part),
                    "job_number": item.findtext("joblisting:jobNumberSingle", namespaces=ns) or "N/A",
                    "division": clean_text(item.findtext("joblisting:division", namespaces=ns) or "DLNR"),
                    "location": clean_text(loc_part),
                    "yearly_salary": parse_salary(item.findtext("description") or ""),
                    "posted": item.findtext("pubDate")[:16] if item.findtext("pubDate") else "",
                    "closing": item.findtext("joblisting:advertiseToDateTime", namespaces=ns) or "Continuous",
                    "link": item.findtext("link"),
                    "duties": clean_text(item.findtext("joblisting:examplesofduties", namespaces=ns) or "View listing for details.")
                })
    except Exception as e:
        print(f"Civil Error: {e}")
    return jobs


def main():
    data = {
        "civil_service": scrape_civil_service(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat()
    }
    with open("jobs.json", "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
