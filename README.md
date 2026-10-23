# OGC API workshop

WS - Dataprodukter

Set up your own **OGC API** with Kartverket's [OGC API starter kit](https://github.com/kartverket/ogc-api-starter): a pygeoapi backend and Kartverket's Next.js frontend, the same software Kartverket runs in production. You publish data, look at how the API works, query it, download from it, style its vector tiles and change the data underneath it, all from Jupyter notebooks.

Everything runs in a GitHub Codespace, so there's nothing to install.

| | Notebook | |
|---|---|---|
| **Part 1** | `part1-geopackage.ipynb` | Publish a GeoPackage as an OGC API – Features service, and see how the API works |
| **Part 2** | `part2-postgis.ipynb` | Publish from a PostGIS database, the setup Kartverket recommends: vector tiles, advanced queries, downloads (processes), styling the tiles yourself, and live data |
| **Part 3** | `part3-why-ogc-api.ipynb` | Optional, for the curious (and the live demo): how an OGC API differs from file downloads, WMS and WFS |

## Get started

You need a GitHub account. QGIS is optional.

1. Click **Code → Codespaces → Create codespace on main** on this repo's GitHub page.
2. Wait for the Codespace to finish setting up (a couple of minutes). This README and `part1-geopackage.ipynb` open automatically.
3. Run the notebook cells from top to bottom: click ▶ on each cell, or press **Shift+Enter**. The cells you're meant to edit are marked ✏️. When you're done, part 1 points you to part 2.

The first time you run a cell, VS Code may ask you to pick a kernel: choose **Python Environments → .venv** (marked *Recommended*).

## Part 1: a GeoPackage

The placeholder dataset `data/demo.gpkg` has three layers: `fylker` (15 counties), `kommuner` (357 municipalities) and `byer` (25 towns), in EPSG:25833.

1. **Download the software:** Kartverket's backend and frontend images, with the starter kit built on top.
2. **Your data:** pick the GeoPackage (`GPKG`) and where the API reads it from (`SOURCE`, see below).
3. **Describe the dataset** and 4. **each collection:** the metadata, in a form or as JSON, with a live preview of the YAML it becomes. The form checks collection ids against Kartverket's URL standard.
5. **Generate the configuration:** `config/pygeoapi-config.yml`, the same file as in the starter kit.
6. **Start your API**, and open the frontend.
7. **How the API works:** content negotiation (one URL, a web page or data), the links and conformance classes clients navigate by, collections and features, and paging with `next` links.
8. **Open it in other tools** (optional): QGIS, ArcGIS Pro, Excel or Power BI. This step makes port 3000 public and checks it can be reached from outside the Codespace.
9. **Change something**, and see it in the API.

`SOURCE` in step 2:

- `"gpkg"` (default): the API reads the GeoPackage directly. Simplest, with no database.
- `"postgis"`: the notebook loads the GeoPackage into a PostGIS database in the Codespace with `ogr2ogr`, and the API reads from there. This adds vector tiles and downloads, like part 2.

## Part 2: a PostGIS database

Real services at Kartverket follow the [golden path](https://kartverket.github.io/ogcapi-docs/docs/ogcapi-skip): data platform → PostGIS → pygeoapi. In part 2 the data already sits in PostGIS, the way a finished data product would, and participants publish it. The database runs in the Codespace, loaded from `postgis/adm.sql.gz` the first time it starts: schema `adm` has `fylker`, `kommuner` and `byer` with full-detail geometries in EPSG:25833, primary and foreign keys, spatial indexes, and comments on every table and column.

1. **Start the database** and download the software.
2. **What's in the database?** The tables and their comments, and plain SQL against them. Participants can load their own dump here.
3. **Describe the dataset** and 4. **choose and describe the collections:** only the tables that are ticked are published. Collections can get download filters by county or municipality.
5. **Generate the configuration:** each collection gets a `PostgreSQL` provider and an `MVT-postgresql` provider (the vector tiles). The connection details are `${DB_*}` placeholders, as on SKIP. Kartverket's processes are added at the top.
6. **Start your API.** The frontend now has tiles and a download panel.
7. **Advanced queries:** sorting, choosing attributes, CQL2 filters, and how a client finds the tiles.
8. **Downloads (OGC API – Processes):** a process is a job the server does for you, explained like ordering food: list the processes (the menu), describe one (its order form) and run it (place the order). Then a download step by step: a diagram of what the server does behind the scenes, what it logged, and the file drawn on a map. Optionally, the same request from the frontend in the browser's developer tools. It ends with an exercise to export one municipality's towns as CSV.
9. **Style the tiles yourself:** vector tiles carry no colours, which is why the frontend draws every collection in the same green. A style editor in the notebook draws the tiles with MapLibre: presets, colour by county or by area, background map, borders, names and 3D, with the MapLibre style JSON the choices add up to. Then, optionally, the same in QGIS. The map loads the tiles from the public address, so this step makes port 3000 public.
10. **Fresh data:** add a town to PostGIS with SQL, and it shows up in the API and on the map straight away, without a restart.
11. **How this works on SKIP:** where the `${DB_*}` values come from in production.

## Part 3 (optional): why OGC API?

`part3-why-ogc-api.ipynb` is optional, for people who want to know what's different from what Geonorge already offers, and for live demos. Part 2 points to it in a collapsible box at the end. It needs part 2's API running.

1. **One question, four ways:** "which municipality is Kartverksveien 21 in?", answered by a file from Geonorge's download API, a WMS, a WFS and the participant's OGC API. All four answer Ringerike; the notebook measures what each cost.
2. **Same code, Kartverket's API:** the question from 1d, with the same code, against Kartverket's demo service.

What the four answers cost when measured on 22 October 2026 (the notebook measures it again each time it runs):

| | You send | You get back | Requests | Downloaded |
|---|---|---|---|---|
| **File download** (Geonorge's download API) | An order: dataset, area, format, projection | A zip of every municipality in Norway (74 MB of GeoJSON, 27,737 features) to search yourself, extracted 10 December 2025 | 2 | 11 MB |
| **WMS** | A map area, then a pixel in it | A PNG picture, then text about one pixel | 2 | 42 KB |
| **WFS** | A 15-line XML request | GML with the full geometry | 1 | 299 KB |
| **OGC API** | One URL: `?filter=S_INTERSECTS(geometry, POINT(10.249 60.144))` | JSON: Ringerike, 3305 | 1 | 2.3 KB |

The OGC API figure was measured against Kartverket's demo service, which runs the same software as the workshop.

## Using your own data

Git ignores your own files in `data/` and `postgis/`, so they aren't committed by accident. GitHub doesn't accept files over 100 MB in a repository, so a large file is something you upload into your own Codespace rather than commit.

### A GeoPackage (part 1)

Drag a GeoPackage (`.gpkg`) into the `data/` folder, set `GPKG` in step 2, and fill in the metadata in steps 3–4. Every layer in the GeoPackage becomes a collection in the API. Layers need a CRS with an EPSG code. Any EPSG code works, and the API reprojects on the fly.

### A database dump (part 2)

Drag a dump into the `postgis/` folder, then in step 2 run `load_dump("postgis/<your file>")` and set `SCHEMA` to your schema. `load_dump` accepts:

| Format | Made with |
|---|---|
| `.sql` | `pg_dump -Fp`, or `ogr2ogr -f PGDump` |
| `.sql.gz` | either of the above, gzipped |
| `.zip` with one `.sql` file | like Kartverket's own development dumps |
| `.dump` | `pg_dump -Fc` (custom format) |

To make one from a PostGIS database (a whole schema; use `--table=schema.table` for one table):

```
pg_dump --no-owner --no-privileges --schema=my_schema -Fc -f my-dump.dump "postgresql://user@host:5432/database"
```

`--no-owner --no-privileges` leaves out database users and permissions, which don't exist in the Codespace. If a dump has them anyway, `load_dump` reports those errors as harmless and loads the data. For Cloud SQL, run `pg_dump` through the Cloud SQL Auth Proxy.

To make one from files (GeoPackage, Shapefile, GeoJSON, …) without a database:

```
ogr2ogr -f PGDump my-dump.sql my-data.gpkg -lco SCHEMA=my_schema
```

Every published table needs a primary key (or another column with unique values) and a geometry column with an EPSG code.

## What's in here

The layout follows [ogc-api-starter](https://github.com/kartverket/ogc-api-starter), plus the notebooks, the data and the Codespace setup:

| File | |
|---|---|
| `part1-geopackage.ipynb` | Part 1: publish a GeoPackage |
| `part2-postgis.ipynb` | Part 2: publish from PostGIS |
| `part3-why-ogc-api.ipynb` | Part 3 (optional): OGC API compared with file downloads, WMS and WFS |
| `workshop.py` | What the notebooks call: reading the data, the metadata editors, writing the config, running Docker, the maps and the style editor |
| `images/` | The diagrams in part 2's step 8 |
| `config/pygeoapi-config.yml` | The API configuration. The notebooks write it; both images are built with it |
| `config/part1-metadata.json`, `config/part2-metadata.json` | The metadata participants edit in steps 3–4 (in a form or as JSON); the notebooks turn it into the config |
| `backend/Dockerfile` | pygeoapi: Kartverket's image + your config + your data |
| `backend/mvt_postgresql.py` | pygeoapi's vector tile provider, with a fix for zoomed-out tiles (see the notes for facilitators) |
| `frontend/Dockerfile` | The frontend: Kartverket's image + your config |
| `docker-compose.yml` | Runs the frontend (port 3000) and backend (port 5001), plus PostGIS when it's needed |
| `data/demo.gpkg` | Part 1's dataset, in EPSG:25833: Kartverket's administrative units with simplified borders, plus 25 towns |
| `postgis/adm.sql.gz` | Part 2's database (schema `adm`), loaded into PostGIS on first start |
| `tools/make_demo_gpkg.py`, `tools/make_demo_dump.py` | Regenerate the two datasets from Kartverket's published PostGIS dumps (need `shapely` and `pyproj`) |
| `.devcontainer/` | The Codespace: Python 3.12, Docker in Docker, the GitHub CLI, the Python and Jupyter extensions, and a `.venv` with what the notebooks need |

Prefer a terminal? `docker compose up -d --build` and `docker compose down` work as usual.

## Differences from the starter kit

- **Data:** the starter kit's `postgis/` service is a custom image built from a dump. Here it's a plain `postgis/postgis` container that loads `postgis/adm.sql.gz` on first start, and it only starts when a notebook needs it (part 2, or part 1 with `SOURCE = "postgis"`).
- **GeoPackage mode:** the backend uses pygeoapi's `OGR` provider instead of `PostgreSQL`, and the PostgreSQL-only processes (downloads, distinct values) are left out. The frontend hides the download panel when they're missing.
- **Collection ids** follow Kartverket's [URL standard](https://kartverket.github.io/ogcapi-docs/docs/url-standard-ogc-api), and the notebooks check them.
- **Vector tiles:** `backend/mvt_postgresql.py` patches pygeoapi's tile provider (see below).
- **No CI/deploy workflows:** the starter kit's build and Argo workflows aren't needed in a Codespace.

## Notes for facilitators

- **Images:** the base images are the same ones the starter kit pins: `datadeling-ogcapi-pygeoapi:pygeoapi-v0.5.1` and `datadeling-ogcapi-frontend:frontend-v0.5.1`, by digest. Both are public, so no `docker login` is needed.
- **One API at a time:** both notebooks write the same `config/pygeoapi-config.yml`, so whichever ran its "Start your API" step last is what's running. Part 3 needs part 2's API.
- **Map view:** the frontend image is built with a maximum map extent covering Norway, so data elsewhere will display, but the initial map view is clamped to Norway.
- **Database:** PostGIS runs in the Codespace with fixed workshop credentials (`postgres`/`qwer1234`, as in the starter kit). Its data survives `stop()` in a Docker volume. `docker compose --profile postgis down -v` wipes it; the next start reloads `adm`. Towns added in part 2's step 10 stay until they're deleted (the commented-out cell at the end of step 10) or the volume is wiped.
- **Ports:** Codespaces ports are private by default. Part 1's step 8 and part 2's step 9 make port 3000 public with `make_public()`, which runs the GitHub CLI (`gh codespace ports visibility 3000:public`); by hand, it's the Ports tab → right-click the port → Port Visibility → Public. The maps in part 2 (steps 9 and 10) and QGIS need it public. An organisation's Codespaces policy can forbid public ports. Everything goes through that one port: the frontend serves the pages and proxies all API requests to pygeoapi, and pygeoapi's own links point there too.
- **A map stays blank or says it couldn't be drawn:** port 3000 is probably private (the cell warns about it), or MapLibre couldn't load from `unpkg.com`. After pulling a change to `workshop.py`, restart the kernel so the notebook picks it up.
- **QGIS shows nothing:** run `check_qgis(...)` (step 8 of part 1, step 9 of part 2). It requests the API from outside the Codespace, the way QGIS does, reports a private port, and fetches test tiles. The tile URL goes in the **URL** field of a generic vector tile connection; pasted into **Style URL** (or anywhere else), QGIS requests `{z}/{y}/{x}` literally and pygeoapi answers 500.
- **"Method Not Allowed" on a process:** opening `/processes/<id>/execution` in a browser sends a GET, but processes only run on a POST with the inputs as JSON. Use the frontend's download panel, or `run_process()` as in step 8 of part 2, which also shows the request it sends.
- **Area downloads:** Kartverket's `export-by-area-gpkg` process only knows two kinds of area, `fylke` and `kommune`, and expects the collections `fylker` and `kommuner`.
- **Patched tile provider:** `backend/mvt_postgresql.py` replaces pygeoapi 0.24.0's `MVT-postgresql` provider in the backend image. Upstream transforms only the four corners of each tile into the storage CRS when it selects features, so in EPSG:25833 the most zoomed-out tiles come out wrong (5 of 357 municipalities at zoom 0, Rana/Lurøy/Træna missing at zoom 2–3; Kartverket's own demo service shows the same). The patch densifies the tile's edges before transforming them, and skips the filter for tiles too big for that (`ST_AsMVTGeom` clips to the tile anyway). The config is unchanged. Check the file against upstream before moving to a newer image.
- **Internet services:** the maps in part 2 (steps 9 and 10) need Kartverket's basemap (`cache.kartverket.no`) and MapLibre and its fonts (`unpkg.com`, `demotiles.maplibre.org`). Part 3 also uses Geonorge's address API, download API, WMS and WFS, and Kartverket's demo OGC API (`datadeling-ogcapi.atgcp1-dev.kartverket.cloud`, a test environment). If one of them is down, only that cell fails.
