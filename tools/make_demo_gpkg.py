"""
Generates data/demo.gpkg — the placeholder dataset for the workshop.

Three layers, stored in EPSG:25833 (UTM 33N) like Kartverket's own data, so
the API has to reproject for the map:

  fylker    15 counties     (polygons)  from Kartverket's administrative units
  kommuner  357 municipalities (polygons)  ditto
  byer      25 towns        (points)    hand-placed, linked to their municipality

The county and municipality boundaries come from the PostGIS dumps Kartverket
publishes with its OGC API reference setup. They're simplified here (with
borders between neighbours kept in step) so the file stays small.

Written with the standard library's sqlite3 plus shapely and pyproj, so no
GDAL install is needed. Re-run to regenerate:

    pip install shapely pyproj
    python3 tools/make_demo_gpkg.py
"""

import io
import re
import sqlite3
import struct
import urllib.request
import zipfile
from pathlib import Path

import shapely
from pyproj import CRS, Transformer

OUT = Path(__file__).resolve().parent.parent / "data" / "demo.gpkg"
EPSG = 25833
SIMPLIFY_TOLERANCE = 150  # metres; coverage_simplify keeps shared borders aligned

DUMPS = {
    "fylker": "https://raw.githubusercontent.com/kartverket/ogc-api-starter/main/postgis/dumps/fylker.zip",
    "kommuner": "https://raw.githubusercontent.com/kartverket/OGC-API-frontend/main/dev/postgis/dumps/kommuner.zip",
}

# (navn, lon, lat) — approximate town-centre positions
TOWNS = [
    ("Oslo", 10.7522, 59.9139), ("Bergen", 5.3221, 60.3913), ("Trondheim", 10.3951, 63.4305),
    ("Stavanger", 5.7331, 58.9700), ("Kristiansand", 7.9956, 58.1599), ("Tromsø", 18.9553, 69.6492),
    ("Bodø", 14.4049, 67.2804), ("Ålesund", 6.1549, 62.4722), ("Drammen", 10.2045, 59.7439),
    ("Fredrikstad", 10.9298, 59.2181), ("Lillehammer", 10.4662, 61.1153), ("Hamar", 11.0680, 60.7945),
    ("Gjøvik", 10.6912, 60.7957), ("Molde", 7.1600, 62.7375), ("Alta", 23.2717, 69.9689),
    ("Kirkenes", 30.0450, 69.7271), ("Hammerfest", 23.6821, 70.6634), ("Narvik", 17.4272, 68.4385),
    ("Harstad", 16.5418, 68.7983), ("Arendal", 8.7725, 58.4617), ("Tønsberg", 10.4076, 59.2676),
    ("Skien", 9.6090, 59.2096), ("Haugesund", 5.2680, 59.4138), ("Førde", 5.8510, 61.4522),
    ("Steinkjer", 11.4954, 64.0149),
]


# ------------------------------------------------------------------
# Reading the dumps (ogr2ogr PGDump output: INSERTs with hex EWKB)
# ------------------------------------------------------------------
INSERT = re.compile(r"VALUES \('([0-9A-F]+)', '[^']*', '((?:[^']|'')*)', '([0-9]+)'")


def read_dump(url: str) -> list[tuple]:
    print(f"Downloading {url}")
    raw = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(url).read()))
    data = raw.read(raw.namelist()[0])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")  # the dumps aren't consistently UTF-8
    rows = []
    for m in INSERT.finditer(text):
        geom = shapely.from_wkb(bytes.fromhex(m.group(1)))  # EWKB; SRID is dropped
        rows.append((m.group(3), m.group(2).replace("''", "'"), geom))
    return rows  # (nr, navn, geometry)


# ------------------------------------------------------------------
# Writing the GeoPackage
# ------------------------------------------------------------------
def gpkg_geom(geom, srs_id: int) -> bytes:
    # GeoPackage binary header: magic "GP", version 0, flags 0x01 (little-endian,
    # no envelope), srs_id — followed by plain (ISO) little-endian WKB.
    wkb = shapely.to_wkb(geom, byte_order=1, output_dimension=2, include_srid=False)
    return b"GP" + bytes([0, 0x01]) + struct.pack("<i", srs_id) + wkb


def create_gpkg(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA application_id = 0x47504B47")  # "GPKG"
    con.execute("PRAGMA user_version = 10400")  # GeoPackage 1.4
    con.executescript(
        """
        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
        CREATE TABLE gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
            description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (
            table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
        """
    )
    con.executemany(
        "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", None),
            ("Undefined geographic SRS", 0, "NONE", 0, "undefined", None),
            ("WGS 84 geodetic", 4326, "EPSG", 4326, CRS.from_epsg(4326).to_wkt("WKT1_GDAL"), None),
            ("ETRS89 / UTM zone 33N", EPSG, "EPSG", EPSG, CRS.from_epsg(EPSG).to_wkt("WKT1_GDAL"), None),
        ],
    )


def add_layer(con, name: str, geom_type: str, columns: dict[str, str], rows: list[tuple], description: str) -> None:
    """rows: (geometry, *values in `columns` order)"""
    cols = ", ".join(f"{c} {t}" for c, t in columns.items())
    con.execute(f"CREATE TABLE {name} (fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, geom {geom_type}, {cols})")
    minx, miny, maxx, maxy = shapely.total_bounds([r[0] for r in rows])
    con.execute(
        "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, min_x, min_y, max_x, max_y, srs_id) "
        "VALUES (?, 'features', ?, ?, ?, ?, ?, ?, ?)",
        (name, name, description, minx, miny, maxx, maxy, EPSG),
    )
    con.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, 0, 0)", (name, geom_type, EPSG))
    placeholders = ", ".join("?" * (len(columns) + 1))
    con.executemany(
        f"INSERT INTO {name} (geom, {', '.join(columns)}) VALUES ({placeholders})",
        [(gpkg_geom(r[0], EPSG), *r[1:]) for r in rows],
    )
    print(f"  {name}: {len(rows)} features")


def as_multipolygons(geoms):
    return [g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]) for g in geoms]


def main() -> None:
    fylker = sorted(read_dump(DUMPS["fylker"]))
    kommuner = sorted(read_dump(DUMPS["kommuner"]))
    fylke_navn = {nr: navn for nr, navn, _ in fylker}

    # Towns → their municipality, using the full-detail boundaries
    to_utm = Transformer.from_crs(4326, EPSG, always_xy=True)
    town_rows = []
    for navn, lon, lat in TOWNS:
        pt = shapely.Point(*to_utm.transform(lon, lat))
        knr, knavn, _ = next(k for k in kommuner if k[2].contains(pt))
        town_rows.append((pt, navn, knr, knavn, fylke_navn[knr[:2]]))

    # Simplify each layer as a coverage, so neighbouring borders stay identical
    f_geoms = as_multipolygons(shapely.coverage_simplify([g for *_, g in fylker], SIMPLIFY_TOLERANCE))
    k_geoms = as_multipolygons(shapely.coverage_simplify([g for *_, g in kommuner], SIMPLIFY_TOLERANCE))

    antall = {nr: sum(k[0][:2] == nr for k in kommuner) for nr, *_ in fylker}
    km2 = lambda g: round(g.area / 1e6, 1)  # area from the full-detail geometry

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.unlink(missing_ok=True)
    con = sqlite3.connect(OUT)
    create_gpkg(con)
    print(f"Writing {OUT}")
    add_layer(
        con, "fylker", "MULTIPOLYGON",
        {"fylkesnummer": "TEXT", "navn": "TEXT", "antall_kommuner": "INTEGER", "areal_km2": "REAL"},
        [(g, nr, navn, antall[nr], km2(orig)) for (nr, navn, orig), g in zip(fylker, f_geoms)],
        "Norges fylker (forenklet)",
    )
    add_layer(
        con, "kommuner", "MULTIPOLYGON",
        {"kommunenummer": "TEXT", "navn": "TEXT", "fylkesnummer": "TEXT", "fylke": "TEXT", "areal_km2": "REAL"},
        [(g, nr, navn, nr[:2], fylke_navn[nr[:2]], km2(orig)) for (nr, navn, orig), g in zip(kommuner, k_geoms)],
        "Norges kommuner (forenklet)",
    )
    add_layer(
        con, "byer", "POINT",
        {"navn": "TEXT", "kommunenummer": "TEXT", "kommune": "TEXT", "fylke": "TEXT"},
        town_rows,
        "Et utvalg norske byer",
    )
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"Done: {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
