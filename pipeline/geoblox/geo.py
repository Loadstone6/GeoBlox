"""Coordinate transforms and raster helpers."""

from __future__ import annotations

import numpy as np
import pyproj
from pyproj.transformer import TransformerGroup

from . import fetch
from .config import PARAMS, Region


class Grid:
    """A north-up raster with 1 m (or `res`) cells: row 0 = northern edge."""

    def __init__(self, data: np.ndarray, e_min: float, n_max: float, res: float = 1.0):
        self.data = data
        self.e_min = e_min
        self.n_max = n_max
        self.res = res

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape  # type: ignore[return-value]

    def to_rc(self, e: np.ndarray | float, n: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
        """Continuous (row, col) of points; cell centres sit at integer + 0.5."""
        col = (np.asarray(e) - self.e_min) / self.res
        row = (self.n_max - np.asarray(n)) / self.res
        return row, col

    def cell_center(self, row: np.ndarray, col: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.e_min + (col + 0.5) * self.res, self.n_max - (row + 0.5) * self.res

    def sample(self, e: np.ndarray | float, n: np.ndarray | float) -> np.ndarray:
        """Bilinear interpolation on cell centres (NaN-aware: falls back to nearest)."""
        row, col = self.to_rc(e, n)
        r = np.asarray(row, dtype=np.float64) - 0.5
        c = np.asarray(col, dtype=np.float64) - 0.5
        h, w = self.shape
        r0 = np.clip(np.floor(r).astype(np.int64), 0, h - 2)
        c0 = np.clip(np.floor(c).astype(np.int64), 0, w - 2)
        fr = np.clip(r - r0, 0.0, 1.0)
        fc = np.clip(c - c0, 0.0, 1.0)
        d = self.data
        v00, v01 = d[r0, c0], d[r0, c0 + 1]
        v10, v11 = d[r0 + 1, c0], d[r0 + 1, c0 + 1]
        out = (v00 * (1 - fr) * (1 - fc) + v01 * (1 - fr) * fc + v10 * fr * (1 - fc) + v11 * fr * fc)
        bad = np.isnan(out)
        if np.any(bad):
            rn = np.clip(np.rint(r).astype(np.int64), 0, h - 1)
            cn = np.clip(np.rint(c).astype(np.int64), 0, w - 1)
            out = np.where(bad, d[rn, cn], out)
        return out


class Projector:
    """WGS84 lon/lat <-> BNG, using OSTN15 when available (~1 cm accuracy)."""

    def __init__(self) -> None:
        grid_dir = fetch.ostn15_grid()
        pyproj.datadir.append_data_dir(str(grid_dir))
        group = TransformerGroup("EPSG:4326", "EPSG:27700", always_xy=True)
        self.fwd = None
        for t in group.transformers:
            if "OSTN15" in t.description or "OSTN15" in str(t.definition):
                self.fwd = t
                break
        if self.fwd is None:
            self.fwd = group.transformers[0]
        self.description = self.fwd.description
        inv_group = TransformerGroup("EPSG:27700", "EPSG:4326", always_xy=True)
        self.inv = next(
            (t for t in inv_group.transformers if "OSTN15" in t.description or "OSTN15" in str(t.definition)),
            inv_group.transformers[0],
        )

    def to_bng(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.fwd.transform(lon, lat)

    def to_wgs(self, e: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.inv.transform(e, n)

    def wgs_bbox(self, region: Region) -> tuple[float, float, float, float]:
        e0, n0, e1, n1 = region.fetch_bbox
        es = np.array([e0, e1, e0, e1])
        ns = np.array([n0, n0, n1, n1])
        lon, lat = self.to_wgs(es, ns)
        return (float(lat.min()), float(lon.min()), float(lat.max()), float(lon.max()))


def to_studs(region: Region, e: np.ndarray, n: np.ndarray, h: np.ndarray) -> np.ndarray:
    """BNG metres -> Roblox studs. +X = east, +Z = south (so north is -Z), +Y = up."""
    e0, n0 = region.origin
    mps = PARAMS.metres_per_stud
    return np.stack([(e - e0) / mps, (h - PARAMS.h0) / mps, -(n - n0) / mps], axis=-1)


def fill_nodata(a: np.ndarray, iterations: int = 400) -> np.ndarray:
    """Harmonic (Laplace) inpainting of NaN holes by Jacobi relaxation.

    Interior of a hole converges to the solution of ∇²z = 0 with the hole's rim
    as Dirichlet boundary — the smoothest surface that matches the data.
    """
    out = a.copy()
    mask = np.isnan(out)
    if not mask.any():
        return out
    # Seed holes with a coarse nearest fill so relaxation converges quickly.
    from scipy import ndimage

    idx = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
    out[mask] = out[tuple(idx)][mask]
    for _ in range(iterations):
        avg = 0.25 * (
            np.roll(out, 1, 0) + np.roll(out, -1, 0) + np.roll(out, 1, 1) + np.roll(out, -1, 1)
        )
        out[mask] = avg[mask]
    return out
