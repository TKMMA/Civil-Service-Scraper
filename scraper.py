import json
import re
import requests
import time
from datetime import datetime, timezone
from xml.etree import ElementTree
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
NEOGOV_FEED = "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=hawaii"
COMPASS_URL = "https://opportunities.conservationcompass.org/opportunity/filter/full-time-jobs"

def clean_text(text):
    if not text: return ""
    text = re.sub('<[^<]+?>', ' ', text)
    text = " ".join(text.split())
    words = text.lower().split()
    # Preserving specific acronyms common in Hawaii DAR/RCUH listings
    caps_list = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "dlnr", "rcuh", "dar", "scuba", "hcri", "himb"]
    return " ".join(w.upper() if w in caps_list else w.capitalize() for w in words)

def parse_salary(text):
    if not text: return None
    # Pattern to catch various formats: $4,000 per month, $50,000/yr, etc.
    pattern = r'\$\s*([\d,]+(?:\.\d+)?).*?(month|year|hr|hour|mon)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            amount = float(match.group(1).replace(',', ''))
            unit = match.group(2).lower()
            if 'mon' in unit: return amount * 12
            if 'yr' in unit or 'year' in unit: return amount
            if 'hr' in unit or 'hour' in unit: return amount * 2080
        except: return None
    return None

def scrape_civil_service():
    jobs = []
    ns = {'joblisting': 'http://www.neogov.com/namespaces/JobListing'}
    try:
        r = requests.get(NEOGOV_FEED, timeout=30)
        root = ElementTree.fromstring(r.content)
        for item in root.findall("./channel/item"):
            dept = item.findtext("joblisting:department", namespaces=ns) or ""
            # Filtering for DLNR-specific roles
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
    except Exception as e: print(f"Civil Error: {e}")
    return jobs

def scrape_rcuh_compass():
    jobs = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        r = requests.get(COMPASS_URL, headers=headers, timeout=30)
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = soup.find_all('tr')
        
        for row in rows:
            cols = row.find_all('td')
            if cols and "RCUH" in cols[0].text:
                title_raw = cols[0].text.split(':', 1)[-1].split('ID#')[0].strip(" –-")
                id_match = re.search(r'ID#\s*(\d+)', cols[0].text)
                loc = row.find('span', class_='badge')
                link_tag = row.find('a')
                detail_url = link_tag['href'] if link_tag else None
                
                salary_val = None
                duties_text = "Detailed duties available on the RCUH portal."

                if detail_url:
                    # THROTTLE: Be polite to the server
                    time.sleep(2) 
                    try:
                        det = requests.get(detail_url, headers=headers, timeout=15)
                        ds = BeautifulSoup(det.text, 'html.parser')
                        page_text = ds.get_text()
                        
                        # Extract Salary
                        if "MONTHLY SALARY:" in page_text:
                            sal_line = page_text.split("MONTHLY SALARY:")[1].split(".")[0]
                            salary_val = parse_salary(sal_line + " per month")
                        
                        # Extract Duties text block
                        if "DUTIES:" in page_text:
                            d_raw = page_text.split("DUTIES:")[1].split("PRIMARY QUALIFICATIONS")[0].strip()
                            duties_text = clean_text(d_raw)
                    except: pass

                jobs.append({
                    "title": clean_text(title_raw),
                    "id": id_match.group(1) if id_match else "N/A",
                    "location": clean_text(loc.text) if loc else "Hawaii",
                    "yearly_salary": salary_val,
                    "closing": cols[2].text.strip() if len(cols) > 2 else "Continuous",
                    "link": detail_url,
                    "duties": duties_text
                })
    except Exception as e: print(f"RCUH Error: {e}")
    return jobs

def main():
    data = {
        "civil_service": scrape_civil_service(),
        "rcuh": scrape_rcuh_compass(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat()
    }
    with open("jobs.json", "w") as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    main()
