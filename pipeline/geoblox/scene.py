"""Scene: loads every input once and exposes shared lookups."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon, box

from . import fetch, geo, osm
from .config import CACHE, PARAMS, Region

warnings.filterwarnings("ignore", message=".*GDAL_NODATA.*")


@dataclass
class Scene:
    region: Region
    proj: geo.Projector
    dtm: geo.Grid
    dsm: geo.Grid
    osm: osm.OsmData
    water_raw: list[osm.Feature]
    clip: Polygon  # region bbox polygon (BNG)
    fetch_clip: Polygon
    water_level: float = PARAMS.thames_level

    @property
    def ndsm(self) -> np.ndarray:
        return self.dsm.data - self.dtm.data


def load(region: Region) -> Scene:
    proj = geo.Projector()
    dtm_arr, (e_min, n_max) = fetch.lidar(region, "dtm")
    dsm_arr, _ = fetch.lidar(region, "dsm")
    dtm_arr = geo.fill_nodata(dtm_arr)
    dsm_arr = geo.fill_nodata(dsm_arr)
    # First-return DSM has occasional deep negative spikes over water (specular
    # dropouts): never let the surface go below the terrain.
    dsm_arr = np.maximum(dsm_arr, dtm_arr)
    wgs = proj.wgs_bbox(region)
    data = osm.parse(fetch.overpass(region, wgs), proj)
    clip = box(*region.bbox)
    fclip = box(*region.fetch_bbox)
    water_obj = fetch.thames(region, wgs)
    thames_ways = json.loads((CACHE / f"{region.name}_thames_ways.json").read_text())
    water = osm.water_from_overpass_geom(water_obj, thames_ways, proj, fclip)
    scene = Scene(
        region=region,
        proj=proj,
        dtm=geo.Grid(dtm_arr, e_min, n_max),
        dsm=geo.Grid(dsm_arr, e_min, n_max),
        osm=data,
        water_raw=water,
        clip=clip,
        fetch_clip=fclip,
    )
    return scene


def raster_cells_in(grid: geo.Grid, poly, shrink: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(rows, cols) of cells whose centres lie inside poly (optionally eroded)."""
    import shapely

    g = poly.buffer(-shrink) if shrink > 0 else poly
    if g.is_empty:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    e0, n0, e1, n1 = g.bounds
    r_lo = max(0, int(np.floor(grid.n_max - n1)))
    r_hi = min(grid.shape[0] - 1, int(np.ceil(grid.n_max - n0)))
    c_lo = max(0, int(np.floor(e0 - grid.e_min)))
    c_hi = min(grid.shape[1] - 1, int(np.ceil(e1 - grid.e_min)))
    if r_hi < r_lo or c_hi < c_lo:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    rr, cc = np.mgrid[r_lo : r_hi + 1, c_lo : c_hi + 1]
    rr, cc = rr.ravel(), cc.ravel()
    x, y = grid.cell_center(rr, cc)
    inside = shapely.contains_xy(g, x, y)
    return rr[inside], cc[inside]
