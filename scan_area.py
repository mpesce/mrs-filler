#!/usr/bin/env python3
"""Scan a geographic area and collect all geolocated Wikipedia entries.

Performs a grid sweep across a square centred on the given coordinates,
querying Wikipedia's geosearch API at each grid point to discover every
geolocated article. Results are deduplicated and written as MRS-format
JSON ready for import into an MRS server.

Usage:
    python scan_area.py --lat LAT --lon LON --radius RADIUS_KM [-o OUTPUT]

The output filename defaults to mrs-entries_LAT_LON_RADIUSm.json
"""

import argparse
import base64
import hashlib
import json
import math
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

USER_AGENT = "MRS-Area-Scanner/1.0 (contact: mpesce@owen.iz.net)"

# Wikipedia geosearch API limits
GS_MAX_RADIUS = 10000  # metres
GS_MAX_RESULTS = 500

# Each Wikipedia POI gets this radius in the MRS registration (metres)
POI_RADIUS = 100.0


def deterministic_id(source_key):
    """Generate a deterministic registration ID from a source key.

    Uses SHA-256 hash of the key, base64url-encoded, so the same
    Wikipedia article always produces the same reg ID across runs.
    """
    h = hashlib.sha256(source_key.encode()).digest()
    b64 = base64.urlsafe_b64encode(h).decode("ascii")
    return "reg_" + b64[:12]


def fetch_url(url, retries=3, delay=2):
    """Fetch a URL with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            if attempt < retries - 1:
                wait = delay * (attempt + 1)
                print(f"  Retry {attempt + 1}/{retries}: {e} (waiting {wait}s)", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def geosearch(lat, lon, radius_m=GS_MAX_RADIUS):
    """Query Wikipedia geosearch API for articles near a point.

    Returns a list of dicts with keys: pageid, title, lat, lon, dist.
    """
    radius_m = min(radius_m, GS_MAX_RADIUS)
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": int(radius_m),
        "gslimit": GS_MAX_RESULTS,
        "format": "json",
        "formatversion": "2",
    })
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    raw = fetch_url(url)
    data = json.loads(raw)
    results = data.get("query", {}).get("geosearch", [])
    return results


# Minimum search radius before we stop subdividing (metres).
# Wikipedia's API requires gsradius >= 10, but below ~200m further
# subdivision is unlikely to help.
MIN_SUBDIV_RADIUS = 200


def geosearch_adaptive(lat, lon, radius_m, seen_pageids, depth=0):
    """Query Wikipedia geosearch, automatically subdividing on overflow.

    When a query returns the maximum 500 results, the area is split into
    four quadrants and each is re-queried at half the radius. This recurses
    until queries no longer overflow or the radius drops below MIN_SUBDIV_RADIUS.

    Returns a list of result dicts, deduplicating against seen_pageids.
    seen_pageids is updated in place.
    """
    radius_m = min(radius_m, GS_MAX_RADIUS)
    indent = "    " * depth

    time.sleep(0.3)  # rate-limit
    results = geosearch(lat, lon, radius_m)

    if len(results) < GS_MAX_RESULTS or radius_m <= MIN_SUBDIV_RADIUS:
        # Didn't overflow, or can't subdivide further — return what we have
        new_results = []
        for r in results:
            if r["pageid"] not in seen_pageids:
                seen_pageids.add(r["pageid"])
                new_results.append(r)
        return new_results

    # Hit the 500-result limit — keep these results, then subdivide for more.
    # The original query captured entries near the centre; the sub-queries
    # will find entries further out that were beyond the 500-result cutoff.
    all_new = []
    for r in results:
        if r["pageid"] not in seen_pageids:
            seen_pageids.add(r["pageid"])
            all_new.append(r)

    sub_radius = radius_m / 2
    offset_m = radius_m / 2
    offset_lat = offset_m / metres_per_degree_lat()
    m_per_deg_lon = metres_per_degree_lon(lat)
    offset_lon = offset_m / m_per_deg_lon if m_per_deg_lon > 0 else offset_m

    print(f"{indent}    ↳ overflow at {radius_m/1000:.1f}km, subdividing into 4 × {sub_radius/1000:.1f}km",
          file=sys.stderr)

    for dlat_sign in (-1, 1):
        for dlon_sign in (-1, 1):
            sub_lat = lat + dlat_sign * offset_lat
            sub_lon = lon + dlon_sign * offset_lon
            sub_results = geosearch_adaptive(sub_lat, sub_lon, sub_radius, seen_pageids, depth + 1)
            all_new.extend(sub_results)

    return all_new


def metres_per_degree_lat():
    """Approximate metres per degree of latitude."""
    return 111_320.0


def metres_per_degree_lon(lat):
    """Approximate metres per degree of longitude at a given latitude."""
    return 111_320.0 * math.cos(math.radians(lat))


def generate_grid(center_lat, center_lon, radius_m):
    """Generate grid points and per-query search radius for scanning a square area.

    For small areas (radius <= 10km), returns a single centre point with
    the exact radius. For larger areas, tiles the square with overlapping
    10km-radius search circles using a step of 12km (ensuring corners of
    each grid cell are within the search radius).

    Returns (points, query_radius_m).
    """
    if radius_m <= GS_MAX_RADIUS:
        # Single query covers the whole area
        return [(center_lat, center_lon)], int(radius_m)

    # Multiple queries needed — tile with 10km search circles
    query_radius = GS_MAX_RADIUS
    # Step so that the corner of each cell is within the search radius:
    # corner distance = step * sqrt(2)/2, must be <= query_radius
    # => step <= query_radius * sqrt(2) ≈ 14.1km; use 12km for safety
    step_m = query_radius * 1.2

    step_lat = step_m / metres_per_degree_lat()
    m_per_deg_lon = metres_per_degree_lon(center_lat)
    step_lon = step_m / m_per_deg_lon if m_per_deg_lon > 0 else step_m

    n_lat = math.ceil(radius_m / step_m)
    n_lon = math.ceil(radius_m / step_m)

    points = []
    for i in range(-n_lat, n_lat + 1):
        for j in range(-n_lon, n_lon + 1):
            lat = center_lat + i * step_lat
            lon = center_lon + j * step_lon
            lat = max(-90.0, min(90.0, lat))
            lon = max(-180.0, min(180.0, lon))
            points.append((lat, lon))

    return points, query_radius


def scan_area(center_lat, center_lon, radius_m):
    """Scan a square area and collect all unique Wikipedia geolocated entries."""
    grid, query_radius = generate_grid(center_lat, center_lon, radius_m)
    total_points = len(grid)

    if total_points == 1:
        print(f"Single query, {radius_m/1000:.1f}km radius...", file=sys.stderr)
    else:
        print(f"Scanning {total_points} grid points over {radius_m/1000:.1f}km radius "
              f"(each query: {query_radius/1000:.0f}km)...", file=sys.stderr)

    seen_pageids = set()
    all_entries = []

    for idx, (lat, lon) in enumerate(grid, 1):
        print(f"  Grid point {idx}/{total_points}: ({lat:.4f}, {lon:.4f})", file=sys.stderr)

        try:
            new_results = geosearch_adaptive(lat, lon, query_radius, seen_pageids)
            all_entries.extend(new_results)
            print(f"    {len(new_results)} new entries (total: {len(all_entries)})", file=sys.stderr)
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue

    return all_entries


def build_registrations(entries):
    """Build MRS registration objects from Wikipedia geosearch results."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registrations = []

    for entry in entries:
        title = entry["title"]
        lat = entry["lat"]
        lon = entry["lon"]
        wiki_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"

        reg_id = deterministic_id(f"wpid:{entry['pageid']}")
        registrations.append({
            "id": reg_id,
            "owner": "mpesce@owen.iz.net",
            "space": {
                "type": "sphere",
                "center": {
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "ele": 0.0,
                },
                "radius": POI_RADIUS,
            },
            "service_point": wiki_url,
            "foad": False,
            "origin_server": "https://owen.iz.net",
            "origin_id": reg_id,
            "version": 1,
            "created": now,
            "updated": now,
        })

    return registrations


def default_filename(lat, lon, radius_m):
    """Generate default output filename from parameters."""
    return f"mrs-entries_{lat:.4f}_{lon:.4f}_{int(radius_m)}m.json"


def main():
    parser = argparse.ArgumentParser(
        description="Scan a geographic area and collect all geolocated Wikipedia entries as MRS registrations."
    )
    parser.add_argument("--lat", type=float, required=True, help="Centre latitude")
    parser.add_argument("--lon", type=float, required=True, help="Centre longitude")
    parser.add_argument("--radius", type=float, required=True, help="Scan radius in kilometres")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output filename (auto-generated if omitted)")
    args = parser.parse_args()

    radius_m = args.radius * 1000  # Convert km to metres

    if not (-90 <= args.lat <= 90):
        print("Error: latitude must be between -90 and 90", file=sys.stderr)
        sys.exit(1)
    if not (-180 <= args.lon <= 180):
        print("Error: longitude must be between -180 and 180", file=sys.stderr)
        sys.exit(1)
    if radius_m <= 0:
        print("Error: radius must be positive", file=sys.stderr)
        sys.exit(1)

    output_file = args.output or default_filename(args.lat, args.lon, radius_m)

    # Step 1: Scan the area
    entries = scan_area(args.lat, args.lon, radius_m)
    print(f"\nFound {len(entries)} unique Wikipedia entries", file=sys.stderr)

    if not entries:
        print("No entries found. Nothing to write.", file=sys.stderr)
        sys.exit(0)

    # Step 2: Build registrations
    print("Building registrations...", file=sys.stderr)
    registrations = build_registrations(entries)

    # Step 3: Assemble output
    output = {
        "mrs_version": "1.0",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server": "https://owen.iz.net",
        "registrations": registrations,
        "tombstones": [],
        "peers": [],
    }

    # Step 4: Write
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(registrations)} entries to {output_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
