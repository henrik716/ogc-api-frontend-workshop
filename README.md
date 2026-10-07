# OGC API workshop

WS - Dataprodukter

Publish a GeoPackage as your own **OGC API – Features** service, using Kartverket's [OGC API starter kit](https://github.com/kartverket/ogc-api-starter): a pygeoapi backend and Kartverket's Next.js frontend.

Everything runs in a GitHub Codespace, so there's nothing to install.

## Get started

1. Click **Code → Codespaces → Create codespace on main** on this repo's GitHub page.
2. Wait for the Codespace to finish setting up (a couple of minutes). `workshop.ipynb` opens automatically.
3. Run the notebook cells from top to bottom.

## Using your own data

Drag a GeoPackage (`.gpkg`) into the `data/` folder, set `GPKG` in step 2 of the notebook, and fill in the metadata in steps 3–4. Every layer in the GeoPackage becomes a collection in the API.

Layers need a CRS with an EPSG code. Any EPSG code works, and the API reprojects on the fly.

## GeoPackage or PostGIS

Step 2 of the notebook has a `SOURCE` setting:

- `"gpkg"` (default): the API reads the GeoPackage directly. Simplest, with no database.
- `"postgis"`: the notebook starts a PostGIS database in the Codespace, loads the GeoPackage into it with `ogr2ogr`, and the API reads from there. This is the [recommended setup](https://kartverket.github.io/ogcapi-docs/docs/ogcapi-skip) for real services at Kartverket, and the config then matches the starter kit's. It also adds vector tiles (OGC API – Tiles) and turns on the starter kit's processes, so the frontend offers GeoPackage/CSV downloads.

No database of your own is needed for either option.

## What's in here

The layout follows [ogc-api-starter](https://github.com/kartverket/ogc-api-starter), plus the notebook and the data:

| File | |
|---|---|
| `workshop.ipynb` | The workshop: run it top to bottom |
| `config/pygeoapi-config.yml` | The API configuration. The notebook writes it; both images are built with it |
| `backend/Dockerfile` | pygeoapi: Kartverket's image + your config + your data |
| `frontend/Dockerfile` | The frontend: Kartverket's image + your config |
| `docker-compose.yml` | Runs the frontend (port 3000) and backend (port 5001), plus PostGIS when `SOURCE = "postgis"` |
| `data/demo.gpkg` | Placeholder dataset in EPSG:25833 with three layers: `fylker` (15 counties) and `kommuner` (357 municipalities), from Kartverket's administrative units with simplified borders, and `byer` (25 towns) |
| `workshop.py` | Plumbing the notebook uses (reading the GeoPackage, writing the config, running Docker) |
| `tools/make_demo_gpkg.py` | Regenerates `data/demo.gpkg` from Kartverket's published PostGIS dumps (needs `shapely` and `pyproj`) |

Prefer a terminal? `docker compose up -d --build` and `docker compose down` work as usual.

## Differences from the starter kit

- **Data:** the starter kit's `postgis/` service is built from a database dump. Here the database is a plain `postgis/postgis` container loaded from the GeoPackage, and it's only started with `SOURCE = "postgis"`.
- **GeoPackage mode:** the backend uses pygeoapi's `OGR` provider instead of `PostgreSQL`, and the PostgreSQL-only processes (downloads, distinct values) are left out. The frontend hides the download panel when they're missing.
- **Collection ids** follow Kartverket's [URL standard](https://kartverket.github.io/ogcapi-docs/docs/url-standard-ogc-api), and the notebook checks them.
- **No CI/deploy workflows:** the starter kit's build and Argo workflows aren't needed in a Codespace.

## Notes for facilitators

- **Images:** the base images are the same ones the starter kit pins: `datadeling-ogcapi-pygeoapi:pygeoapi-v0.5.1` and `datadeling-ogcapi-frontend:frontend-v0.5.1`, by digest. Both are public, so no `docker login` is needed.
- **Map view:** the frontend image is built with a maximum map extent covering Norway, so data elsewhere will display, but the initial map view is clamped to Norway.
- **PostGIS mode:** the database runs in the Codespace with fixed workshop credentials (`postgres`/`qwer1234`, as in the starter kit). Its data survives `stop()` in a Docker volume. `docker compose --profile postgis down -v` wipes it.
- **Ports:** Codespaces ports are private by default. Participants who want to open their API in QGIS or share it make port 3000 public in the Ports tab (step 8 in the notebook). Everything goes through that one port: the frontend serves the pages and proxies all API requests to pygeoapi, and pygeoapi's own links point there too.
