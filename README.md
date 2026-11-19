# USGS stream gage finder

A one-page web app for students: filter USGS stream gages across the U.S. by state, drainage area,
data type, record length, and recency, then click through to each gage's USGS page.
It only helps you **find** sites. It does not plot or download data.

- `index.html`: the whole app. The site list (~40,000 sites) is embedded inside it, so the page needs
  no server and no data files. Open it from your disk, host it on GitHub Pages, or publish it anywhere.
- `build_snapshot.py`: refreshes the site list embedded in `index.html`. Run it about once a term.

The page opens filtered to Michigan. Students can pick another state or "All states". To change
the default, edit `DEFAULT_STATE` near the top of `build_snapshot.py` (use `""` for all states) and re-run it.

## Refreshing the snapshot

Requires Python 3.9+ and `requests` (`pip install requests`).

**1. Get a free API key** at <https://api.waterdata.usgs.gov/signup/> (it arrives by email).
Without a key, the API cuts you off after a few hundred requests per hour, and the build needs about 100
large ones. With a key the limit is 1,000 per hour.

**2. Run the script with the key in an environment variable** (don't paste it into the script):

```bash
USGS_API_KEY=your_key_here python3 build_snapshot.py
```

On Windows PowerShell: `$env:USGS_API_KEY="your_key_here"; python build_snapshot.py`

It takes about 5 minutes, mostly paging through ~830,000 annual-peak records. It prints a
summary (total sites, counts by data type, sites without a drainage area, plus a Michigan breakdown)
and rewrites the data section of `index.html` in place, including the snapshot date shown on the page.
If the API fails partway, nothing is written. Just re-run it.

### Where the data comes from

Everything comes from the USGS Water Data OGC API (`https://api.waterdata.usgs.gov/ogcapi/v1/`),
not the legacy `waterservices.usgs.gov` services, which shut down in February 2027.

| Field | Collection | Notes |
|---|---|---|
| Name, state, county, lat/lon, drainage areas | `combined-metadata` | `site_type_code=ST` (streams) |
| Daily discharge first/last year | `combined-metadata` | `parameter_code=00060`, `statistic_id=00003`, data type "Daily values" |
| Continuous discharge first/last year | `combined-metadata` | `00060`, `statistic_id=00011`, "Continuous values" |
| Annual peaks | `peaks` | `parameter_code=00060` rows with a numeric value (stage-only peaks excluded) |

A site is included if it has **any** of the three discharge record types. Discontinued sites are included.
Only U.S. sites (states, DC, and territories) are kept. USGS also stores some Canadian, Japanese (Okinawa), and
Pacific-island stations; set `COUNTRY_CODE = None` in the script to include them.

Things to know:
- **"Years of daily data" is approximate**: the time from the first to the last daily value.
  Gaps inside a record are not subtracted, so a gage with a 20-year gap in the middle looks longer than it is.
  (Exact counts would need one extra API request per site, about 27,000 requests.)
- **Continuous** records online usually start around 2007, even at gages operating since the 1900s.
- **Peaks** use water years (Oct 1 – Sep 30).
- Some sites list a peak series with no discharge values in it (e.g. stage only). They're treated as having no peaks.

## Hosting on GitHub Pages (free)

You need a free GitHub account. You don't need to install anything; it all happens in the browser.

1. Go to <https://github.com/new>. Name the repository (e.g. `gage-finder`), set it to **Public**,
   and click **Create repository**.
2. On the new repository's page, click **uploading an existing file**. Drag in `index.html` and
   `README.md` (and optionally `build_snapshot.py`), then click **Commit changes**.
   (GitHub's web upload accepts files up to 25 MB; `index.html` is well under that.)
3. Click **Settings** (top of the repo) → **Pages** (left sidebar). Under **Build and deployment → Source**,
   choose **Deploy from a branch**, then pick branch **main**, folder **/ (root)**, and click **Save**.
4. Wait a minute or two and refresh the Pages settings screen. It shows your link, which looks like
   `https://<your-username>.github.io/gage-finder/`. Share that with students.

To update it each term: run `build_snapshot.py`, then in the repo choose **Add file → Upload files**
and upload the new `index.html` (it replaces the old one). The site updates within a few minutes.

## Notes

- The map uses Leaflet from unpkg.com and OpenStreetMap tiles. If either is blocked or you're offline,
  the map hides itself and the table still works.
- The table shows 100 rows per page; the CSV download includes every site that matches the filters.
- Each Site ID links to `https://waterdata.usgs.gov/monitoring-location/USGS-<site_no>/`.
- In the CSV export, site IDs are text like `04141000`. Excel drops the leading zero if you
  double-click the file open; use Data → From Text/CSV and set the column to Text, or read it in
  pandas with `dtype={"site_id": str}`.
