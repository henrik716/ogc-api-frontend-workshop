"""
workshop.py — plumbing for the workshop notebooks (part1-geopackage.ipynb, part2-postgis.ipynb).

Everything here is the boring part (reading the GeoPackage, writing YAML,
running docker compose), kept out of the notebook so its cells stay about
the things participants actually decide: their data and their metadata.
"""

import copy
import gzip
import html
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import shapely
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


def fetch_json(url: str, params: dict | None = None):
    """GET any URL as JSON, e.g. one of Geonorge's APIs."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as res:
        return json.load(res)


def get_json(path: str, params: dict | None = None):
    """GET a path from the API (the pygeoapi backend) as JSON.

    Query parameters can go in the path, or in `params`, which takes care of
    URL-encoding (needed for e.g. CQL2 filters with spaces and quotes).
    `path` can also be a link from one of the API's own responses (e.g. the
    "next" page): those point at the public address, which is mapped back here.
    """
    if path.startswith(public_url(3000)):
        path = path[len(public_url(3000)):]
    if "f=" not in path:
        params = {"f": "json", **(params or {})}
    return fetch_json(f"http://localhost:5001{path}", params)


def show_request(path: str, accept: str) -> None:
    """GET a path through the frontend, the way a browser or QGIS does, and show
    the start of the response: the status, its Content-Type, and the body."""
    req = urllib.request.Request(f"http://localhost:3000{path}", headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=60) as res:
        body = res.read(400).decode("utf-8", errors="replace")
        print(f"GET {path}\nAccept: {accept}\n")
        print(f"→ {res.status} {res.reason}, Content-Type: {res.headers.get('Content-Type')}")
    print(body.strip()[:400] + " …\n")


def run_process(process_id: str, inputs: dict):
    """Run an OGC API process and return its result.

    Execution is a POST with the inputs as JSON (a browser opening the
    .../execution URL sends a GET, which the API answers with 405 Method Not
    Allowed). Without a 'Prefer: respond-async' header pygeoapi runs it right
    away and sends the result back: parsed JSON, or the raw bytes of a file.
    """
    req = urllib.request.Request(
        f"http://localhost:5001/processes/{process_id}/execution",
        data=json.dumps({"inputs": inputs}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            content_type, body = res.headers.get_content_type(), res.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("description", detail)
        except ValueError:
            pass
        raise SystemExit(f"✗ The process '{process_id}' failed ({err.code}): {detail}")
    return json.loads(body) if content_type == "application/json" else body


def wait_until_ready(timeout: int = 120) -> None:
    print("Waiting for the API to answer", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get_json("/collections")
            urllib.request.urlopen("http://localhost:3000/collections?f=json", timeout=30)  # via the frontend
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
               s.organization, s.organization_coordsys_id
        FROM gpkg_contents c
        JOIN gpkg_geometry_columns g ON g.table_name = c.table_name
        LEFT JOIN gpkg_spatial_ref_sys s ON s.srs_id = g.srs_id
        WHERE c.data_type = 'features'
        ORDER BY c.table_name
        """
    ).fetchall()

    layers = []
    for table, geom_col, geom_type, org, code in rows:
        if (org or "").upper() != "EPSG":
            print(f"⚠ Skipping layer '{table}': its CRS is not an EPSG code ({org}:{code}).")
            continue
        info = [r for r in con.execute(f'PRAGMA table_info("{table}")') if r[1] != geom_col]
        columns = [(r[1], r[2]) for r in info]
        pk = next((r[1] for r in info if r[5]), None)
        boxes = _feature_boxes(con, table, geom_col)
        if not boxes:
            print(f"⚠ Skipping layer '{table}': it has no geometries.")
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
                "bbox": to_crs84(boxes, int(code)),
                "file": path.relative_to(DATA_DIR).as_posix(),
            }
        )
    con.close()

    if not layers:
        raise SystemExit("✗ No usable feature layers found in this GeoPackage.")
    return layers


# Size of the envelope in a GeoPackage geometry header, by envelope indicator
_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _feature_boxes(con, table: str, geom_col: str) -> list[tuple]:
    """Each feature's bounding box (minx, miny, maxx, maxy), in the layer's own CRS."""
    try:
        # The spatial index already has them, when there is one
        return con.execute(f'SELECT minx, miny, maxx, maxy FROM "rtree_{table}_{geom_col}"').fetchall()
    except sqlite3.OperationalError:
        pass
    wkbs = []
    for (blob,) in con.execute(f'SELECT "{geom_col}" FROM "{table}" WHERE "{geom_col}" IS NOT NULL'):
        envelope = (blob[3] >> 1) & 0b111  # header: "GP", version, flags, srs_id, envelope
        wkbs.append(bytes(blob[8 + _ENVELOPE_BYTES[envelope]:]))
    bounds = shapely.bounds(shapely.from_wkb(wkbs))
    return [tuple(b) for b in bounds if not any(map(math.isnan, b))]


def to_crs84(boxes: list[tuple], epsg: int) -> list[float]:
    """Combine per-feature boxes into one lon/lat extent.

    Reprojecting each feature's (small) box and combining them gives a much
    tighter extent than reprojecting the layer's one big box, whose corners
    can land far outside the data (e.g. in the sea west of Norway).
    """
    xs = [x for b in boxes for x in (b[0], b[0], b[2], b[2])]
    ys = [y for b in boxes for y in (b[1], b[3], b[1], b[3])]
    if epsg != 4326:
        xs, ys = Transformer.from_crs(epsg, 4326, always_xy=True).transform(xs, ys)
    return [round(v, 6) for v in (min(xs), min(ys), max(xs), max(ys))]


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


def prepare_data(gpkg, source: str) -> list[dict]:
    """Step 2 in one go: read the GeoPackage, show what's in it, and — for
    SOURCE = "postgis" — (re)load it into the database, so that can't be forgotten."""
    if source not in SOURCES:
        raise SystemExit(f"✗ SOURCE must be one of {SOURCES}, not '{source}'.")
    layers = inspect_gpkg(gpkg)
    describe(layers)
    if source == "postgis":
        load_into_postgis(layers)
    return layers


def download() -> None:
    print("Downloading Kartverket's backend and frontend (about 650 MB). "
          "The first time takes a minute or two, so feel free to read ahead.", flush=True)
    compose("build")


# ------------------------------------------------------------------
# Part 2: an existing PostGIS database (part2-postgis.ipynb)
# ------------------------------------------------------------------
def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_json(query: str) -> list[dict]:
    """Run a SELECT in the workshop database and return its rows as dicts."""
    sql = f"SELECT coalesce(json_agg(q), '[]') FROM ({query}) q"
    proc = subprocess.run(
        ["docker", "compose", "--profile", "postgis", "exec", "-T", "postgis",
         "psql", "-U", "postgres", "-d", "ogcapi", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"✗ The database query failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def run_sql(statement: str) -> None:
    """Run any SQL statement in the workshop database (e.g. an INSERT) and show psql's output."""
    proc = subprocess.run(
        ["docker", "compose", "--profile", "postgis", "exec", "-T", "postgis",
         "psql", "-U", "postgres", "-d", "ogcapi", "-X", "-v", "ON_ERROR_STOP=1", "-c", statement],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"✗ The SQL failed:\n{proc.stderr.strip()}")
    print(proc.stdout.strip())


def start_database() -> None:
    print("Starting PostGIS. The very first start also loads the workshop database "
          "(postgis/adm.sql.gz), which takes a minute or two.", flush=True)
    compose("--profile", "postgis", "up", "-d", "--wait", "postgis")
    if not psql_json("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'adm'"):
        # The database was created before the dump was there (e.g. by part 1),
        # so PostgreSQL skipped loading it. Load it now.
        print("Loading the workshop database into the existing PostGIS…", flush=True)
        run("docker", "compose", "--profile", "postgis", "exec", "-T", "postgis", "sh", "-c",
            "gunzip -c /docker-entrypoint-initdb.d/20_adm.sql.gz | psql -U postgres -d ogcapi -q -v ON_ERROR_STOP=1")
    print("✓ Database ready")


def _open_dump(path: Path):
    """The dump's SQL (or pg_dump archive) as a binary stream, unpacking .gz and .zip."""
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic[:2] == b"\x1f\x8b":
        return gzip.open(path, "rb")
    if magic == b"PK\x03\x04":
        zf = zipfile.ZipFile(path)
        members = [n for n in zf.namelist() if not n.endswith("/")]
        if len(members) != 1:
            raise SystemExit(f"✗ {path.name} should contain exactly one file, but has {len(members)}.")
        return zf.open(members[0])
    return open(path, "rb")


def load_dump(path) -> None:
    """Load a database dump into the workshop database.

    Accepts plain SQL (pg_dump -Fp, or ogr2ogr -f PGDump), optionally gzipped
    (.sql.gz) or zipped, and pg_dump's custom format (pg_dump -Fc). The file is
    streamed into the container, so it can be large.
    """
    path = (ROOT / path).resolve()
    if not path.exists():
        raise SystemExit(f"✗ {path} not found. Drag your dump into the postgis/ folder and check the path.")

    stream = _open_dump(path)
    head = stream.read(5)
    if head == b"PGDMP":  # pg_dump's custom format
        tool = ["pg_restore", "-U", "postgres", "-d", "ogcapi", "--no-owner", "--no-privileges"]
    else:
        tool = ["psql", "-U", "postgres", "-d", "ogcapi", "-X", "-q"]
    print(f"Loading {path.name} with {tool[0]}…", flush=True)

    proc = subprocess.Popen(
        ["docker", "compose", "--profile", "postgis", "exec", "-T", "postgis", *tool],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        proc.stdin.write(head)
        shutil.copyfileobj(stream, proc.stdin, length=1024 * 1024)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # the tool stopped early; its own error message is reported below
    finally:
        stream.close()
    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    proc.wait()
    if proc.returncode != 0 and not stderr.strip():
        raise SystemExit(f"✗ Loading {path.name} failed (exit {proc.returncode}).")

    # Dumps often grant rights to, or set owners from, roles that don't exist
    # here. Those errors are harmless: the data itself still loads.
    errors = [l for l in stderr.splitlines() if "ERROR" in l or "error:" in l]
    harmless = [l for l in errors if "role" in l and "does not exist" in l]
    serious = [l for l in errors if l not in harmless]
    if harmless:
        print(f"  (ignored {len(harmless)} error(s) about database roles that don't exist here)")
    if serious:
        print(f"⚠ {len(serious)} error(s) while loading. The first few:")
        for line in serious[:5]:
            print("   ", line)
    elif proc.returncode != 0 and not harmless:
        print(stderr.strip()[-2000:])
        raise SystemExit(f"✗ Loading {path.name} failed (exit {proc.returncode}) — see above.")
    print(f"✓ Loaded {path.name}. Schemas in the database now: {', '.join(list_schemas())}")


def list_schemas() -> list[str]:
    """The schemas holding data (leaving out PostgreSQL's and PostGIS's own)."""
    return [r["nspname"] for r in psql_json("""
        SELECT nspname FROM pg_namespace
        WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN ('information_schema', 'tiger', 'tiger_data', 'topology')
        ORDER BY nspname""")]


def inspect_postgis(schema: str) -> list[dict]:
    """Every table with a geometry column in `schema`: columns, keys, comments and extent."""
    tables = psql_json(f"""
        SELECT f_table_name AS table, f_geometry_column AS geom_column, srid, type,
               obj_description(format('%I.%I', f_table_schema, f_table_name)::regclass, 'pg_class') AS comment
        FROM geometry_columns
        WHERE f_table_schema = {_sql_literal(schema)}
        ORDER BY f_table_name""")
    if not tables:
        raise SystemExit(f"✗ No tables with a geometry column in schema '{schema}'.")

    layers = []
    for t in tables:
        qualified = f'"{schema}"."{t["table"]}"'
        columns = psql_json(f"""
            SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                   col_description(a.attrelid, a.attnum) AS comment,
                   coalesce(i.indisprimary, false) AS pk
            FROM pg_attribute a
            LEFT JOIN pg_index i ON i.indrelid = a.attrelid AND i.indisprimary AND a.attnum = ANY(i.indkey)
            WHERE a.attrelid = {_sql_literal(qualified)}::regclass AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum""")
        # Each feature's box, reprojected and combined: a tight lon/lat extent
        # without reprojecting every vertex of every geometry.
        stats = psql_json(f"""
            SELECT n AS count, ST_XMin(e) AS minx, ST_YMin(e) AS miny, ST_XMax(e) AS maxx, ST_YMax(e) AS maxy
            FROM (SELECT count(*) AS n, ST_Extent(ST_Transform(ST_Envelope("{t['geom_column']}"), 4326)) AS e
                  FROM {qualified}) s""")[0]
        if not stats["count"]:
            print(f"⚠ Skipping table '{schema}.{t['table']}': it's empty.")
            continue
        attrs = [c for c in columns if c["name"] != t["geom_column"]]
        layers.append(
            {
                "layer": f"{schema}.{t['table']}",
                "schema": schema,
                "table": t["table"],
                "geom_column": t["geom_column"],
                "id": safe_id(t["table"]),
                "geometry": t["type"],
                "epsg": t["srid"],
                "columns": [(c["name"], c["type"]) for c in attrs],
                "column_comments": {c["name"]: c["comment"] for c in attrs},
                "pk": next((c["name"] for c in attrs if c["pk"]), None),
                "count": stats["count"],
                "bbox": [round(stats[k], 6) for k in ("minx", "miny", "maxx", "maxy")],
                "comment": t["comment"],
            }
        )
    return layers


def describe_postgis(layers: list[dict]) -> None:
    for l in layers:
        print(f"Table {l['layer']}")
        if l["comment"]:
            print(f"  \"{l['comment']}\"")
        print(f"  {l['count']} rows, {l['geometry']}, EPSG:{l['epsg']}, primary key: {l['pk']}")
        print(f"  bbox (lon/lat): {l['bbox']}")
        for name, type_ in l["columns"]:
            comment = l["column_comments"].get(name)
            print(f"    {name:16} {type_:18} {comment or ''}")
        print()


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
        l.setdefault("default_id", l["id"])
        l["id"] = meta.get("id", l["default_id"])
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
                    "search_path": [l.get("schema", "public")],
                },
                "id_field": id_field,
                "table": l.get("table", table_name(l["layer"])),
                "geom_field": l.get("geom_column", "geometry"),
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
        storage_crs = CRS84 if l["epsg"] == 4326 else epsg_uri(l["epsg"])
        provider["crs"] = [CRS84] + [epsg_uri(c) for c in OUTPUT_EPSG]
        provider["storage_crs"] = storage_crs
        if title_field:
            provider["title_field"] = title_field
        providers = [provider]

        if source == "postgis":
            # OGC API - Tiles: vector tiles cut on the fly from the same table
            # (pygeoapi's built-in MVT-postgresql provider, as in the starter kit).
            providers.append(
                {
                    "type": "tile",
                    "name": "MVT-postgresql",
                    "data": copy.deepcopy(provider["data"]),  # a copy, so the YAML has no &id001 anchors
                    "id_field": id_field,
                    "table": provider["table"],
                    "geom_field": provider["geom_field"],
                    "storage_crs": storage_crs,
                    "options": {"zoom": {"min": 0, "max": 18}},
                    "format": {"name": "pbf", "mimetype": "application/vnd.mapbox-vector-tile"},
                }
            )

        resources[l["id"]] = {
            "type": "collection",
            "title": _lang(meta.get("title", l["layer"])),
            "description": _lang(meta.get("description", f"Features from layer {l['layer']}")),
            "keywords": _keywords(meta.get("keywords", [])),
            "extents": {"spatial": {"bbox": l["bbox"], "crs": CRS84}},
            "providers": providers,
        }
        if meta.get("download"):
            # Area filters for the frontend's download panel (postgis only),
            # in the same shape as Kartverket's own config
            resources[l["id"]]["download"] = meta["download"]
        if meta.get("links"):
            # e.g. the dataset's page in Geonorge — shown in the frontend and the API
            resources[l["id"]]["links"] = [
                {"type": "text/html", "rel": "related", **link} for link in meta["links"]
            ]

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
        "# Generated by the workshop notebooks — edit the notebook and re-run it instead of this file.\n"
        + yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    uses_postgis = any(
        p["name"] == "PostgreSQL" for r in config["resources"].values() for p in r.get("providers", [])
    )
    # Read by docker-compose.yml. PUBLIC_URL is the frontend's address, which
    # pygeoapi also uses for its links (the frontend proxies them through).
    # COMPOSE_PROFILES=postgis makes every `docker compose` command include the database.
    env = f"PUBLIC_URL={public_url(3000)}\n"
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


def show_urls(rows: list[tuple], openable: bool = True) -> None:
    """A table of (label, URL) rows, each URL with a Copy button: notebook
    output turns URLs into links, which are awkward to copy. `openable` adds
    an Open link too (not for tile URL templates, which aren't pages)."""
    from IPython.display import HTML, display

    # Copy with the clipboard API, or by selecting the text where that's not allowed
    copy = ("const i = this.parentNode.querySelector('input'); i.select(); "
            "(navigator.clipboard ? navigator.clipboard.writeText(i.value) : Promise.reject())"
            ".catch(() => document.execCommand('copy')).finally(() => this.textContent = 'Copied');")
    cells = []
    for label, url in rows:
        url = html.escape(url, quote=True)
        open_link = f' <a href="{url}" target="_blank">Open</a>' if openable else ""
        cells.append(
            f"<tr><td>{label}</td><td style='white-space: nowrap'>"
            f"<input readonly value=\"{url}\" onclick=\"this.select()\" "
            f"style='width: 46em; font-family: monospace; font-size: 12px'> "
            f"<button onclick=\"{html.escape(copy, quote=True)}\">Copy</button>{open_link}</td></tr>")
    display(HTML(f"<table>{''.join(cells)}</table>"))


def show_links(layers: list[dict]) -> None:
    # One address for everything: the frontend serves the pages and passes
    # every other request (?f=json, /openapi, tiles, ...) on to pygeoapi.
    base = public_url(3000)
    resources = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))["resources"]
    rows = [
        ("Landing page", base),
        ("The API as JSON", f"{base}/?f=json"),
        ("API documentation (OpenAPI)", f"{base}/openapi?f=html"),
    ]
    if any(r["type"] == "process" for r in resources.values()):
        # The frontend has no page of its own for these; it shows pygeoapi's
        rows.append(("Processes (OGC API – Processes)", f"{base}/processes"))
    for l in layers:
        rows += [
            (f"<code>{l['id']}</code>: collection page", f"{base}/collections/{l['id']}"),
            (f"<code>{l['id']}</code>: features as GeoJSON", f"{base}/collections/{l['id']}/items?f=json"),
        ]
        if any(p["type"] == "tile" for p in resources.get(l["id"], {}).get("providers", [])):
            rows.append((f"<code>{l['id']}</code>: vector tiles", f"{base}/collections/{l['id']}/tiles"))
    show_urls(rows)


# ------------------------------------------------------------------
# Checking that QGIS can reach the API
# ------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # report a redirect (e.g. to GitHub's sign-in) instead of following it


def _fetch_as_qgis(url: str):
    """GET a URL the way QGIS does: from outside, not signed in to GitHub,
    with QGIS's User-Agent. Returns (status, content type, body)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 QGIS/34000"})
    try:
        with opener.open(req, timeout=60) as res:
            return res.status, res.headers.get_content_type(), res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get_content_type(), err.read()


def _tile_at(lon: float, lat: float, z: int) -> tuple[int, int]:
    """The WebMercatorQuad tile (column, row) that holds a lon/lat at zoom z."""
    n = 2**z
    col = int((lon + 180) / 360 * n)
    row = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return col, row


def make_public(port: int = 3000) -> None:
    """Make the Codespace's port public, as Ports tab → right-click → Port Visibility → Public does."""
    codespace = os.environ.get("CODESPACE_NAME")
    if not codespace:
        print("Not in a Codespace, so there's no port visibility to change.")
        return
    manual = (f"  Do it by hand instead: Ports tab → right-click port {port} → Port Visibility → Public.")
    try:
        proc = subprocess.run(["gh", "codespace", "ports", "visibility", f"{port}:public", "-c", codespace],
                              capture_output=True, text=True)
    except FileNotFoundError:
        print("✗ The GitHub CLI (gh) isn't installed in this Codespace.\n" + manual)
        return
    if proc.returncode != 0:
        print(f"✗ Couldn't make port {port} public: {(proc.stderr or proc.stdout).strip()}\n" + manual)
        return
    print(f"✓ Port {port} is public: anyone with the link can now use your API.")


def check_qgis(layers: list[dict]) -> None:
    """Check that the API can be reached from outside the Codespace, as QGIS
    reaches it, and print the vector tile URLs in the form QGIS wants."""
    base = public_url(3000)
    status, content_type, _ = _fetch_as_qgis(f"{base}/collections?f=json")
    if status != 200 or content_type != "application/json":
        print(f"✗ Asked for {base}/collections?f=json and got {status} ({content_type}), not JSON.")
        if status in (301, 302, 303, 307, 308, 401, 403) or content_type == "text/html":
            print("  Port 3000 is still private, so GitHub asks for a sign-in that QGIS can't do.\n"
                  "  Ports tab → right-click port 3000 → Port Visibility → Public, then run this cell again.")
        else:
            print("  Is the API running? Run the 'Start your API' step again.")
        return
    print("✓ Your API can be reached from outside the Codespace.")
    rows = [("The API (OGC API – Features in QGIS or ArcGIS)", base)]

    resources = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))["resources"]
    tiled = [l for l in layers
             if any(p["type"] == "tile" for p in resources.get(l["id"], {}).get("providers", []))]
    for l in tiled:
        # Fetch two real tiles holding the collection's first feature: the
        # whole-world one at zoom 0, and one at zoom 5
        coords = get_json(f"/collections/{l['id']}/items", {"limit": 1})["features"][0]["geometry"]["coordinates"]
        while isinstance(coords[0], list):
            coords = coords[0]
        results = []
        for z in (0, 5):
            col, row = _tile_at(coords[0], coords[1], z)
            status, content_type, body = _fetch_as_qgis(
                f"{base}/collections/{l['id']}/tiles/WebMercatorQuad/{z}/{row}/{col}?f=mvt")
            ok = status == 200 and content_type == "application/vnd.mapbox-vector-tile"
            results.append(f"zoom {z} ✓ ({len(body) / 1024:.0f} KB)" if ok else f"zoom {z} ✗ ({status}, {content_type})")
        print(f"  {l['id']}: test tiles " + ", ".join(results))
        rows.append((f"<code>{l['id']}</code>: vector tiles for QGIS",
                     f"{base}/collections/{l['id']}/tiles/WebMercatorQuad/{{z}}/{{y}}/{{x}}?f=mvt"))
    show_urls(rows, openable=False)


# ------------------------------------------------------------------
# Part 3 (part3-why-ogc-api.ipynb)
# ------------------------------------------------------------------
_measurements: dict[str, dict] = {}


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n > 1024 * 1024 else f"{n / 1024:.1f} KB" if n > 1024 else f"{n} bytes"


def fetch(url: str, label: str, data: bytes | None = None, content_type: str | None = None) -> bytes:
    """GET a URL (or POST `data` to it) and return the raw answer. Counts the
    requests, bytes and time under `label`, for compare()."""
    headers = {"Content-Type": content_type} if content_type else {}
    started = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=300) as res:
        body, answer_type = res.read(), res.headers.get_content_type()
    seconds = time.time() - started
    m = _measurements.setdefault(label, {"requests": 0, "bytes": 0, "seconds": 0.0, "formats": []})
    m["requests"] += 1
    m["bytes"] += len(body)
    m["seconds"] += seconds
    if answer_type not in m["formats"]:
        m["formats"].append(answer_type)
    print(f"→ {answer_type}, {_size(len(body))} in {seconds:.1f} s")
    return body


def compare() -> None:
    """A table of what each way of answering the question cost, from fetch()'s counts."""
    from IPython.display import HTML, display

    most = max(m["bytes"] for m in _measurements.values())
    rows = "".join(
        f"<tr><td><b>{label}</b></td><td>{m['requests']}</td><td>{', '.join(m['formats'])}</td>"
        f"<td style='text-align:right'>{_size(m['bytes'])}</td>"
        f"<td style='width:40%'><div style='background:#2f7d5b;height:12px;"
        f"width:{max(m['bytes'] / most * 100, 0.3):.1f}%'></div></td></tr>"
        for label, m in _measurements.items()
    )
    display(HTML("<table><tr><th></th><th>Requests</th><th>Answer as</th><th>Downloaded</th><th></th></tr>"
                 f"{rows}</table>"))


MAPLIBRE = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl"


def show_map(style_layers: list[dict], center=(10.5, 60.5), zoom: float = 6) -> None:
    """Draw the API's vector tiles in the notebook with MapLibre, styled by `style_layers`.

    Every collection with tiles is a source named after its id, so a style layer
    says e.g. "source": "kommuner", "source-layer": "kommuner" (the layer name
    inside the tiles is the table name). The map loads the tiles from the public
    address, so port 3000 must be public.
    """
    from IPython.display import HTML, display

    base = public_url(3000)
    status, content_type, _ = _fetch_as_qgis(f"{base}/collections?f=json")
    if status != 200 or content_type != "application/json":
        print("⚠ The map can't load your tiles while port 3000 is private. Run make_public(), or: "
              "Ports tab → right-click port 3000 → Port Visibility → Public, then run this cell again.")

    resources = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))["resources"]
    sources = {
        rid: {
            "type": "vector", "maxzoom": 18,
            "tiles": [f"{base}/collections/{rid}/tiles/WebMercatorQuad/{{z}}/{{y}}/{{x}}?f=mvt"],
        }
        for rid, r in resources.items()
        if any(p["type"] == "tile" for p in r.get("providers", []))
    }
    style = {
        "version": 8,
        "glyphs": "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",  # fonts for labels
        "sources": {
            "basemap": {
                "type": "raster", "tileSize": 256, "attribution": "&copy; Kartverket",
                "tiles": ["https://cache.kartverket.no/v1/wmts/1.0.0/topograatone/default/webmercator/{z}/{y}/{x}.png"],
            },
            **sources,
        },
        "layers": [{"id": "basemap", "type": "raster", "source": "basemap"}, *style_layers],
    }
    container = f"map-{time.time_ns()}"  # each map in the notebook needs its own element
    display(HTML(f"""<div id="{container}" style="height: 520px"></div><script>
(function () {{
  // VS Code puts outputs in shadow roots, where document.getElementById can't
  // see them, so look inside those too
  function find(root) {{
    const hit = root.getElementById("{container}");
    if (hit) return hit;
    for (const node of root.querySelectorAll("*")) {{
      const inner = node.shadowRoot && find(node.shadowRoot);
      if (inner) return inner;
    }}
    return null;
  }}
  const el = find(document);
  // The stylesheet goes next to the map, so it applies inside a shadow root too
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "{MAPLIBRE}.css";
  el.parentNode.insertBefore(css, el);

  function draw() {{
    const map = new maplibregl.Map({{container: el, style: {json.dumps(style)},
                                     center: {json.dumps(list(center))}, zoom: {zoom}}});
    map.addControl(new maplibregl.NavigationControl());
    // Click a feature to see what the tile holds about it
    map.on("click", e => {{
      const f = map.queryRenderedFeatures(e.point).find(f => f.source !== "basemap");
      if (f) new maplibregl.Popup().setLngLat(e.lngLat)
        .setHTML("<pre>" + JSON.stringify(f.properties, null, 1) + "</pre>").addTo(map);
    }});
  }}
  // Load MapLibre once (another map may already be loading it), then draw
  if (window.maplibregl) return draw();
  if (window.maplibreWaiting) return window.maplibreWaiting.push(draw);
  window.maplibreWaiting = [draw];
  const js = document.createElement("script");
  js.src = "{MAPLIBRE}.js";
  js.onload = () => window.maplibreWaiting.forEach(f => f());
  document.head.appendChild(js);
}})();
</script>"""))
