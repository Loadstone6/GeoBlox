"""Data acquisition with on-disk caching.

* Environment Agency LiDAR Composite (2022), 1 m DTM and 1 m first-return DSM,
  via the Defra OGC WCS 2.0.1 endpoints (raw float32 GeoTIFF, EPSG:27700).
* OpenStreetMap via the Overpass API.
* OSTN15 grid for centimetre-accurate WGS84 -> OSGB36 transformation.
"""

from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import tifffile

from .config import CACHE, Region

WCS = {
    "dtm": (
        "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m-2022/wcs",
        "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
    ),
    "dsm": (
        "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-first-return-dsm-1m/wcs",
        "df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m",
    ),
}

OVERPASS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

OSTN15_URL = "https://cdn.proj.org/uk_os_OSTN15_NTv2_OSGBtoETRS.tif"

USER_AGENT = "GeoBlox-pipeline/0.2 (open-data city reconstruction research)"


def _get(url: str, data: bytes | None = None, timeout: float = 180.0, tries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # network hiccups: back off and retry
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"download failed: {url}: {last}")


def _wcs_tile(kind: str, e0: float, n0: float, e1: float, n1: float) -> np.ndarray:
    base, coverage = WCS[kind]
    query = urllib.parse.urlencode(
        [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("request", "GetCoverage"),
            ("CoverageId", coverage),
            ("subset", f"E({e0:.0f},{e1:.0f})"),
            ("subset", f"N({n0:.0f},{n1:.0f})"),
            ("format", "image/tiff"),
        ]
    )
    raw = _get(f"{base}?{query}")
    if not raw.startswith((b"II", b"MM")):
        raise RuntimeError(f"WCS returned non-TIFF for {kind}: {raw[:200]!r}")
    arr = tifffile.imread(io.BytesIO(raw)).astype(np.float32)
    expected = (int(round(n1 - n0)), int(round(e1 - e0)))
    if arr.shape != expected:
        raise RuntimeError(f"unexpected WCS tile shape {arr.shape} != {expected}")
    return arr


def lidar(region: Region, kind: str, tile: int = 500) -> tuple[np.ndarray, tuple[float, float]]:
    """Return (raster, (e_min, n_max)) — row 0 is the northern edge, 1 m cells.

    No-data cells are NaN.
    """
    e0, n0, e1, n1 = region.fetch_bbox
    path = CACHE / f"{region.name}_{kind}.npy"
    if path.exists():
        return np.load(path), (e0, n1)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = int(e1 - e0), int(n1 - n0)
    out = np.full((height, width), np.nan, dtype=np.float32)
    for te in range(0, width, tile):
        for tn in range(0, height, tile):
            te1, tn1 = min(width, te + tile), min(height, tn + tile)
            # Raster rows count down from the north edge.
            arr = _wcs_tile(kind, e0 + te, n1 - tn1, e0 + te1, n1 - tn)
            out[tn:tn1, te:te1] = arr
            print(f"  lidar {kind}: tile e{te} n{tn} ok")
    out[(out < -100) | (out > 2000)] = np.nan  # nodata sentinels
    np.save(path, out)
    return out, (e0, n1)


def overpass(region: Region, wgs_bbox: tuple[float, float, float, float]) -> dict:
    """Everything needed for the city model inside the bbox (south, west, north, east)."""
    path = CACHE / f"{region.name}_osm.json"
    if path.exists():
        return json.loads(path.read_text())
    s, w, n, e = wgs_bbox
    bb = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    query = f"""
[out:json][timeout:240][maxsize:536870912];
(
  way["building"]({bb});
  relation["building"]({bb});
  way["building:part"]({bb});
  relation["building:part"]({bb});
  way["highway"]({bb});
  way["area:highway"]({bb});
  way["railway"]({bb});
  way["man_made"]({bb});
  relation["man_made"]({bb});
  way["bridge"]({bb});
  way["landuse"]({bb});
  relation["landuse"]({bb});
  way["leisure"]({bb});
  relation["leisure"]({bb});
  way["natural"]({bb});
  way["amenity"]({bb});
  way["tourism"]({bb});
  way["attraction"]({bb});
  way["barrier"]({bb});
  way["waterway"]({bb});
  way["place"]({bb});
  node({bb})["highway"];
  node({bb})["amenity"];
  node({bb})["natural"="tree"];
  node({bb})["historic"];
  node({bb})["tourism"];
  node({bb})["man_made"];
  node({bb})["barrier"];
  node({bb})["leisure"];
  node({bb})["emergency"];
  node({bb})["advertising"];
  node({bb})["railway"];
);
out body;
>;
out skel qt;
"""
    data = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for url in OVERPASS:
        try:
            raw = _get(url, data=data, timeout=300, tries=2)
            obj = json.loads(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(obj))
            return obj
        except Exception as exc:
            last = exc
            print(f"  overpass {url} failed: {exc}")
    raise RuntimeError(f"all overpass endpoints failed: {last}")


def thames(region: Region, wgs_bbox: tuple[float, float, float, float]) -> dict:
    """Water multipolygons (the Thames is one huge relation) restricted to the bbox."""
    path = CACHE / f"{region.name}_water.json"
    if path.exists():
        return json.loads(path.read_text())
    s, w, n, e = wgs_bbox
    bb = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    query = f"""
[out:json][timeout:240];
(
  relation["natural"="water"]({bb});
  way["natural"="water"]({bb});
);
out geom({bb});
"""
    data = urllib.parse.urlencode({"data": query}).encode()
    raw = _get(OVERPASS[0], data=data, timeout=300, tries=3)
    obj = json.loads(raw)
    path.write_text(json.dumps(obj))
    return obj


def ostn15_grid() -> Path:
    """Download the OSTN15 NTv2 grid once and return its directory for PROJ."""
    grid_dir = CACHE / "proj"
    path = grid_dir / "uk_os_OSTN15_NTv2_OSGBtoETRS.tif"
    if not path.exists():
        grid_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(OSTN15_URL))
    return grid_dir
