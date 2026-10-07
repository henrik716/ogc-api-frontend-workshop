"""
Generates data/demo.gpkg — the placeholder dataset for the workshop.

One point layer ("byer") of Norwegian towns, stored in EPSG:25833 (UTM 33N),
the same storage CRS Kartverket uses, so the API has to reproject to WGS84 for
the map — just like it would for real Kartverket data.

Written with the standard library's sqlite3 (plus pyproj for the coordinates),
so no GDAL install is needed. Re-run to regenerate:

    python3 tools/make_demo_gpkg.py
"""

import sqlite3
import struct
from pathlib import Path

from pyproj import CRS, Transformer

OUT = Path(__file__).resolve().parent.parent / "data" / "demo.gpkg"
EPSG = 25833
LAYER = "byer"

# (navn, kommune, fylke, lon, lat) — approximate town-centre positions, demo use only
TOWNS = [
    ("Oslo", "Oslo", "Oslo", 10.7522, 59.9139),
    ("Bergen", "Bergen", "Vestland", 5.3221, 60.3913),
    ("Trondheim", "Trondheim", "Trøndelag", 10.3951, 63.4305),
    ("Stavanger", "Stavanger", "Rogaland", 5.7331, 58.9700),
    ("Kristiansand", "Kristiansand", "Agder", 7.9956, 58.1599),
    ("Tromsø", "Tromsø", "Troms", 18.9553, 69.6492),
    ("Bodø", "Bodø", "Nordland", 14.4049, 67.2804),
    ("Ålesund", "Ålesund", "Møre og Romsdal", 6.1549, 62.4722),
    ("Drammen", "Drammen", "Buskerud", 10.2045, 59.7439),
    ("Fredrikstad", "Fredrikstad", "Østfold", 10.9298, 59.2181),
    ("Lillehammer", "Lillehammer", "Innlandet", 10.4662, 61.1153),
    ("Hamar", "Hamar", "Innlandet", 11.0680, 60.7945),
    ("Gjøvik", "Gjøvik", "Innlandet", 10.6912, 60.7957),
    ("Molde", "Molde", "Møre og Romsdal", 7.1600, 62.7375),
    ("Alta", "Alta", "Finnmark", 23.2717, 69.9689),
    ("Kirkenes", "Sør-Varanger", "Finnmark", 30.0450, 69.7271),
    ("Hammerfest", "Hammerfest", "Finnmark", 23.6821, 70.6634),
    ("Narvik", "Narvik", "Nordland", 17.4272, 68.4385),
    ("Harstad", "Harstad", "Troms", 16.5418, 68.7983),
    ("Arendal", "Arendal", "Agder", 8.7725, 58.4617),
    ("Tønsberg", "Tønsberg", "Vestfold", 10.4076, 59.2676),
    ("Skien", "Skien", "Telemark", 9.6090, 59.2096),
    ("Haugesund", "Haugesund", "Rogaland", 5.2680, 59.4138),
    ("Førde", "Sunnfjord", "Vestland", 5.8510, 61.4522),
    ("Steinkjer", "Steinkjer", "Trøndelag", 11.4954, 64.0149),
]


def gpkg_point(x: float, y: float, srs_id: int) -> bytes:
    # GeoPackage binary header: magic "GP", version 0, flags 0x01 (little-endian,
    # no envelope), srs_id — followed by a plain little-endian WKB point.
    return b"GP" + bytes([0, 0x01]) + struct.pack("<i", srs_id) + struct.pack("<BIdd", 1, 1, x, y)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.unlink(missing_ok=True)

    to_utm = Transformer.from_crs(4326, EPSG, always_xy=True)
    points = [(*t[:3], *to_utm.transform(t[3], t[4])) for t in TOWNS]
    xs, ys = [p[3] for p in points], [p[4] for p in points]

    con = sqlite3.connect(OUT)
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
    con.execute(
        f"CREATE TABLE {LAYER} (fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
        "geom POINT, navn TEXT, kommune TEXT, fylke TEXT)"
    )
    con.execute(
        "INSERT INTO gpkg_contents (table_name, data_type, identifier, min_x, min_y, max_x, max_y, srs_id) "
        "VALUES (?, 'features', ?, ?, ?, ?, ?, ?)",
        (LAYER, LAYER, min(xs), min(ys), max(xs), max(ys), EPSG),
    )
    con.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', 'POINT', ?, 0, 0)", (LAYER, EPSG))
    con.executemany(
        f"INSERT INTO {LAYER} (geom, navn, kommune, fylke) VALUES (?, ?, ?, ?)",
        [(gpkg_point(x, y, EPSG), navn, kommune, fylke) for navn, kommune, fylke, x, y in points],
    )
    con.commit()
    con.close()
    print(f"Wrote {OUT} ({len(points)} features in layer '{LAYER}', EPSG:{EPSG})")


if __name__ == "__main__":
    main()
