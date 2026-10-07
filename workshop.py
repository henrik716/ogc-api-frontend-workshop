"""
workshop.py — plumbing for workshop.ipynb.

Everything here is the boring part (reading the GeoPackage, writing YAML,
running docker compose), kept out of the notebook so its cells stay about
the things participants actually decide: their data and their metadata.
"""

import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_FILE = ROOT / "config" / "pygeoapi-config.yml"
ENV_FILE = ROOT / ".env"

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
# Output CRSs offered on every collection (?crs=...), same list Kartverket uses.
OUTPUT_EPSG = [4326, 4258, 25832, 25833, 25835, 3857]

SOURCES = ("gpkg", "postgis")
# Must match the postgis service in docker-compose.yml
PG_CONNECTION = "PG:host=postgis dbname=ogcapi user=postgres password=qwer1234"

# The processes from the ogc-api-starter example config (PostgreSQL only)
POSTGIS_PROCESSES = {
    "get-distinct-values": {"type": "process", "processor": {"name": "distinct_values.DistinctValuesProcessor"}},
    "export-collection-gpkg": {"type": "process", "processor": {"name": "processes.gpkg_collection.ExportCollectionGpkgProcessor"}},
    "export-all-gpkg": {"type": "process", "processor": {"name": "processes.gpkg_all_collections.ExportAllGpkgProcessor"}},
    "export-by-area-gpkg": {"type": "process", "processor": {"name": "processes.gpkg_by_area.ExportByAreaGpkgProcessor"}},
    "export-collection-csv": {"type": "process", "processor": {"name": "processes.csv_collection.ExportCollectionCsvProcessor"}},
}


def epsg_uri(code: int) -> str:
    return f"http://www.opengis.net/def/crs/EPSG/0/{code}"


# ------------------------------------------------------------------
# Running things
# ------------------------------------------------------------------
def run(*cmd: str) -> None:
    """Run a command, streaming its output; stop the notebook if it fails."""
    label = " ".join(cmd)
    print(f"→ {label}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"✗ {label} failed (exit {proc.returncode}) — see output above.")
    print(f"✓ {label}")


def compose(*args: str) -> None:
    # --progress quiet: the default progress UI turns into a wall of garbled
    # spinner frames inside a notebook cell. Real errors still print.
    run("docker", "compose", "--progress", "quiet", *args)


def public_url(port: int = 3000) -> str:
    """The URL a browser uses to reach `port` — the forwarded URL inside a Codespace."""
    codespace = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if codespace and domain:
        return f"https://{codespace}-{port}.{domain}"
    return f"http://localhost:{port}"


def get_json(path: str):
    """GET a path from the API (the pygeoapi backend) as JSON."""
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"http://localhost:5001{path}{sep}f=json", timeout=30) as res:
        return json.load(res)


def wait_until_ready(timeout: int = 120) -> None:
    print("Waiting for the API to answer", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get_json("/collections")
            urllib.request.urlopen("http://localhost:3000/", timeout=30)
            print(" ready.")
            return
        except Exception:
            print(".", end="", flush=True)
            time.sleep(2)
    print()
    subprocess.run(["docker", "compose", "logs", "--tail", "40"], cwd=ROOT)
    raise SystemExit("✗ The API did not come up in time — see the logs above.")


# ------------------------------------------------------------------
# Reading the GeoPackage
#
# A GeoPackage is just an SQLite file with a few well-known metadata tables,
# so the standard library is enough — no GDAL needed in the notebook.
# ------------------------------------------------------------------
VALID_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def safe_id(name: str) -> str:
    """Layer name → collection id following Kartverket's URL standard:
    lowercase, words separated by hyphens, æøå written as ae/o/a
    (e.g. 'Fylker_Øst' → 'fylker-ost').
    https://kartverket.github.io/ogcapi-docs/docs/url-standard-ogc-api
    """
    name = name.lower().translate(str.maketrans({"æ": "ae", "ø": "o", "å": "a"}))
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "layer"


def table_name(layer: str) -> str:
    """Layer name → PostGIS table name (same as the id, but with underscores)."""
    return safe_id(layer).replace("-", "_")


def inspect_gpkg(path) -> list[dict]:
    path = (ROOT / path).resolve()
    if not path.exists():
        raise SystemExit(f"✗ {path} not found. Put your GeoPackage in the data/ folder and check GPKG.")
    if DATA_DIR not in path.parents:
        raise SystemExit("✗ The GeoPackage must be inside the data/ folder (that's what the API container sees).")

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT c.table_name, g.column_name, g.geometry_type_name,
               c.min_x, c.min_y, c.max_x, c.max_y,
               s.organization, s.organization_coordsys_id
        FROM gpkg_contents c
        JOIN gpkg_geometry_columns g ON g.table_name = c.table_name
        LEFT JOIN gpkg_spatial_ref_sys s ON s.srs_id = g.srs_id
        WHERE c.data_type = 'features'
        ORDER BY c.table_name
        """
    ).fetchall()

    layers = []
    for table, geom_col, geom_type, minx, miny, maxx, maxy, org, code in rows:
        if (org or "").upper() != "EPSG":
            print(f"⚠ Skipping layer '{table}': its CRS is not an EPSG code ({org}:{code}).")
            continue
        info = [r for r in con.execute(f'PRAGMA table_info("{table}")') if r[1] != geom_col]
        columns = [(r[1], r[2]) for r in info]
        pk = next((r[1] for r in info if r[5]), None)
        if None in (minx, miny, maxx, maxy):
            # Extent not recorded in gpkg_contents — fall back to the spatial index.
            try:
                minx, miny, maxx, maxy = con.execute(
                    f'SELECT min(minx), min(miny), max(maxx), max(maxy) FROM "rtree_{table}_{geom_col}"'
                ).fetchone()
            except sqlite3.OperationalError:
                print(f"⚠ Skipping layer '{table}': no extent recorded and no spatial index.")
                continue
        layers.append(
            {
                "layer": table,
                "id": safe_id(table),
                "geometry": geom_type,
                "epsg": int(code),
                "columns": columns,
                "pk": pk,
                "count": con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                "bbox": to_crs84((minx, miny, maxx, maxy), int(code)),
                "file": path.relative_to(DATA_DIR).as_posix(),
            }
        )
    con.close()

    if not layers:
        raise SystemExit("✗ No usable feature layers found in this GeoPackage.")
    return layers


def to_crs84(bbox, epsg: int) -> list[float]:
    if epsg != 4326:
        bbox = Transformer.from_crs(epsg, 4326, always_xy=True).transform_bounds(*bbox, densify_pts=21)
    return [round(v, 6) for v in bbox]


def describe(layers: list[dict]) -> None:
    for l in layers:
        print(f"Layer '{l['layer']}' (default collection id: '{l['id']}')")
        print(f"  {l['count']} features, {l['geometry']}, stored in EPSG:{l['epsg']}")
        print(f"  bbox (lon/lat): {l['bbox']}")
        print("  columns: " + ", ".join(f"{n} ({t or '?'})" for n, t in l["columns"]))
        print()


# ------------------------------------------------------------------
# Loading into PostGIS (SOURCE = "postgis" only)
# ------------------------------------------------------------------
def load_into_postgis(layers: list[dict]) -> None:
    """Start the local PostGIS database and copy every layer into it with ogr2ogr."""
    compose("--profile", "postgis", "up", "-d", "--wait", "postgis")
    for l in layers:
        compose(
            "--profile", "tools", "run", "--rm", "loader",
            "ogr2ogr", "-f", "PostgreSQL", PG_CONNECTION,
            f"/data/{l['file']}", l["layer"],
            "-nln", table_name(l["layer"]),
            "-overwrite",
            # Keep ids, column names and the CRS exactly as in the GeoPackage,
            # so the metadata you wrote for it still matches.
            "-preserve_fid", "-lco", f"FID={l['pk']}",
            "-lco", "GEOMETRY_NAME=geometry",
            "-lco", "LAUNDER=NO",
        )
    print(f"✓ Loaded {len(layers)} layer(s) into PostGIS: " + ", ".join(table_name(l["layer"]) for l in layers))


# ------------------------------------------------------------------
# Building pygeoapi-config.yml
#
# Both images bake in this one file (see backend/ and frontend/Dockerfile):
# pygeoapi serves the API from it, and the Kartverket frontend reads it
# directly for the dataset and collection info it shows.
# ------------------------------------------------------------------
def _lang(value):
    """'text' → {'en': 'text'}, the same shape as the ogc-api-starter config.

    The frontend looks up `.en` directly, so that key must be there whatever
    language the text is actually written in.
    """
    return value if isinstance(value, dict) else {"en": value}


def _keywords(value):
    return value if isinstance(value, dict) else {"en": list(value)}


def build_config(dataset: dict, collections: dict, layers: list[dict], source: str = "gpkg") -> dict:
    if source not in SOURCES:
        raise SystemExit(f"✗ SOURCE must be one of {SOURCES}, not '{source}'.")

    # Kartverket's processes — GeoPackage/CSV downloads in the frontend, and the
    # distinct-values lookup — query PostgreSQL directly, so only in postgis mode.
    resources = dict(POSTGIS_PROCESSES) if source == "postgis" else {}
    for l in layers:
        meta = collections.get(l["layer"])
        if meta is None:
            print(f"⚠ No metadata for layer '{l['layer']}' — using defaults. Add this to COLLECTIONS:")
            print(_stub(l))
            meta = {}

        # The collection id is the URL path segment: /collections/<id>.
        # Stored back on the layer so later cells (links, examples) use it too.
        l["id"] = meta.get("id", safe_id(l["layer"]))
        if not VALID_ID.match(l["id"]):
            raise SystemExit(
                f"✗ {l['layer']}: id '{l['id']}' doesn't follow the URL standard — use lowercase "
                f"letters, digits and hyphens, e.g. '{safe_id(l['id'])}'."
            )

        column_names = [name for name, _ in l["columns"]]
        id_field = meta.get("id_field", l["pk"])
        title_field = meta.get("title_field")
        if id_field not in column_names:
            raise SystemExit(f"✗ {l['layer']}: id_field '{id_field}' is not a column. Pick one of {column_names}")
        if title_field and title_field not in column_names:
            raise SystemExit(f"✗ {l['layer']}: title_field '{title_field}' is not a column. Pick one of {column_names}")

        if source == "postgis":
            # Same shape as the ogc-api-starter example; ${DB_*} are filled in
            # from the backend's environment (docker-compose.yml).
            provider = {
                "type": "feature",
                "name": "PostgreSQL",
                "data": {
                    "host": "${DB_HOST}",
                    "dbname": "${DB_NAME}",
                    "user": "${DB_USER}",
                    "password": "${DB_PASSWORD}",
                    "search_path": ["public"],
                },
                "id_field": id_field,
                "table": table_name(l["layer"]),
                "geom_field": "geometry",
            }
        else:
            provider = {
                "type": "feature",
                "name": "OGR",
                "data": {
                    "source_type": "GPKG",
                    "source": f"/data/{l['file']}",
                    "source_capabilities": {"paging": True},
                },
                "layer": l["layer"],
                "id_field": id_field,
            }
        provider["crs"] = [CRS84] + [epsg_uri(c) for c in OUTPUT_EPSG]
        provider["storage_crs"] = CRS84 if l["epsg"] == 4326 else epsg_uri(l["epsg"])
        if title_field:
            provider["title_field"] = title_field

        resources[l["id"]] = {
            "type": "collection",
            "title": _lang(meta.get("title", l["layer"])),
            "description": _lang(meta.get("description", f"Features from layer {l['layer']}")),
            "keywords": _keywords(meta.get("keywords", [])),
            "extents": {"spatial": {"bbox": l["bbox"], "crs": CRS84}},
            "providers": [provider],
        }

    return {
        "server": {
            "bind": {"host": "0.0.0.0", "port": 5000},
            # Filled in from the environment at startup (see docker-compose.yml)
            "url": "${PYGEOAPI_URL}",
            "mimetype": "application/json; charset=UTF-8",
            "encoding": "utf-8",
            "gzip": False,
            "languages": ["en-US"],
            "cors": True,
            "pretty_print": True,
            "limits": {"default_items": 20, "max_items": 1000},
            "map": {
                "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap contributors</a>',
            },
            "admin": False,
            "templates": {"path": "/pygeoapi/custom_templates", "static": "/pygeoapi/pygeoapi/static"},
        },
        "logging": {"level": "INFO"},
        "metadata": {
            "identification": {
                "title": _lang(dataset["title"]),
                "description": _lang(dataset["description"]),
                "keywords": _keywords(dataset.get("keywords", [])),
                "keywords_type": "theme",
                "terms_of_service": dataset["license"]["url"],
                "url": dataset["provider"]["url"],
            },
            "license": dataset["license"],
            "provider": dataset["provider"],
            # contactUrl is read by the frontend's "contact us" link
            "contact": {**dataset["contact"], "contactUrl": dataset["contact"].get("url")},
        },
        "resources": resources,
    }


def _stub(l: dict) -> str:
    names = [n for n, _ in l["columns"]]
    title_guess = next((n for n in names if n.lower() in ("navn", "name", "tittel", "title")), None)
    return (
        f'    "{l["layer"]}": {{\n'
        f'        "id": "{safe_id(l["layer"])}",\n'
        f'        "title": "...",\n'
        f'        "description": "...",\n'
        f'        "keywords": [],\n'
        f'        "id_field": {json.dumps(l["pk"])},\n'
        f'        "title_field": {json.dumps(title_guess)},\n'
        f"    }},"
    )


def write_config(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(
        "# Generated by workshop.ipynb — edit the notebook and re-run it instead of this file.\n"
        + yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    uses_postgis = any(
        p["name"] == "PostgreSQL" for r in config["resources"].values() for p in r.get("providers", [])
    )
    # Read by docker-compose.yml. Public URLs: the backend writes its own into
    # every link, and the frontend hands its own to the browser.
    # COMPOSE_PROFILES=postgis makes every `docker compose` command include the database.
    env = f"FRONTEND_URL={public_url(3000)}\nBACKEND_URL={public_url(5001)}\n"
    if uses_postgis:
        env += "COMPOSE_PROFILES=postgis\n"
    ENV_FILE.write_text(env, encoding="utf-8")

    n = sum(r["type"] == "collection" for r in config["resources"].values())
    print(f"✓ Wrote {CONFIG_FILE.relative_to(ROOT)} ({n} collection(s), {'PostGIS' if uses_postgis else 'GeoPackage'})")


# ------------------------------------------------------------------
# Starting and stopping
# ------------------------------------------------------------------
def start() -> None:
    # --build bakes the current config and data into the images. Takes a few
    # seconds: it's just a file copy on top of Kartverket's images.
    compose("up", "-d", "--build")
    wait_until_ready()


def stop() -> None:
    # --profile postgis so the database is stopped too, whichever SOURCE is set.
    # Its data is kept (in a Docker volume) for next time.
    compose("--profile", "postgis", "down")


def show_links(layers: list[dict]) -> None:
    from IPython.display import Markdown, display

    frontend, backend = public_url(3000), public_url(5001)
    rows = [
        ("Landing page (frontend)", frontend),
        ("The API itself (pygeoapi)", backend),
        ("API documentation (OpenAPI)", f"{backend}/openapi?f=html"),
    ]
    for l in layers:
        rows += [
            (f"`{l['id']}` — collection page", f"{frontend}/collections/{l['id']}"),
            (f"`{l['id']}` — features as GeoJSON", f"{backend}/collections/{l['id']}/items?f=json"),
        ]
    display(Markdown("| | |\n|---|---|\n" + "\n".join(f"| {a} | <{b}> |" for a, b in rows)))
