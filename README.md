# mrs-filler

Seed data generator for the [Mixed Reality Service](https://github.com/mpesce/mrs-server) (MRS). Produces a JSON file containing spatial registrations for the 1000 most populous metropolitan areas on Earth, ready to import into an MRS server.

## What it does

1. Queries [Wikidata](https://www.wikidata.org/) for cities and metropolitan areas with populations above 150,000
2. Deduplicates results by entity and geographic proximity (50 km threshold)
3. Selects the top 1,000 by population
4. Estimates each metro's spatial radius from land area (or from population when area data is unavailable)
5. Outputs a JSON file conforming to the [MRS export format](https://github.com/mpesce/mrs-server/blob/main/docs/EXPORT_FORMAT.md)

Each entry's `service_point` links to the city's English Wikipedia article.

## Requirements

- Python 3.7+
- No external dependencies (stdlib only)
- Internet connection (to query Wikidata)

## Usage

```bash
python generate_cities.py
```

This produces `mrs-entries.json` in the current directory.

To specify a different output file:

```bash
python generate_cities.py -o my-seed-data.json
```

### Example output

```
Querying Wikidata for city data...
  Received 3000 raw results from Wikidata
Deduplicating...
  1329 unique cities after deduplication
  Using top 1000 cities by population
Building registrations...
Validating...
  Validation passed

Wrote 1000 entries to mrs-entries.json
  Population range: 349,842 – 32,054,159
  Top 5: Chongqing, Delhi, Shanghai, Greater Mexico City, Beijing
```

## Output format

The generated JSON follows the MRS export schema:

```json
{
  "mrs_version": "1.0",
  "exported_at": "2026-03-08T...",
  "server": "https://owen.iz.net",
  "registrations": [
    {
      "id": "reg_abc123def456",
      "owner": "mpesce@owen.iz.net",
      "space": {
        "type": "sphere",
        "center": { "lat": 35.689444, "lon": 139.691667, "ele": 0.0 },
        "radius": 26413.5
      },
      "service_point": "https://en.wikipedia.org/wiki/Tokyo",
      "foad": false,
      "origin_server": "https://owen.iz.net",
      "version": 1,
      "created": "2026-03-08T...",
      "updated": "2026-03-08T..."
    }
  ],
  "tombstones": [],
  "peers": []
}
```

## How radius is estimated

- If Wikidata provides the metro's land area in km², the radius is calculated as `sqrt(area / pi)`, converted to metres.
- Otherwise, the area is estimated from population assuming an average metro density of ~2,000 people per km².
- Radii are clamped to the MRS schema limits (100 m to 1,000,000 m).

## Data source

All city data comes from [Wikidata](https://www.wikidata.org/) via its public SPARQL endpoint. The query searches for entities classified as cities (Q515), metropolitan areas (Q1549591), megacities (Q174844), metropolises (Q200250), and cities with millions of inhabitants (Q1637706).

## License

MIT
