"""The drivable ground: height fields, Roblox terrain columns, kerbs.

Roblox terrain is the street surface. It has zero part cost, junctions and
odd-shaped carriageways come for free, and it streams and collides like
any terrain. Heisters, for one, builds its roads this way. The heights come
from the EA bare-earth DTM:

  road      normalised convolution (Gaussian, sigma 1.5 m) of the DTM over
            carriageway cells at least 0.8 m from the kerb, so kerbs and
            parked-car edges in the mixed pixels do not leak in. It is
            smooth enough to drive at speed and keeps real gradients
            (Bridge Street rising to Westminster Bridge).
  pavement  kerb height above the road near the kerb, blending over 3 m into
            the measured pavement surface.
  ground    lightly smoothed DTM elsewhere (parks, squares).

Terrain voxels are 4 studs (1.2 m), too coarse for a 12 cm kerb. The
kerb is therefore a granite part with a pavement-edge strip behind it.
Terrain columns within 1 m of the kerb on the pavement side stay at road
height, and the 2.4 m strip covers where the terrain rises to pavement
height, so no terrain lump ever sits on the road.
"""

from __future__ import annotations

import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.ops import unary_union
from PIL import Image, ImageDraw

from .config import PARAMS
from .geo import Grid
from .ground import MAT, Ground
from .osm import polygons
from .partmap import Look, Parts
from .scene import Scene

KERB = PARAMS.kerb_height
DIP = 1.0  # m of pavement-side terrain kept at road height
STRIP = 2.4  # m, pavement-edge strip behind the kerb (covers the dip ramp)
GRANITE = Look("Granite", (150, 150, 152), collide=True)
STRIP_LOOK = Look("Pavement", (166, 162, 154), collide=True)


def rasterize(geom, grid: Grid) -> np.ndarray:
    """Cells whose centre lies inside geom (PIL scanline fill)."""
    H, W = grid.shape
    if geom is None or geom.is_empty:
        return np.zeros((H, W), dtype=bool)
    img = Image.new("1", (W, H), 0)
    draw = ImageDraw.Draw(img)

    def px(ring):
        c = np.asarray(ring.coords)
        x = (c[:, 0] - grid.e_min) / grid.res
        y = (grid.n_max - c[:, 1]) / grid.res
        return list(zip(x.tolist(), y.tolist()))

    polys = polygons(geom)
    for poly in polys:
        draw.polygon(px(poly.exterior), fill=1)
    for poly in polys:
        for hole in poly.interiors:
            draw.polygon(px(hole), fill=0)
    return np.array(img, dtype=bool)


def rasterize_codes(items, grid: Grid, background: int = 0) -> np.ndarray:
    """uint8 raster: each (geom, code) drawn in order (later items win)."""
    H, W = grid.shape
    img = Image.new("L", (W, H), background)
    draw = ImageDraw.Draw(img)
    for geom, code in items:
        if geom is None or geom.is_empty:
            continue
        for poly in polygons(geom):
            c = np.asarray(poly.exterior.coords)
            draw.polygon(list(zip(((c[:, 0] - grid.e_min) / grid.res).tolist(), ((grid.n_max - c[:, 1]) / grid.res).tolist())), fill=int(code))
            for hole in poly.interiors:
                h = np.asarray(hole.coords)
                draw.polygon(list(zip(((h[:, 0] - grid.e_min) / grid.res).tolist(), ((grid.n_max - h[:, 1]) / grid.res).tolist())), fill=background)
    return np.array(img, dtype=np.uint8)


def normconv(values: np.ndarray, mask: np.ndarray, sigma: float, min_weight: float = 1e-3) -> np.ndarray:
    w = mask.astype(np.float64)
    v = np.where(mask, values, 0.0)
    num = ndimage.gaussian_filter(v, sigma, mode="nearest")
    den = ndimage.gaussian_filter(w, sigma, mode="nearest")
    out = np.full(values.shape, np.nan)
    ok = den > min_weight
    out[ok] = num[ok] / den[ok]
    return out


def bridge_approach_heights(scene: Scene, walkable: np.ndarray, decks: list, reach: float = 90.0) -> np.ndarray:
    """Bare-earth filtering removes raised bridge approaches (vaults and
    ramps count as structures), which puts the DTM metres below the real
    road. Near each river bridge, use a vehicle-free DSM surface instead
    where it is smooth (not trees) and above the DTM. The 5x5 minimum filter
    erases anything narrower than 5 m (cars, buses, people, lamp columns).
    Cells under the deck itself are excluded, since roads there pass beneath
    it."""
    g = scene.dtm
    dtm = g.data.astype(np.float64)
    out = dtm.copy()
    if not decks:
        return out, np.zeros(dtm.shape, dtype=bool)
    dsm = scene.dsm.data.astype(np.float64)
    # Minimum over street cells only, so river water or a basement light well
    # beside the road never pulls the estimate down.
    low = ndimage.minimum_filter(np.where(walkable, dsm, np.inf), size=5)
    rough = np.sqrt(np.maximum(ndimage.uniform_filter(dsm * dsm, 3) - ndimage.uniform_filter(dsm, 3) ** 2, 0.0))
    near = rasterize(unary_union([d.buffer(reach) for d in decks]), g)
    under = rasterize(unary_union(decks), g)
    use = walkable & near & ~under & np.isfinite(low) & (rough < 0.25) & (low > dtm + 0.3) & (low < dtm + 12.0)
    out[use] = low[use]
    return out, under


class Surface:
    def __init__(self, scene: Scene, ground: Ground, building_union, bridge_decks: list | None = None):
        g = scene.dtm
        self.scene = scene
        self.ground_model = ground
        self.car = rasterize(ground.carriageway, g)
        self.hard = rasterize(ground.hardscape, g)
        dtm, under_deck = bridge_approach_heights(scene, self.car | self.hard, bridge_decks or [])
        self.green = rasterize(ground.green, g) | rasterize(ground.paths, g)
        water = unary_union([w.poly for w in ground.water]) if ground.water else Polygon()
        self.water = rasterize(water, g)
        self.bld = rasterize(building_union, g)
        d_in = ndimage.distance_transform_edt(self.car) * g.res
        d_out = ndimage.distance_transform_edt(~self.car) * g.res
        self.d_out = d_out
        # Road cells under a river-bridge deck carry the DTM's interpolated
        # river bed, not a road: they take their height from their neighbours.
        core = self.car & (d_in > 0.8) & ~under_deck
        road = normconv(dtm, core, 1.5)
        # Robust refit: cells far from the local road surface are DTM pits
        # (interpolated holes beside abutments, basement wells), not road.
        for _ in range(2):
            keep = core & np.isfinite(road) & (np.abs(dtm - np.nan_to_num(road, nan=1e9)) < 0.5)
            road = normconv(dtm, keep, 1.5)
        core = keep
        road_ext = normconv(dtm, core, 4.0)
        road = np.where(np.isnan(road), road_ext, road)
        # Gradient limiter: no street here is steeper than ~20%, so cliffs
        # inside the carriageway (DTM artefacts at vaults and retaining walls)
        # are relaxed into ramps.
        for _ in range(4):
            gy, gx = np.gradient(np.nan_to_num(road, nan=0.0), g.res)
            steep = self.car & np.isfinite(road) & (np.hypot(gx, gy) > 0.2)
            if not steep.any():
                break
            steep = ndimage.binary_dilation(steep, iterations=3) & self.car
            wide = normconv(np.nan_to_num(road, nan=0.0), self.car & np.isfinite(road), 3.0)
            road = np.where(steep & np.isfinite(wide), wide, road)
        road_ext = np.where(self.car, road, road_ext)
        open_ground = ~self.bld & ~self.water
        meas = normconv(dtm, (self.hard | self.green) & (d_out > 1.2) & open_ground, 1.0)
        meas = np.where(np.isnan(meas), normconv(dtm, open_ground, 2.0), meas)
        kerbed = road_ext + KERB
        w = np.clip((d_out - STRIP) / 3.0, 0.0, 1.0)
        pave = np.where(np.isnan(kerbed), meas, kerbed + w * (meas - kerbed))
        pave = np.where(np.isnan(pave), dtm, pave)
        gnd = normconv(dtm, open_ground, 0.7)
        gnd = np.where(np.isnan(gnd), dtm, gnd)
        road_ext = np.where(np.isnan(road_ext), gnd, road_ext)
        self.road_f = Grid(road_ext, g.e_min, g.n_max, g.res)
        self.pave_f = Grid(pave, g.e_min, g.n_max, g.res)
        self.ground_f = Grid(gnd, g.e_min, g.n_max, g.res)
        self.dist_f = Grid(d_out, g.e_min, g.n_max, g.res)

    # Heights (metres ODN) at BNG points.
    def road(self, xy) -> np.ndarray:
        xy = np.atleast_2d(xy)
        return self.road_f.sample(xy[:, 0], xy[:, 1])

    def pave(self, xy) -> np.ndarray:
        xy = np.atleast_2d(xy)
        return self.pave_f.sample(xy[:, 0], xy[:, 1])

    def ground(self, xy) -> np.ndarray:
        xy = np.atleast_2d(xy)
        return self.ground_f.sample(xy[:, 0], xy[:, 1])

    def top(self, xy) -> np.ndarray:
        """Walkable surface at points: kerb-strip / pavement / road / ground."""
        xy = np.atleast_2d(xy)
        g = self.scene.dtm
        r, c = g.to_rc(xy[:, 0], xy[:, 1])
        ri = np.clip(r.astype(np.int64), 0, g.shape[0] - 1)
        ci = np.clip(c.astype(np.int64), 0, g.shape[1] - 1)
        out = self.ground(xy)
        hard = self.hard[ri, ci]
        out = np.where(hard, self.pave(xy), out)
        out = np.where(self.car[ri, ci], self.road(xy), out)
        return out

    # ------------------------------------------------------------ terrain columns
    def terrain_columns(self, extent_studs: tuple[float, float, float, float], decks: list | None = None) -> dict:
        """4-stud voxel columns: height (m ODN), material code and water level.
        `decks` [(polygon, deck_z(xy))]: terrain under a deck stays below it."""
        mps = PARAMS.metres_per_stud
        x0, z0, x1, z1 = extent_studs
        e0, n0 = self.scene.region.origin
        nx = int(round((x1 - x0) / 4))
        nz = int(round((z1 - z0) / 4))
        xs = x0 + (np.arange(nx) + 0.5) * 4
        zs = z0 + (np.arange(nz) + 0.5) * 4
        gx, gz = np.meshgrid(xs, zs, indexing="ij")
        E = (e0 + gx * mps).ravel()
        N = (n0 - gz * mps).ravel()
        g = self.scene.dtm
        r, c = g.to_rc(E, N)
        ri = np.clip(r.astype(np.int64), 0, g.shape[0] - 1)
        ci = np.clip(c.astype(np.int64), 0, g.shape[1] - 1)
        car, hard, green, bld = self.car[ri, ci], self.hard[ri, ci], self.green[ri, ci], self.bld[ri, ci]
        h = self.ground_f.sample(E, N)
        mat = np.full(len(E), MAT["pavement"], dtype=np.uint8)
        mat[green] = MAT["grass"]
        d = self.dist_f.sample(E, N)
        road_h = self.road_f.sample(E, N)
        pave_h = self.pave_f.sample(E, N)
        h = np.where(hard | bld, pave_h, h)
        dip = hard & (d < DIP)
        h = np.where(dip, road_h, h)
        h = np.where(car, road_h, h)
        mat[car] = MAT["asphalt"]
        # Park paths and surfaced areas keep their mapped materials.
        gm = self.ground_model
        items = [(gm.paths, MAT["asphalt"])] + [(poly, code) for poly, code in (gm.surfaces or [])]
        codes = rasterize_codes(items, g, 255)[ri, ci]
        m = (codes != 255) & ~car
        mat[m] = codes[m]
        for poly, zfn in decks or []:
            m = shapely.contains_xy(poly, E, N)
            if m.any():
                h[m] = np.minimum(h[m], zfn(np.column_stack([E[m], N[m]])) - 0.25)
        water = np.full(len(E), np.nan)
        wid = rasterize_codes([(wb.poly, k) for k, wb in enumerate(gm.water)], g, 255)[ri, ci]
        for k, wbody in enumerate(gm.water):
            m = wid == k
            water[m] = wbody.level
            h[m] = wbody.level - wbody.depth
            mat[m] = MAT["mud"]
        return {
            "x0": x0, "z0": z0, "nx": nx, "nz": nz,
            "height": h.reshape(nx, nz), "material": mat.reshape(nx, nz), "water": water.reshape(nx, nz),
        }

    # ------------------------------------------------------------ kerbs
    def kerb_parts(self, P: Parts) -> int:
        """Granite kerb plus pavement-edge strip along every kerb line."""
        car = self.ground_model.carriageway
        n = 0
        k = 0
        for line in self.ground_model.kerb_lines:
            pts = np.asarray(line.simplify(0.1).coords)
            for i in range(len(pts) - 1):
                p, q = pts[i], pts[i + 1]
                L = float(np.linalg.norm(q - p))
                if L < 0.05:
                    continue
                d = (q - p) / L
                nl = np.array([-d[1], d[0]])
                mid = (p + q) / 2
                to_pave = -nl if car.contains(shapely.Point(mid + nl * 0.3)) else nl
                ext = 0.04
                a, b = p - d * ext, q + d * ext
                za, zb = self.road(np.array([a, b])) + KERB
                if not (np.isfinite(za) and np.isfinite(zb)):
                    continue
                k += 1
                lift = (k % 3) * 0.004
                w_k = to_pave * 0.3
                corners = [np.array([*a, za + lift]), np.array([*b, zb + lift]), np.array([*(b + w_k), zb + lift]), np.array([*(a + w_k), za + lift])]
                up = _up(corners)
                P.rect(*corners, up, KERB + 0.35, GRANITE)
                s0, s1 = to_pave * 0.3, to_pave * STRIP
                strip = [np.array([*(a + s0), za + lift]), np.array([*(b + s0), zb + lift]), np.array([*(b + s1), zb + lift]), np.array([*(a + s1), za + lift])]
                P.rect(*strip, up, 0.6, STRIP_LOOK)
                n += 2
        return n


def _up(c: list[np.ndarray]) -> np.ndarray:
    nrm = np.cross(c[1] - c[0], c[3] - c[0])
    return nrm if nrm[2] >= 0 else -nrm
