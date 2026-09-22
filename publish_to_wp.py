#!/usr/bin/env python3
"""Publish jobs.json into a hidden WordPress page on the DAR site.

The Media Library on dar.hawaii.gov only accepts images, PDFs and video, so a
data file cannot be uploaded. WordPress will happily store text as a *page*
though, and pages are readable by anonymous visitors through the REST API.

So the job data is base64 encoded and parked inside an HTML comment on a page
that is not linked from anywhere. A human who finds the URL sees a blank page.
The careers page widget reads it through the REST API and decodes it.

Refuses to publish data that looks broken, so a bad upstream fetch can never
blank the listings on the live page.

Environment:
  WP_BASE      site root, default https://dar.hawaii.gov
  WP_USER      WordPress username
  WP_APP_PASS  Application Password (spaces are fine, they get stripped)
  FORCE        set to 1 to override the plausibility guard
"""

import base64
import json
import os
import re
import sys

import requests

WP_BASE = os.environ.get("WP_BASE", "https://dar.hawaii.gov").rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASS = os.environ.get("WP_APP_PASS", "").replace(" ", "")
FORCE = os.environ.get("FORCE", "") == "1"

PAGE_SLUG = "dar-jobs-data"
PAGE_TITLE = "DAR job listings data"
MARKER = "DARJOBS1"
DATA_FILE = "jobs.json"
PREV_FILE = "jobs.prev.json"
PAGES_ENDPOINT = f"{WP_BASE}/wp-json/wp/v2/pages"
TIMEOUT = 45


def die(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def info(msg):
    print(msg, flush=True)


def auth():
    if not WP_USER or not WP_APP_PASS:
        die("WP_USER and WP_APP_PASS must both be set as repository secrets.")
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def load_and_check():
    """Parse jobs.json and refuse anything implausible."""
    try:
        with open(DATA_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        die(f"{DATA_FILE} missing or unparseable: {exc}")

    jobs = data.get("civil_service")
    if not isinstance(jobs, list):
        die("civil_service is not a list. Refusing to publish.")

    required = {"title", "job_number", "division", "location", "link"}
    for idx, job in enumerate(jobs):
        missing = required - set(job or {})
        if missing:
            die(f"job {idx} is missing required fields: {sorted(missing)}")

    new_count = len(jobs)
    if new_count == 0:
        die("Scraper returned 0 openings. Refusing to publish an empty list, "
            "which would blank the careers page.")

    prev_count = None
    if os.path.exists(PREV_FILE):
        try:
            with open(PREV_FILE, encoding="utf-8") as fh:
                prev_count = len(json.load(fh).get("civil_service") or [])
        except Exception:
            prev_count = None

    if prev_count and prev_count >= 4 and new_count < prev_count / 2:
        if not FORCE:
            die(f"Openings fell from {prev_count} to {new_count}, more than half. "
                "This usually means the NeoGov feed changed shape rather than "
                "that the jobs vanished. Refusing to publish. Re-run the "
                "workflow with FORCE=1 if the drop is genuine.")
        info(f"FORCE set, publishing despite drop {prev_count} -> {new_count}.")

    info(f"Validated {new_count} openings"
         + (f" (previously {prev_count})" if prev_count is not None else ""))
    return new_count


def build_content():
    """jobs.json, base64 encoded, wrapped in an invisible HTML comment.

    Base64 survives WordPress's content filters untouched. Raw JSON would not:
    wptexturize turns straight quotes into curly ones and would corrupt it.
    """
    with open(DATA_FILE, "rb") as fh:
        payload = fh.read()
    encoded = base64.b64encode(payload).decode("ascii")
    return (
        "<!-- Machine generated, do not edit. Refreshed daily by the "
        "Civil-Service-Scraper GitHub Action.\n"
        f"{MARKER} {encoded} {MARKER} -->"
    )


def find_page(session):
    resp = session.get(PAGES_ENDPOINT, headers=auth(),
                       params={"slug": PAGE_SLUG, "status": "publish,draft,private",
                               "per_page": 1, "_fields": "id,slug,status"},
                       timeout=TIMEOUT)
    if resp.status_code == 401:
        die("WordPress rejected the credentials (401). The Application "
            "Password may have been revoked, or the username is wrong.")
    if not resp.ok:
        die(f"Could not look up the data page: HTTP {resp.status_code} "
            f"{resp.text[:300]}")
    items = resp.json()
    return items[0]["id"] if items else None


def write_page(session, page_id, content):
    body = {"title": PAGE_TITLE, "content": content, "status": "publish"}
    if page_id:
        url = f"{PAGES_ENDPOINT}/{page_id}"
    else:
        url = PAGES_ENDPOINT
        body["slug"] = PAGE_SLUG

    resp = session.post(url, headers={**auth(), "Content-Type": "application/json"},
                        data=json.dumps(body), timeout=TIMEOUT)

    if resp.status_code == 401:
        die("WordPress rejected the credentials (401).")
    if resp.status_code == 403:
        die("WordPress accepted the login but refused to write the page (403). "
            "The account may lack publish_pages, or a security plugin is "
            "blocking REST writes.")
    if resp.status_code not in (200, 201):
        die(f"Writing the page failed with HTTP {resp.status_code}: "
            f"{resp.text[:400]}")

    item = resp.json()
    info(f"{'Updated' if page_id else 'Created'} page id={item['id']} "
         f"slug={item.get('slug')} link={item.get('link')}")
    return item["id"]


def verify_public(expected_count):
    """Read it back the way an anonymous visitor does. No credentials."""
    resp = requests.get(PAGES_ENDPOINT,
                        params={"slug": PAGE_SLUG, "per_page": 1,
                                "_fields": "content,link"},
                        timeout=TIMEOUT)
    if not resp.ok:
        die(f"Anonymous page lookup failed with HTTP {resp.status_code}. "
            "The careers page will not be able to find the data.")

    items = resp.json()
    if not items:
        die("Anonymous lookup returned nothing. The page may not be published.")

    rendered = (items[0].get("content") or {}).get("rendered") or ""
    match = re.search(rf"{MARKER}\s+([A-Za-z0-9+/=]+)\s+{MARKER}", rendered)
    if not match:
        die("Could not find the data marker in the published page. Something "
            "on the site is stripping HTML comments from page content.")

    try:
        data = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
    except Exception as exc:
        die(f"Published payload did not decode: {exc}")

    got = len(data.get("civil_service") or [])
    if got != expected_count:
        die(f"Published page has {got} openings but we wrote {expected_count}.")

    info(f"Verified anonymously: {got} openings at {items[0].get('link')}")
    info(f"Data timestamp: {data.get('generated_at_utc')}")


def main():
    expected = load_and_check()
    content = build_content()
    info(f"Payload is {len(content)} characters of base64 in an HTML comment.")
    with requests.Session() as session:
        page_id = find_page(session)
        write_page(session, page_id, content)
    verify_public(expected)
    info("Publish complete.")


if __name__ == "__main__":
    main()
