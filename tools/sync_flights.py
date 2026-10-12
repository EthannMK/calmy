#!/usr/bin/env python3
"""Save real departures from AviationStack into BigQuery and into data/flights_snapshot.json.

Why: the free live plan only returns flights that fly today, it allows 100 requests a month, and the
live service can fail at a bad moment. This script takes one real snapshot of departures. The app then
(1) lets travelers pick any date up to a month ahead and (2) keeps working offline during a presentation.

Run it in Cloud Shell, where the Google Cloud login and the `bq` tool already exist:

    export AVIATIONSTACK_KEY=your_key
    python3 tools/sync_flights.py --project calmy-demo-123456

Each airport costs one request per page (100 flights). The default is 6 airports x 1 page = 6 of the 100
monthly requests. Then deploy again so the new data/flights_snapshot.json goes into the service.

Read a snapshot back out of BigQuery on another machine:

    python3 tools/sync_flights.py --project calmy-demo-123456 --from-bq
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import live, markets  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flights_snapshot.json")
HUBS = "BKK,LHR,JFK,SIN,DEL,SYD"
SCHEMA = ("snapshot_date:DATE,synced_at:TIMESTAMP,code:STRING,airline:STRING,origin:STRING,origin_city:STRING,dest:STRING,"
          "dest_city:STRING,time:STRING,tz:STRING,market:STRING")


def fetch(hub: str, pages: int) -> list[dict]:
    """Real departures for today from one airport, reduced to what repeats every day: number, route and clock time."""
    out = {}
    for page in range(pages):
        body = live._http_get({"dep_iata": hub, "limit": 100, "offset": page * 100})
        if "error" in body:
            raise SystemExit(f"{hub}: the live service refused the request: {(body['error'] or {}).get('message')}")
        for rec in body.get("data", []):
            f = live.normalize(rec)
            if not f or f["status"] == "cancelled" or not f["scheduled"]:
                continue
            tz = (rec.get("departure") or {}).get("timezone")
            out.setdefault(f["code"], {
                "code": f["code"], "airline": f["airline"], "origin": f["origin"], "origin_city": f["origin_city"],
                "dest": f["dest"], "dest_city": f["dest_city"], "time": f["scheduled"], "tz": tz or "UTC", "market": f["market"],
            })
        if len(body.get("data", [])) < 100:
            break
    return list(out.values())


def to_bigquery(rows: list[dict], project: str, dataset: str, table: str) -> None:
    if not shutil.which("bq"):
        print("The bq tool was not found, so BigQuery was skipped. Run this in Cloud Shell.")
        return
    now = datetime.now(timezone.utc)
    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False) as fh:
        for r in rows:
            fh.write(json.dumps({"snapshot_date": now.date().isoformat(), "synced_at": now.isoformat(), **r}) + "\n")
        path = fh.name
    subprocess.run(["bq", f"--project_id={project}", "mk", "-d", "--location=asia-southeast1", dataset], capture_output=True)  # fine if it exists
    r = subprocess.run(["bq", f"--project_id={project}", "load", "--source_format=NEWLINE_DELIMITED_JSON",
                        f"{dataset}.{table}", path, SCHEMA], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    os.unlink(path)


def from_bigquery(project: str, dataset: str, table: str) -> list[dict]:
    sql = (f"SELECT code,airline,origin,origin_city,dest,dest_city,time,tz,market FROM `{project}.{dataset}.{table}` "
           f"WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `{project}.{dataset}.{table}`)")
    r = subprocess.run(["bq", f"--project_id={project}", "query", "--use_legacy_sql=false", "--format=json", "--max_rows=100000", sql],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr)
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", "calmy-demo-123456"))
    ap.add_argument("--dataset", default="koda")
    ap.add_argument("--table", default="flights_snapshot")
    ap.add_argument("--hubs", default=HUBS, help="comma separated airport codes")
    ap.add_argument("--pages", type=int, default=1, help="pages of 100 flights per airport (one request each)")
    ap.add_argument("--out", default=OUT, help="where to write the JSON snapshot")
    ap.add_argument("--no-bq", action="store_true", help="only write the JSON file")
    ap.add_argument("--from-bq", action="store_true", help="rebuild the JSON file from the latest BigQuery snapshot, no live calls")
    a = ap.parse_args()

    if a.from_bq:
        rows = from_bigquery(a.project, a.dataset, a.table)
    else:
        if not os.environ.get("AVIATIONSTACK_KEY"):
            raise SystemExit("Set AVIATIONSTACK_KEY first.")
        rows = []
        for hub in [h.strip().upper() for h in a.hubs.split(",") if h.strip()]:
            got = fetch(hub, a.pages)
            print(f"{hub}: {len(got)} real flights")
            rows += got
        if not rows:
            raise SystemExit("No flights came back, so nothing was saved.")
        if not a.no_bq:
            to_bigquery(rows, a.project, a.dataset, a.table)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "source": "aviationstack" if not a.from_bq else "bigquery",
                   "flights": rows}, fh, ensure_ascii=False, indent=0)
    print(f"Saved {len(rows)} flights to {a.out}")


if __name__ == "__main__":
    main()
