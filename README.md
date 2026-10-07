# OGC API workshop

WS - Dataprodukter

Set up your own **OGC API** with Kartverket's [OGC API starter kit](https://github.com/kartverket/ogc-api-starter): a pygeoapi backend and Kartverket's Next.js frontend.

Everything runs in a GitHub Codespace, so there's nothing to install.

| | Notebook | |
|---|---|---|
| **Part 1** | `workshop.ipynb` | Publish a GeoPackage as an OGC API – Features service |
| **Part 2** | `workshop-postgis.ipynb` | Publish from a PostGIS database, the setup Kartverket recommends, with vector tiles, downloads and advanced queries |

## Get started

1. Click **Code → Codespaces → Create codespace on main** on this repo's GitHub page.
2. Wait for the Codespace to finish setting up (a couple of minutes). `workshop.ipynb` opens automatically.
3. Run the notebook cells from top to bottom.

## Part 1: a GeoPackage

The placeholder dataset `data/demo.gpkg` has three layers: `fylker` (15 counties), `kommuner` (357 municipalities) and `byer` (25 towns).

**Using your own data:** drag a GeoPackage (`.gpkg`) into the `data/` folder, set `GPKG` in step 2 of the notebook, and fill in the metadata in steps 3–4. Every layer in the GeoPackage becomes a collection in the API. Layers need a CRS with an EPSG code. Any EPSG code works, and the API reprojects on the fly.

Step 2 also has a `SOURCE` setting:

- `"gpkg"` (default): the API reads the GeoPackage directly. Simplest, with no database.
- `"postgis"`: the notebook loads the GeoPackage into a PostGIS database in the Codespace with `ogr2ogr`, and the API reads from there. This adds vector tiles and downloads, like part 2.

## Part 2: a PostGIS database

Real services at Kartverket follow the [golden path](https://kartverket.github.io/ogcapi-docs/docs/ogcapi-skip): data platform → PostGIS → pygeoapi. In part 2 the data already sits in PostGIS, the way a finished data product would, and participants publish it:

- **The database:** a local PostGIS in the Codespace, loaded from `postgis/adm.sql.gz` the first time it starts. Schema `adm` has `fylker`, `kommuner` and `byer` with full-detail geometries in EPSG:25833, primary and foreign keys, spatial indexes, and comments on every table and column.
- **Choosing what to publish:** only the tables listed in the notebook are published.
- **What you get on top of part 1:** vector tiles (OGC API – Tiles), downloads in the frontend (whole collections, or filtered by county or municipality), and CQL2 filters, sorting and attribute selection.

## What's in here

The layout follows [ogc-api-starter](https://github.com/kartverket/ogc-api-starter), plus the notebooks and the data:

| File | |
|---|---|
| `workshop.ipynb` | Part 1: publish a GeoPackage |
| `workshop-postgis.ipynb` | Part 2: publish from PostGIS |
| `config/pygeoapi-config.yml` | The API configuration. The notebooks write it; both images are built with it |
| `backend/Dockerfile` | pygeoapi: Kartverket's image + your config + your data |
| `frontend/Dockerfile` | The frontend: Kartverket's image + your config |
| `docker-compose.yml` | Runs the frontend (port 3000) and backend (port 5001), plus PostGIS when it's needed |
| `data/demo.gpkg` | Part 1's dataset, in EPSG:25833: Kartverket's administrative units with simplified borders, plus 25 towns |
| `postgis/adm.sql.gz` | Part 2's database (schema `adm`), loaded into PostGIS on first start |
| `workshop.py` | Plumbing the notebooks use (reading the data, writing the config, running Docker) |
| `tools/make_demo_gpkg.py`, `tools/make_demo_dump.py` | Regenerate the two datasets from Kartverket's published PostGIS dumps (need `shapely` and `pyproj`) |

Prefer a terminal? `docker compose up -d --build` and `docker compose down` work as usual.

## Differences from the starter kit

- **Data:** the starter kit's `postgis/` service is a custom image built from a dump. Here it's a plain `postgis/postgis` container that loads `postgis/adm.sql.gz` on first start, and it only runs when a notebook needs it.
- **GeoPackage mode:** the backend uses pygeoapi's `OGR` provider instead of `PostgreSQL`, and the PostgreSQL-only processes (downloads, distinct values) are left out. The frontend hides the download panel when they're missing.
- **Collection ids** follow Kartverket's [URL standard](https://kartverket.github.io/ogcapi-docs/docs/url-standard-ogc-api), and the notebooks check them.
- **No CI/deploy workflows:** the starter kit's build and Argo workflows aren't needed in a Codespace.

## Notes for facilitators

- **Images:** the base images are the same ones the starter kit pins: `datadeling-ogcapi-pygeoapi:pygeoapi-v0.5.1` and `datadeling-ogcapi-frontend:frontend-v0.5.1`, by digest. Both are public, so no `docker login` is needed.
- **One API at a time:** both notebooks write the same `config/pygeoapi-config.yml`, so whichever ran its "Start your API" step last is what's running.
- **Map view:** the frontend image is built with a maximum map extent covering Norway, so data elsewhere will display, but the initial map view is clamped to Norway.
- **Database:** PostGIS runs in the Codespace with fixed workshop credentials (`postgres`/`qwer1234`, as in the starter kit). Its data survives `stop()` in a Docker volume. `docker compose --profile postgis down -v` wipes it; the next start reloads `adm`.
- **Area downloads:** Kartverket's `export-by-area-gpkg` process only knows two kinds of area, `fylke` and `kommune`, and expects the collections `fylker` and `kommuner`.
- **Ports:** Codespaces ports are private by default. Participants who want to open their API in QGIS or share it make port 3000 public in the Ports tab. Everything goes through that one port: the frontend serves the pages and proxies all API requests to pygeoapi, and pygeoapi's own links point there too.
