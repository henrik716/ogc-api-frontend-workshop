FROM ghcr.io/kartverket/datadeling-ogcapi-pygeoapi:pygeoapi-v0.5.1@sha256:adb32dc7ad6d8c3d7c7198f19c10b1ae85ea07158f8df2224634dde5bd7e4e1b

USER 150

COPY config/pygeoapi-config.yml /pygeoapi/pygeoapi-config.yml
# The GeoPackage(s) the config points at (source: /data/...)
COPY data/ /data/
# pygeoapi's vector tile provider, with a fix for the most zoomed-out tiles
# (see "OGC API workshop patch" in the file). Written for pygeoapi 0.24.0, the
# version in the image above: compare it with upstream before updating the image.
COPY backend/mvt_postgresql.py /pygeoapi/pygeoapi/provider/mvt_postgresql.py
