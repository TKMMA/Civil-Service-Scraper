# 🌊 DAR Career Portal Scraper

An automated, interactive, and exceptionally niche job dashboard specifically tailored for the **Hawaii Division of Aquatic Resources (DAR)**. This portal aggregates listings from both the State of Hawaii's Civil Service system and RCUH contract positions into a single, interactive interface.

View here:
https://tkmma.github.io/Double-Scraper/

## 🚀 Key Features
- **Dual-Source Aggregation:** Combines DLNR Civil Service listings (via NEOGOV) and RCUH positions.
- **Throttled Deep-Scraping:** Navigates the RCUH portal through `conservationcompass.org` to bypass session timeouts and bot-detection.
- **Interactive UI:** Built with DataTables.js, featuring:
  - **Keyword & Division Filtering:** Toggle visibility for specific DLNR divisions or RCUH programs like **DAR**, **HCRI**, or **Coral/Fish** specific roles.
  - **Salary Toggle:** Seamlessly switch between Annual ($/yr) and Monthly ($/mo) salary views.
  - **Accordion Summaries:** Click any row to view deep-scraped "Duties" and "Qualifications" without leaving the portal.
- **High-Contrast UX:** Selected rows are highlighted in deep gray to maintain clarity during exploration.

## 🛠️ The "Oracle" Workaround
Directly scraping the RCUH portal often results in session errors. This project utilizes a **Throttled Hybrid Scraper** that:
1. Scans `conservationcompass.org` to identify relevant active job IDs.
2. Implements a **2-second delay** between requests to visit individual job detail pages.
3. Parses specific text blocks between "DUTIES:" and "PRIMARY QUALIFICATIONS:" to provide real-time job summaries.

## 🤖 Automation & Deployment
The portal is fully autonomous:
- **Scraper:** A Python script (`scraper.py`) runs daily at **8:00 AM HST** via GitHub Actions.
- **Storage:** Scraped data is saved to `jobs.json`.
- **Hosting:** The frontend is served via [GitHub Pages](https://tkmma.github.io/Double-Scraper/).

## 📁 Repository Structure
- `.github/workflows/scrape.yml`: The automation engine (configured with a 15-minute timeout).
- `scraper.py`: The Python logic for throttled data extraction.
- `index.html`: The Bootstrap/DataTables frontend.
- `jobs.json`: The latest synchronized dataset.

---
*Built for the DAR Team to streamline conservation recruitment in Hawaiʻi.*
