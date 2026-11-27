#!/usr/bin/env python3
"""
Update the LicensedTO directory (directory_data.js in this repository) from the City of Toronto's
open data: "Municipal Licensing and Standards - Business Licences and Permits".

Runs automatically on GitHub every weekday at 10 pm Toronto time (.github/workflows/update-data.yml), which
commits the result so GitHub Pages republishes the site. Phone numbers are never stored: some sole
proprietors list a personal number, and the app is public.
https://open.toronto.ca/dataset/municipal-licensing-and-standards-business-licences-and-permits/

The City publishes one full copy of every licence each day (no "changes only" file), so this script:
  1. downloads today's full CSV,
  2. compares it with the licences already in directory_data.js (the last update),
  3. applies only what changed since then:
       - licences cancelled since the last update move into that business's history,
         and a business with no licence left is taken off the directory;
       - licences that are new since the last update are added, to the same business when the name and
         address match one already listed, or as a new business;
       - every current licence gets its issue date, status and "valid until" refreshed;
  4. writes directory_data.js with the new date and a short summary of the changes (shown on the page),
     and points index.html at the new file so browsers don't keep the old one.

Existing listings (names, map positions, history) are kept as they are; only new businesses are formatted
and placed on the map here.

Usage (from the repository root):
  python scripts/update-licensedto.py            # download and update
  python scripts/update-licensedto.py --dry-run  # show what would change, write nothing
  python scripts/update-licensedto.py --csv path/to/business-licences-data.csv   # use a file already downloaded
  python scripts/update-licensedto.py --no-geocode   # don't look up map positions for new addresses
"""
import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # the repository root, served as the site
DATA = ROOT / "directory_data.js"
PAGE = ROOT / "index.html"

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show"
PACKAGE = "municipal-licensing-and-standards-business-licences-and-permits"
GEOCODER = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "licence-directory-updater/1.0"
MAX_GEOCODE = 150           # Nominatim allows about one request a second; keep each run short
RENEW_WINDOW_DAYS = 75      # a renewal recorded this close before the anniversary pays for the next year

AREA_ONLY_CONDITIONS = ("MAILING ADDRESS ONLY", "OFFICE USE ONLY", "ONLINE CLASSES ONLY")


# ---------------------------------------------------------------------------------------------- download
def http_get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def city_resource():
    """The daily-refreshed CSV in the dataset, and the date of the City's copy."""
    meta = json.loads(http_get(CKAN + "?id=" + PACKAGE))["result"]
    csvs = [r for r in meta["resources"] if r.get("format", "").upper() == "CSV" and r.get("url", "").endswith(".csv")]
    if not csvs:
        sys.exit("No CSV file found in the City dataset.")
    res = max(csvs, key=lambda r: r.get("last_modified") or "")
    return res, (res.get("last_modified") or dt.date.today().isoformat())[:10]


def download_csv(res):
    print(f"Downloading {res['name']} ...")
    return http_get(res["url"], timeout=600).decode("utf-8-sig")


# ---------------------------------------------------------------------------------------------- data file
def load_data():
    s = DATA.read_text(encoding="utf-8")
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def save_data(D):
    body = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
    DATA.write_text("window.LTO_DATA=" + body + ";", encoding="utf-8")


def bump_page_version(snapshot):
    s = PAGE.read_text(encoding="utf-8")
    s2 = re.sub(r'directory_data\.js\?v=[\w-]+', f"directory_data.js?v={snapshot}", s)
    if s2 != s:
        PAGE.write_text(s2, encoding="utf-8", newline="")


# ---------------------------------------------------------------------------------------------- formatting
def title(s):
    """'CHEF'S HALL INC' -> "Chef's Hall Inc": capital after a space, hyphen, slash, bracket or '&' only."""
    out, up = [], True
    for ch in s.lower():
        out.append(ch.upper() if up else ch)
        up = ch in " -/(&"
    return re.sub(r"\s+", " ", "".join(out)).strip()


def company(s):
    t = title(s)
    return re.sub(r"\b(Ltd|Inc|Corp)$", r"\1.", t)


def street(line1):
    s = re.sub(r"#\s*(\w+)", r"Unit \1", title(line1))
    return re.sub(r"\b(\d+)([a-z])\b", lambda m: m.group(1) + m.group(2).upper(), s)   # '44a' -> '44A'


def city(line2):
    c = line2.split(",")[0].strip()
    return "" if c.upper() == "TORONTO" else title(c)


def valid_until(issued, last_update):
    """Licences run a year from the issue date and renew on its anniversary."""
    lu = max(issued, last_update)
    if lu == issued:
        return add_years(issued, 1)
    y = lu.year
    a = add_years(issued, y - issued.year)
    while a <= lu:
        y += 1
        a = add_years(issued, y - issued.year)
    if (a - lu).days <= RENEW_WINDOW_DAYS:
        a = add_years(a, 1)
    return a


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:          # 29 Feb
        return d.replace(year=d.year + n, day=28)


def status(issued, until, today):
    """0 = first year, 1 = renewed, 2 = renewal not recorded (the year has run out)."""
    if until <= today:
        return 2
    if until == add_years(issued, 1):
        return 0
    return 1


def D_(s):
    return dt.date.fromisoformat(s)


# ---------------------------------------------------------------------------------------------- geocoding
def geocode(addr, postal):
    q = urllib.parse.urlencode({"q": f"{addr}, Toronto, ON {postal}, Canada", "format": "json", "limit": 1,
                                "countrycodes": "ca"})
    try:
        hits = json.loads(http_get(GEOCODER + "?" + q, timeout=30))
    except Exception as e:      # network trouble: leave the business off the map rather than fail the update
        print("   geocoder error:", e)
        return None
    finally:
        time.sleep(1.1)
    if not hits:
        return None
    lat, lon = round(float(hits[0]["lat"]), 5), round(float(hits[0]["lon"]), 5)
    if not (43.5 < lat < 43.9 and -79.7 < lon < -79.1):     # outside Toronto: a wrong match
        return None
    return lat, lon


# ---------------------------------------------------------------------------------------------- update
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="use this CSV instead of downloading")
    ap.add_argument("--date", help="date of the City's copy (YYYY-MM-DD); default: today, or the City's date")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-geocode", action="store_true")
    args = ap.parse_args()

    D = load_data()
    old_snap = D["snapshot"]
    if args.csv:
        city_date = args.date or dt.date.today().isoformat()
    else:
        res, city_date = city_resource()
        city_date = args.date or city_date
        print(f"City copy of {city_date}; directory last updated {old_snap}.")
    if city_date <= old_snap:
        print("The directory is already up to date. Nothing to do.")
        return
    text = Path(args.csv).read_text(encoding="utf-8-sig") if args.csv else download_csv(res)
    rows = list(csv.DictReader(io.StringIO(text)))
    C = {r["Licence No."]: r for r in rows}
    print(f"{len(rows):,} licences in the City file.")
    today = D_(city_date)

    # What the existing data teaches us: City category -> directory category, and how endorsements are shown.
    cat_of = {}
    shown = {}                   # 'D.S.O. OFFICE - NO VEHICLE' -> 'D.S.O. Office - No Vehicle'
    for r in D["rows"]:
        for p in filter(None, r[3].split("; ")):
            shown[p.upper()] = p
        for l in r[13]:
            c = C.get(l[0])
            if c:
                cat_of.setdefault(c["Category"], l[1])
    for i, (name, _g) in enumerate(D["cats"]):   # categories listed but with no licence at the last update
        cat_of.setdefault(name.upper(), i)
    hidden = set()               # endorsements that only repeat the category, e.g. 'EATING/DRINKING'
    for r in D["rows"]:
        for l in r[13]:
            c = C.get(l[0])
            if c:
                for e in filter(None, (x.strip() for x in c["Endorsements"].split(";"))):
                    if e.upper() not in shown:
                        hidden.add(e.upper())

    known = {l[0] for r in D["rows"] for l in r[13]} | {h[0] for r in D["rows"] for h in r[17]}
    by_key = {}                  # (NAME, LINE 1) as the City writes them -> row
    for r in D["rows"]:
        for l in r[13]:
            c = C.get(l[0])
            if c:
                by_key[(c["Operating Name"].strip().upper(), c["Licence Address Line 1"].strip().upper())] = r
    coords_at = {}               # (street, postal) -> (lat, lon, quality), to reuse for neighbours
    for r in D["rows"]:
        if r[14] is not None:
            coords_at[(r[4].upper(), r[6])] = (r[14], r[15], r[16])

    stats = Counter()
    closed_rows = []

    # 1. current licences: cancelled ones go to history, the rest get fresh dates and status
    for r in D["rows"]:
        keep = []
        for l in r[13]:
            c = C.get(l[0])
            gone = c is None
            cancel = (c or {}).get("Cancel Date", "").strip()
            if gone or cancel:
                r[17].append([l[0], l[1], l[2], cancel or city_date])
                stats["cancelled licences"] += 1
                continue
            iss = D_(c["Issued"])
            until = valid_until(iss, D_(c["Last Record Update"]))
            st = status(iss, until, today)
            if until.isoformat() != l[4] and until > D_(l[4]):
                stats["renewed licences"] += 1
            keep.append([l[0], l[1], c["Issued"], st, until.isoformat()])
        r[13] = keep
        if not keep:
            closed_rows.append(r)
    closed_ids = {id(r) for r in closed_rows}

    # 2. licences that are new since the last update
    new_rows = []
    to_geocode = []
    for no, c in C.items():
        if no in known or c["Cancel Date"].strip():
            continue
        cat = cat_of.get(c["Category"])
        name_raw, line1 = c["Operating Name"].strip(), c["Licence Address Line 1"].strip()
        if cat is None or not name_raw or not line1 or c["Issued"] > city_date:
            continue
        iss = D_(c["Issued"])
        until = valid_until(iss, D_(c["Last Record Update"]))
        lic = [no, cat, c["Issued"], status(iss, until, today), until.isoformat()]
        stats["new licences"] += 1
        key = (name_raw.upper(), line1.upper())
        row = by_key.get(key)
        if row is not None:
            row[13].append(lic)
            closed_ids.discard(id(row))      # same business, new licence number
            continue
        name = company(name_raw)
        owner = "" if "," in c["Client Name"] else company(c["Client Name"])
        if owner.upper().rstrip(".") == name.upper().rstrip("."):
            owner = ""
        ends = [e.strip() for e in c["Endorsements"].split(";") if e.strip()]
        detail = "; ".join(dict.fromkeys(shown.get(e.upper(), title(e)) for e in ends if e.upper() not in hidden))
        postal = c["Licence Address Line 3"].strip().upper()
        ward = int(c["Ward"]) if c["Ward"].strip().isdigit() else 0
        area = c["Category"].startswith("DRIVING SCHOOL") and any(x in c["Conditions"].upper() for x in AREA_ONLY_CONDITIONS)
        st_, ct = ("" if area else street(line1)), city(c["Licence Address Line 2"])
        if area:
            postal = postal[:3]
        # fields 8 and 9 (phone, extension) stay empty on purpose: no phone numbers in the public app
        row = [name, owner, [cat], detail, st_, ct, postal, ward, "", "",
               c["Issued"], c["Issued"], 1, [lic], None, None, 0, [], 1 if area else 0]
        if not area and not ct:             # in Toronto: put it on the map
            hit = coords_at.get((st_.upper(), postal))
            if hit:
                row[14], row[15], row[16] = hit
            elif postal in D["postal"]:
                row[14], row[15] = D["postal"][postal]
                row[16] = 2
            else:
                to_geocode.append(row)
        by_key[key] = row
        new_rows.append(row)
    stats["new businesses"] = len(new_rows)
    stats["businesses closed"] = len(closed_ids)

    if to_geocode and not args.no_geocode and not args.dry_run:
        todo = to_geocode[:MAX_GEOCODE]
        print(f"Looking up map positions for {len(todo)} new addresses (about {len(todo) * 1.1 / 60:.0f} min) ...")
        for row in todo:
            hit = geocode(row[4].split(",")[0], row[6])
            if hit:
                row[14], row[15], row[16] = hit[0], hit[1], 1
                coords_at[(row[4].upper(), row[6])] = (hit[0], hit[1], 1)
                if row[6] and row[6] not in D["postal"]:
                    D["postal"][row[6]] = [hit[0], hit[1]]
    stats["new businesses without a map position"] = sum(1 for r in new_rows if r[14] is None and not r[5] and not r[18])

    # 3. tidy every changed row: counts, first/latest dates, categories
    rows_out = [r for r in D["rows"] if id(r) not in closed_ids] + new_rows
    for r in rows_out:
        r[13].sort(key=lambda l: l[2])
        r[12] = len(r[13])
        r[10], r[11] = r[13][0][2], r[13][-1][2]
        r[2] = list(dict.fromkeys(l[1] for l in r[13]))
    rows_out.sort(key=lambda r: r[10], reverse=True)
    for r in rows_out:                       # safety net: never publish a phone number
        r[8], r[9] = "", ""

    D["rows"] = rows_out
    D["snapshot"] = city_date
    D["update"] = {"from": old_snap, "to": city_date,
                   "new_businesses": stats["new businesses"], "closed_businesses": stats["businesses closed"],
                   "new_licences": stats["new licences"], "cancelled_licences": stats["cancelled licences"]}

    print(f"\nChanges from {old_snap} to {city_date}:")
    for k in ("new businesses", "new licences", "renewed licences", "cancelled licences", "businesses closed",
              "new businesses without a map position"):
        print(f"  {k:<40} {stats[k]:>6,}")
    print(f"  {'businesses listed now':<40} {len(rows_out):>6,}")
    for r in new_rows[:12]:
        print("   +", r[0], "-", r[4] or r[6], r[5])
    if args.dry_run:
        print("\nDry run: nothing written.")
        return
    save_data(D)
    bump_page_version(city_date)
    print(f"\nWrote {DATA.relative_to(ROOT)} and pointed index.html at ?v={city_date}.")


if __name__ == "__main__":
    main()
