# 🌊 Civil Service Listings Scraper

launch: https://tkmma.github.io/Civil-Service-Scraper/

An automated, interactive dashboard tailored for **Hawaii DLNR Civil Service openings**. The portal now focuses only on Civil Service listings sourced from the State of Hawaii feed.

## 🚀 Key Features
- **Single-Source Civil Service Aggregation:** Pulls DLNR listings from the State NEOGOV feed.
- **Interactive UI:** Built with DataTables.js, featuring:
  - **Division Filtering:** Toggle visibility for specific DLNR divisions.
  - **Salary Toggle:** Switch between Annual ($/yr) and Monthly ($/mo) salary views.
  - **Accordion Summaries:** Click any row to view duties without leaving the table.
- **Readable Visual Theme:** Inter font and a high-contrast blue/turquoise palette for clarity.

## 🤖 Automation & Deployment
- **Scraper:** Python script (`scraper.py`) generates the dataset.
- **Storage:** Scraped data is saved to `jobs.json`.
- **Hosting:** Frontend can be served via GitHub Pages.

## 📁 Repository Structure
- `.github/workflows/scrape.yml`: Automation workflow.
- `scraper.py`: Python logic for Civil Service extraction.
- `index.html`: Bootstrap/DataTables frontend.
- `jobs.json`: Latest synchronized dataset.

---
*Built to streamline DLNR Civil Service recruitment visibility in Hawaiʻi.*
