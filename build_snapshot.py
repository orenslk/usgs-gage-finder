#!/usr/bin/env python3
"""
Build the site snapshot for the USGS stream gage finder and embed it in index.html.

Data source: USGS Water Data OGC APIs (https://api.waterdata.usgs.gov/ogcapi/v1/).
Nothing here uses the legacy waterservices.usgs.gov / NWIS peak services, which are
scheduled for shutdown in February 2027.

What it does (United States incl. territories, stream sites only)
  1. combined-metadata  -> every discharge (00060) time series of three kinds:
                           daily mean (stat 00003), continuous (stat 00011), annual peaks.
                           Also gives name, state, county, drainage areas, coordinates.
  2. peaks              -> every annual peak record with parameter 00060 (discharge).
                           Stage-only peaks (00065) and rows with no value are ignored.
  3. Writes a compact site table into index.html between the SNAPSHOT markers.

About 100 paged requests in total. Takes several minutes.

Usage
  USGS_API_KEY=your_key python3 build_snapshot.py     # key strongly recommended, see README

Requires: Python 3.9+ and `requests`.
"""

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

import requests

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
SITE_TYPE_CODE = "ST"          # Stream

# Country filter. "US" includes U.S. territories (Puerto Rico, Guam, ...) but leaves out
# the Canadian, Japanese, and Pacific stations USGS also stores. Use None for everything.
COUNTRY_CODE = "US"
DISCHARGE = "00060"            # parameter code: discharge, ft^3/s
STAT_DAILY_MEAN = "00003"
STAT_INSTANTANEOUS = "00011"   # continuous / "unit values"

# State the page shows when it first opens ("" = all states). Must match a
# state name exactly as USGS spells it, e.g. "Michigan".
DEFAULT_STATE = "Michigan"

# States to print a detailed summary for after the build (for spot checks).
REPORT_STATES = ["Michigan"]

API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections"
SITE_PAGE = "https://waterdata.usgs.gov/monitoring-location/{id}/"

PAGE_LIMIT = 10000             # items per page
TIMEOUT = 180                  # seconds per request
PAUSE = 1.0                    # seconds between page requests (politeness)

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "index.html")
START_MARK = "/*__SNAPSHOT_START__*/"
END_MARK = "/*__SNAPSHOT_END__*/"


# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "usgs-gage-finder-snapshot/2.0 (educational)"
if os.environ.get("USGS_API_KEY"):
    SESSION.headers["X-Api-Key"] = os.environ["USGS_API_KEY"]

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 6


def get_json(url, params=None):
    """GET with retries on rate limiting (429), server errors, and network errors.
    Waits 10, 20, 40, 80, 120 s between attempts (or the server's Retry-After, max 300 s).
    Also slows down when the rate-limit headers say the hourly quota is nearly used."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = SESSION.get(url, params=params, timeout=TIMEOUT)
            remaining = r.headers.get("X-RateLimit-Remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) < 20:
                print(f"    (only {remaining} requests left this hour; pausing 60 s)", flush=True)
                time.sleep(60)
            if r.status_code not in RETRY_STATUS:
                r.raise_for_status()
                return r.json()
            why = f"HTTP {r.status_code}"
            wait = r.headers.get("Retry-After")
            wait = min(float(wait), 300) if wait and wait.isdigit() else None
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as ex:
            why, wait = type(ex).__name__, None
        if attempt == MAX_ATTEMPTS:
            sys.exit(f"\nGave up on {url} after {attempt} attempts ({why}). "
                     "Nothing was written; re-run later.")
        wait = wait or min(10 * 2 ** (attempt - 1), 120)
        if why == "HTTP 429":
            why += " (rate limited; set USGS_API_KEY, see README)"
        print(f"    ! {why}; retrying in {wait:.0f}s", flush=True)
        time.sleep(wait)


def fetch_all(collection, params, label):
    """Fetch every feature of a query, following 'next' links."""
    url = f"{API}/{collection}/items"
    d = get_json(url, {"f": "json", "limit": PAGE_LIMIT, **params})
    feats = list(d.get("features", []))
    print(f"    {label}: {len(feats):,}", flush=True)
    while True:
        nxt = [l["href"] for l in d.get("links", []) if l.get("rel") == "next"]
        if not nxt:
            break
        time.sleep(PAUSE)
        d = get_json(nxt[0])
        feats.extend(d.get("features", []))
        print(f"    {label}: {len(feats):,}", flush=True)
    return feats


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def to_date(ts):
    """'2007-10-01T04:00:00+00:00' -> date(2007, 10, 1). Daily timestamps are local
    midnight expressed in UTC, so the leading date is the local day."""
    return date.fromisoformat(ts[:10]) if ts else None


def merge_intervals(iv):
    out = []
    for b, e in sorted(iv):
        if out and b <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([b, e])
    return out


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def clean_county(c):
    return re.sub(r"\s+(County|Parish|Borough|Census Area|Municipio|Municipality)$", "", c or "").strip() or None


def short_id(mlid):
    """'USGS-04141000' -> '04141000'; other agencies keep their prefix."""
    return mlid[5:] if mlid.startswith("USGS-") else mlid


# ----------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------
SERIES_PROPS = ("monitoring_location_id,monitoring_location_name,state_name,county_name,"
                "drainage_area,contributing_drainage_area,data_type,statistic_id,begin,end")


def build():
    sites = {}
    daily_iv = defaultdict(list)
    cont_iv = defaultdict(list)
    peak_series_sites = set()

    print("1. Discharge time series (combined-metadata)", flush=True)
    for data_type, stat in (("Daily values", STAT_DAILY_MEAN),
                            ("Continuous values", STAT_INSTANTANEOUS),
                            ("Peaks", None)):
        params = {"site_type_code": SITE_TYPE_CODE, "parameter_code": DISCHARGE,
                  "data_type": data_type, "properties": SERIES_PROPS}
        if COUNTRY_CODE:
            params["country_code"] = COUNTRY_CODE
        if stat:
            params["statistic_id"] = stat
        for f in fetch_all("combined-metadata", params, data_type):
            p = f["properties"]
            sid = p["monitoring_location_id"]
            if sid not in sites:
                coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
                sites[sid] = {
                    "id": short_id(sid),
                    "name": (p.get("monitoring_location_name") or "").strip(),
                    "state": p.get("state_name") or "Unknown",
                    "county": clean_county(p.get("county_name")),
                    "lat": round(coords[1], 4) if coords[1] is not None else None,
                    "lon": round(coords[0], 4) if coords[0] is not None else None,
                    "da": p.get("drainage_area"),
                    "cda": p.get("contributing_drainage_area"),
                }
            b, e = to_date(p.get("begin")), to_date(p.get("end"))
            if data_type == "Peaks":
                peak_series_sites.add(sid)
            elif p.get("statistic_id") == stat and b and e:
                (daily_iv if stat == STAT_DAILY_MEAN else cont_iv)[sid].append((b, e))

    print("2. Annual peak discharge records (peaks)", flush=True)
    peak_years = defaultdict(set)
    params = {"site_type_code": SITE_TYPE_CODE, "parameter_code": DISCHARGE, "skipGeometry": "true",
              "properties": "monitoring_location_id,value,water_year"}
    if COUNTRY_CODE:
        params["country_code"] = COUNTRY_CODE
    for f in fetch_all("peaks", params, "peak records"):
        p = f["properties"]
        if num(p.get("value")) is not None and p.get("water_year"):
            peak_years[p["monitoring_location_id"]].add(int(p["water_year"]))

    print("3. Assembling", flush=True)
    out = []
    for sid, s in sites.items():
        rec = dict(s, d=None, c=None, p=None)
        if sid in daily_iv:
            m = merge_intervals(daily_iv[sid])
            days = sum((e - b).days + 1 for b, e in m)    # span; gaps between series removed,
            rec["d"] = [m[0][0].year, m[-1][1].year, days]  # gaps within a series are not
        if sid in cont_iv:
            m = merge_intervals(cont_iv[sid])
            rec["c"] = [m[0][0].year, m[-1][1].year]
        if sid in peak_years:
            ys = peak_years[sid]
            rec["p"] = [len(ys), min(ys), max(ys)]
        if rec["d"] or rec["c"] or rec["p"]:
            out.append(rec)
    out.sort(key=lambda r: (r["state"], r["id"]))
    empty_peaks = sorted(short_id(s) for s in peak_series_sites - set(peak_years))
    return out, empty_peaks


def summarize(sites, label):
    return {
        "scope": label,
        "sites": len(sites),
        "daily": sum(1 for r in sites if r["d"]),
        "continuous": sum(1 for r in sites if r["c"]),
        "peaks": sum(1 for r in sites if r["p"]),
        "peaks_only": sum(1 for r in sites if r["p"] and not r["d"] and not r["c"]),
        "no_drainage_area": sum(1 for r in sites if r["da"] is None),
    }


# Compact table format: one array per site, in this column order.
FIELDS = ["id", "name", "state", "county", "lat", "lon", "da", "cda",
          "d0", "d1", "dDays", "c0", "c1", "pN", "p0", "p1"]


def pack(sites):
    states = sorted({s["state"] for s in sites})
    idx = {st: i for i, st in enumerate(states)}
    rows = []
    for s in sites:
        d, c, p = s["d"] or [None] * 3, s["c"] or [None] * 2, s["p"] or [None] * 3
        rows.append([s["id"], s["name"], idx[s["state"]], s["county"], s["lat"], s["lon"],
                     s["da"], s["cda"], *d, *c, *p])
    return states, rows


def embed(snapshot):
    with open(HTML_PATH, encoding="utf-8") as fh:
        html = fh.read()
    i, j = html.find(START_MARK), html.find(END_MARK)
    if i < 0 or j < 0 or j < i:
        sys.exit(f"Could not find {START_MARK} ... {END_MARK} in {HTML_PATH}")
    payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # never close the <script> early
    new = html[: i + len(START_MARK)] + "\nconst SNAPSHOT = " + payload + ";\n" + html[j:]
    with open(HTML_PATH, "w", encoding="utf-8") as fh:
        fh.write(new)
    return len(new.encode("utf-8"))


def main():
    t0 = time.time()
    if not os.environ.get("USGS_API_KEY"):
        print("Note: USGS_API_KEY is not set. Without a key the API may rate-limit this build.\n")
    sites, empty_peaks = build()
    states, rows = pack(sites)
    stats = [summarize(sites, "All")] + [
        summarize([s for s in sites if s["state"] == st], st) for st in REPORT_STATES]
    snapshot = {
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "source": "USGS Water Data OGC API (api.waterdata.usgs.gov/ogcapi/v1)",
        "site_page": SITE_PAGE,
        "default_state": DEFAULT_STATE if DEFAULT_STATE in states else "",
        "states": states,
        "fields": FIELDS,
        "stats": stats,
        "sites": rows,
    }
    size = embed(snapshot)

    print("\n== Summary")
    for s in stats:
        print("  " + ", ".join(f"{k}: {v:,}" if isinstance(v, int) else f"{k}: {v}" for k, v in s.items()))
    print(f"  states/territories: {len(states)}")
    by_state = Counter(s["state"] for s in sites)
    print("  largest: " + ", ".join(f"{k} {v:,}" for k, v in by_state.most_common(5)))
    print(f"  peak series with no discharge values (treated as no peaks): {len(empty_peaks):,}")
    print(f"\nEmbedded {len(sites):,} sites in {HTML_PATH} "
          f"({size / 1e6:.1f} MB, snapshot {snapshot['snapshot_date']}, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
