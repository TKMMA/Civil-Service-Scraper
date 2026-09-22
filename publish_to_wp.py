#!/usr/bin/env python3
"""Publish jobs.json to the DAR WordPress Media Library.

Uploads jobs.json as a single text file (dar-jobs.txt) via the WordPress REST
API, removes superseded copies, then verifies the result is readable by an
anonymous visitor exactly the way the careers page reads it.

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
import sys

import requests

WP_BASE = os.environ.get("WP_BASE", "https://dar.hawaii.gov").rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASS = os.environ.get("WP_APP_PASS", "").replace(" ", "")
FORCE = os.environ.get("FORCE", "") == "1"

MEDIA_SLUG = "dar-jobs"       # the careers page searches for this
UPLOAD_NAMES = ["dar-jobs.txt", "dar-jobs.csv", "dar-jobs.json"]
DATA_FILE = "jobs.json"
PREV_FILE = "jobs.prev.json"
MEDIA_ENDPOINT = f"{WP_BASE}/wp-json/wp/v2/media"
TIMEOUT = 45


def die(msg):
    # ::error:: makes this surface as an annotation in the Actions run
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


def upload(session):
    with open(DATA_FILE, "rb") as fh:
        payload = fh.read()

    attempts = []
    for name in UPLOAD_NAMES:
        if name.endswith(".csv"):
            ctype = "text/csv"
        elif name.endswith(".json"):
            ctype = "application/json"
        else:
            ctype = "text/plain"

        headers = dict(auth())
        headers.update({
            "Content-Type": ctype,
            "Content-Disposition": f'attachment; filename="{name}"',
            "Accept": "application/json",
        })
        resp = session.post(MEDIA_ENDPOINT, headers=headers, data=payload,
                            timeout=TIMEOUT)

        if resp.status_code == 401:
            die("WordPress rejected the credentials (401). The Application "
                "Password may have been revoked, or the username is wrong.")

        if resp.status_code in (200, 201):
            item = resp.json()
            info(f"Uploaded {name} as media id={item['id']} "
                 f"slug={item.get('slug')} url={item.get('source_url')}")
            return item["id"], item.get("source_url", "")

        attempts.append(f"{name} -> HTTP {resp.status_code} {resp.text[:160]}")
        info(f"::warning::{name} refused, trying the next file type.")

    die("Every file type was refused by WordPress. Attempts:\n"
        + "\n".join(attempts))


def cleanup(session, keep_id):
    """Delete older dar-jobs uploads so the library holds exactly one."""
    params = {"search": MEDIA_SLUG, "per_page": 100, "orderby": "date",
              "order": "desc", "_fields": "id,slug"}
    resp = session.get(MEDIA_ENDPOINT, headers=auth(), params=params, timeout=TIMEOUT)
    if not resp.ok:
        info(f"::warning::Could not list old uploads to clean up "
             f"(HTTP {resp.status_code}). Nothing deleted.")
        return

    removed = 0
    for item in resp.json():
        if item["id"] == keep_id:
            continue
        # only ever touch things whose slug really is ours
        if not str(item.get("slug", "")).startswith(MEDIA_SLUG):
            continue
        d = session.delete(f"{MEDIA_ENDPOINT}/{item['id']}", headers=auth(),
                           params={"force": "true"}, timeout=TIMEOUT)
        if d.ok:
            removed += 1
        else:
            info(f"::warning::Could not delete media id={item['id']} "
                 f"(HTTP {d.status_code}).")
    info(f"Cleaned up {removed} superseded upload(s).")


def verify_public(expected_count):
    """Read it back the way an anonymous visitor does. No credentials."""
    lookup = requests.get(
        MEDIA_ENDPOINT,
        params={"search": MEDIA_SLUG, "per_page": 1, "orderby": "date",
                "order": "desc", "_fields": "source_url"},
        timeout=TIMEOUT,
    )
    if not lookup.ok:
        die(f"Anonymous media lookup failed with HTTP {lookup.status_code}. "
            "The careers page will not be able to find the data.")

    items = lookup.json()
    if not items or not items[0].get("source_url"):
        die("Anonymous media lookup returned nothing. The careers page will "
            "fall back to its error state.")

    source_url = items[0]["source_url"]
    fetched = requests.get(source_url, timeout=TIMEOUT,
                           headers={"Cache-Control": "no-cache"})
    if not fetched.ok:
        die(f"Anonymous fetch of {source_url} failed with "
            f"HTTP {fetched.status_code}.")

    try:
        data = fetched.json()
    except Exception as exc:
        die(f"Published file did not parse as JSON: {exc}")

    got = len(data.get("civil_service") or [])
    if got != expected_count:
        die(f"Published file has {got} openings but we uploaded "
            f"{expected_count}. The lookup may be resolving an older file.")

    info(f"Verified anonymously: {got} openings at {source_url}")
    info(f"Data timestamp: {data.get('generated_at_utc')}")


def main():
    expected = load_and_check()
    with requests.Session() as session:
        media_id, _ = upload(session)
        cleanup(session, media_id)
    verify_public(expected)
    info("Publish complete.")


if __name__ == "__main__":
    main()
