#!/usr/bin/env python3
"""Generate MRS seed data for the 1000 most populous metropolitan areas.

Fetches city data from Wikidata (coordinates, populations, areas, Wikipedia URLs),
deduplicates by proximity, estimates metropolitan radii, and outputs a JSON file
conforming to the MRS export format.

Usage:
    python generate_cities.py [-o OUTPUT_FILE]
"""

import argparse
import json
import math
import secrets
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# Wikidata entity types to query:
#   Q515 = city, Q1549591 = metropolitan area, Q1637706 = city with millions,
#   Q200250 = metropolis, Q174844 = megacity
SPARQL_QUERY = """
SELECT DISTINCT ?city ?cityLabel ?population ?lat ?lon ?area ?article WHERE {
  VALUES ?type { wd:Q515 wd:Q1549591 wd:Q1637706 wd:Q200250 wd:Q174844 }
  ?city wdt:P31 ?type .
  ?city wdt:P1082 ?population .
  ?city wdt:P625 ?coord .
  BIND(geof:latitude(?coord) AS ?lat)
  BIND(geof:longitude(?coord) AS ?lon)
  FILTER(?population > 150000)
  OPTIONAL { ?city wdt:P2046 ?area . }
  OPTIONAL {
    ?article schema:about ?city ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}
ORDER BY DESC(?population)
LIMIT 3000
"""

USER_AGENT = "MRS-Seed-Generator/1.0 (contact: mpesce@owen.iz.net)"


def generate_id():
    """Generate a registration ID: reg_ + 12 URL-safe characters."""
    return "reg_" + secrets.token_urlsafe(9)[:12]


def fetch_url(url, retries=3, delay=2):
    """Fetch a URL with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            if attempt < retries - 1:
                wait = delay * (attempt + 1)
                print(f"  Retry {attempt + 1}/{retries} after error: {e} (waiting {wait}s)", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def fetch_cities_from_wikidata():
    """Query Wikidata SPARQL endpoint for cities with populations and coordinates."""
    print("Querying Wikidata for city data...", file=sys.stderr)
    params = urllib.parse.urlencode({"format": "json", "query": SPARQL_QUERY})
    url = f"{WIKIDATA_SPARQL_URL}?{params}"
    raw = fetch_url(url)
    data = json.loads(raw)

    cities = []
    for binding in data["results"]["bindings"]:
        city_uri = binding["city"]["value"]
        name = binding["cityLabel"]["value"]
        population = int(float(binding["population"]["value"]))
        lat = float(binding["lat"]["value"])
        lon = float(binding["lon"]["value"])
        area_km2 = None
        if "area" in binding:
            area_km2 = float(binding["area"]["value"])
        wiki_url = binding.get("article", {}).get("value")

        cities.append({
            "uri": city_uri,
            "name": name,
            "population": population,
            "lat": lat,
            "lon": lon,
            "area_km2": area_km2,
            "wiki_url": wiki_url,
        })

    print(f"  Received {len(cities)} raw results from Wikidata", file=sys.stderr)
    return cities


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def deduplicate_cities(cities, proximity_km=50):
    """Remove duplicate/overlapping entries, keeping the one with highest population.

    Wikidata may return the same city as both a 'city' and a 'metropolitan area',
    or nearby cities that represent the same metro. We keep the highest-population
    entry for each cluster within proximity_km.
    """
    # Sort by population descending so we keep the biggest first
    cities.sort(key=lambda c: c["population"], reverse=True)

    # Also deduplicate by Wikidata URI (same entity, different population values)
    seen_uris = {}
    uri_deduped = []
    for c in cities:
        uri = c["uri"]
        if uri not in seen_uris:
            seen_uris[uri] = True
            uri_deduped.append(c)

    # Spatial deduplication
    kept = []
    for city in uri_deduped:
        too_close = False
        for existing in kept:
            dist = haversine_km(city["lat"], city["lon"], existing["lat"], existing["lon"])
            if dist < proximity_km:
                too_close = True
                break
        if not too_close:
            kept.append(city)

    return kept


def estimate_radius_meters(population, area_km2=None):
    """Estimate metropolitan area radius in meters from area or population."""
    if area_km2 and 0 < area_km2 < 1_000_000:
        return math.sqrt(area_km2 / math.pi) * 1000
    if population:
        # Rough estimate: ~2000 people per km² average metro density
        estimated_area = population / 2000
        return math.sqrt(estimated_area / math.pi) * 1000
    return 10000  # Default 10 km


def build_registrations(cities):
    """Build MRS registration objects from city data."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registrations = []

    for city in cities:
        wiki_url = city.get("wiki_url")
        if not wiki_url:
            # Construct a best-guess Wikipedia URL from the city name
            slug = urllib.parse.quote(city["name"].replace(" ", "_"))
            wiki_url = f"https://en.wikipedia.org/wiki/{slug}"

        radius = estimate_radius_meters(city["population"], city.get("area_km2"))
        # Clamp radius: minimum 100m, maximum 100km
        radius = max(100, min(radius, 100_000))

        reg_id = generate_id()
        registrations.append({
            "id": reg_id,
            "owner": "mpesce@owen.iz.net",
            "space": {
                "type": "sphere",
                "center": {
                    "lat": round(city["lat"], 6),
                    "lon": round(city["lon"], 6),
                    "ele": 0.0,
                },
                "radius": round(radius, 1),
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


def validate_output(data):
    """Basic validation of the output structure."""
    errors = []

    if "mrs_version" not in data:
        errors.append("Missing mrs_version")
    if "registrations" not in data or not isinstance(data["registrations"], list):
        errors.append("Missing or invalid registrations array")
    if "tombstones" not in data:
        errors.append("Missing tombstones array")
    if "peers" not in data:
        errors.append("Missing peers array")

    for i, reg in enumerate(data.get("registrations", [])):
        prefix = f"registrations[{i}]"
        if not reg.get("id", "").startswith("reg_"):
            errors.append(f"{prefix}: id must start with 'reg_'")
        if not reg.get("owner"):
            errors.append(f"{prefix}: missing owner")
        space = reg.get("space", {})
        if space.get("type") != "sphere":
            errors.append(f"{prefix}: space.type must be 'sphere'")
        center = space.get("center", {})
        if not (-90 <= center.get("lat", 999) <= 90):
            errors.append(f"{prefix}: lat out of range")
        if not (-180 <= center.get("lon", 999) <= 180):
            errors.append(f"{prefix}: lon out of range")
        if not (0 < space.get("radius", 0) <= 1_000_000):
            errors.append(f"{prefix}: radius out of range")
        if not reg.get("foad") and not reg.get("service_point"):
            errors.append(f"{prefix}: service_point required when foad is false")

    return errors


def main():
    parser = argparse.ArgumentParser(
        description="Generate MRS seed data for the 1000 most populous metropolitan areas."
    )
    parser.add_argument(
        "-o", "--output",
        default="mrs-entries.json",
        help="Output file name (default: mrs-entries.json)",
    )
    args = parser.parse_args()

    # Step 1: Fetch city data from Wikidata
    raw_cities = fetch_cities_from_wikidata()

    # Step 2: Deduplicate
    print("Deduplicating...", file=sys.stderr)
    cities = deduplicate_cities(raw_cities)
    print(f"  {len(cities)} unique cities after deduplication", file=sys.stderr)

    if len(cities) < 1000:
        print(f"  Warning: Only {len(cities)} cities available (target: 1000)", file=sys.stderr)

    # Take top 1000
    cities = cities[:1000]
    print(f"  Using top {len(cities)} cities by population", file=sys.stderr)

    # Step 3: Build registrations
    print("Building registrations...", file=sys.stderr)
    registrations = build_registrations(cities)

    # Step 4: Assemble output
    output = {
        "mrs_version": "1.0",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server": "https://owen.iz.net",
        "registrations": registrations,
        "tombstones": [],
        "peers": [],
    }

    # Step 5: Validate
    print("Validating...", file=sys.stderr)
    errors = validate_output(output)
    if errors:
        print(f"  Validation errors:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("  Validation passed", file=sys.stderr)

    # Step 6: Write
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {len(registrations)} entries to {args.output}", file=sys.stderr)

    # Summary stats
    pops = [c["population"] for c in cities]
    print(f"  Population range: {min(pops):,} – {max(pops):,}", file=sys.stderr)
    print(f"  Top 5: {', '.join(c['name'] for c in cities[:5])}", file=sys.stderr)


if __name__ == "__main__":
    main()
