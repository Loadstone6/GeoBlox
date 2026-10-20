"""Buildings as Roblox parts.

LiDAR buildings: the cleaned OSM footprint (with LiDAR-detected courtyards cut
out) is partitioned into planar roof faces (roofer.py). Then:

  * roof faces become plates: flat faces as slabs, pitched faces in their own
    plane frame (a gable plane is one tilted box);
  * walls run along every outline edge from the ground, or from a lower
    neighbour's roof for party walls, up to the roof profile along that edge,
    plus a parapet on flat roofs. The rectangular part below the eave gets a
    facade module. Anything above it (gables, steps) is decomposed exactly;
  * step walls fill height jumps between neighbouring roof faces. Steps of a
    storey or more get windows too.

Facade module (per wall run): spandrel bands at the wall line, a recessed
glass band per floor (tagged so the client can light floors at night), and
piers proud of the wall at every bay boundary. This gives real window
reveals and shadows with B + 2F + 2 parts, where B is bays and F is floors.
Shopfronts, fascias, cornices and curtain walls (mullions and transoms) are
variants.

Mapped landmarks (Simple 3D Buildings parts) use the same wall runs, with
roofs built from their roof:shape: gables, hips and pyramids as exact
planes, domes and onions as ellipsoids, and barrel vaults as box strips.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import roofer
from .buildings import STYLES, Building, Style, courtyard_holes, rgb, roof_palette, stable_hash, wall_tint, _inward_normals
from .config import PARAMS
from .geomutil import parse_colour, triangulate
from .partmap import Look, Parts, rgb_tuple
from .roofs import RoofSamples
from .s3db import is_gilt, part_heights, roof_function
from .scene import Scene, raster_cells_in

PARAPET = 0.8  # m above flat roofs
PASSAGE_H = 5.0  # m clearance where a mapped road runs through a building
WALL_DEPTH = 0.4  # m, thickness of facade parts behind the wall line
ROOF_THICK = 0.3
WALL_MATERIAL = {
    "stock": "Brick", "red": "Brick", "portland": "Limestone", "gothic": "Limestone",
    "glass": "Glass", "concrete": "Concrete", "stucco": "Plaster",
}
GLASS = (34, 44, 52)
SHOP_GLASS = (40, 52, 60)


# ------------------------------------------------------------------ looks

class Looks:
    """Material/colour set for one building."""

    def __init__(self, style: Style, key: int, wall_rgb=None, roof_pitched=None, roof_flat=None, wall_material=None):
        self.style = style
        w = rgb_tuple(wall_rgb if wall_rgb is not None else wall_tint(style, key))
        mat = wall_material or WALL_MATERIAL[style.name]
        self.wall = Look(mat, w, collide=False)
        self.wall_solid = Look(mat, w, collide=True)
        trim = tuple(int(min(255, v * 1.1 + 10)) for v in w)
        self.trim = Look("Limestone" if mat in ("Brick", "Limestone") else mat, trim, collide=False)
        self.glass = Look("Glass", GLASS, collide=False, reflectance=0.18, tag="GeoBloxWindow")
        self.curtain = Look("Glass", rgb_tuple(rgb(*style.glass)), collide=False, reflectance=0.3, tag="GeoBloxWindow")
        self.mullion = Look("Metal", rgb_tuple(rgb(*style.frame)), collide=False)
        self.shop = Look("Glass", SHOP_GLASS, collide=True, reflectance=0.25, tag="GeoBloxShopfront")
        fascia = [(36, 40, 44), (24, 52, 40), (90, 24, 30), (30, 36, 70), (200, 196, 186)][key % 5]
        self.fascia = Look("SmoothPlastic", fascia, collide=False)
        rp, rf = roof_pitched, roof_flat
        self.roof_pitched = Look("Slate", rgb_tuple(rp), collide=True) if rp is not None else Look("Slate", (74, 80, 90))
        flat_mat = ["Concrete", "Concrete", "Asphalt", "Pebble"][(key >> 12) % 4]
        self.roof_flat = Look(flat_mat, rgb_tuple(rf) if rf is not None else (120, 118, 112), collide=True)


def looks_for(b: Building) -> Looks:
    style = STYLES[b.style]
    pitched, flat = roof_palette(b, style)
    mat = b.tags.get("roof:material", "")
    lk = Looks(style, b.key, roof_pitched=pitched, roof_flat=flat)
    if mat in ("lead", "copper", "metal", "zinc"):
        lk.roof_pitched = Look("Metal", lk.roof_pitched.color, collide=True)
    elif mat in ("roof_tiles", "tiles", "clay") or tuple(lk.roof_pitched.color) == rgb_tuple(rgb(140, 76, 58)):
        lk.roof_pitched = Look("Brick", lk.roof_pitched.color, collide=True)
    return lk


# ------------------------------------------------------------------ preparation (LiDAR buildings)

def _dominant_angle(rings: list[np.ndarray]) -> float:
    hist: dict[int, float] = {}
    for r in rings:
        d = np.roll(r, -1, axis=0) - r
        L = np.hypot(d[:, 0], d[:, 1])
        ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90.0
        for a, w in zip(ang, L):
            b = int(round(a * 2)) % 180
            hist[b] = hist.get(b, 0.0) + w
    b = max(hist.items(), key=lambda kv: kv[1])[0] if hist else 0
    # Refine: length-weighted mean of the edge angles near the winning bin.
    centre = b / 2.0
    num = den = 0.0
    for r in rings:
        d = np.roll(r, -1, axis=0) - r
        L = np.hypot(d[:, 0], d[:, 1])
        ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90.0
        dev = (ang - centre + 45.0) % 90.0 - 45.0
        m = np.abs(dev) < 1.5
        num += float((dev[m] * L[m]).sum())
        den += float(L[m].sum())
    return np.radians(centre + (num / den if den > 0 else 0.0))


def _regularize_ring(ring: np.ndarray, theta: float, max_dev: float, merge_off: float) -> np.ndarray | None:
    """Snap near-orthogonal edges to the dominant axes (if that moves their ends
    by at most max_dev), merge parallel neighbours, re-intersect."""
    n = len(ring)
    lines = []  # (point, direction, length)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        d = q - p
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        d = d / L
        best = None
        for k in range(4):
            a = theta + k * np.pi / 2
            ax = np.array([np.cos(a), np.sin(a)])
            sin_dev = abs(d[0] * ax[1] - d[1] * ax[0])
            if d @ ax > 0 and (L / 2 * sin_dev <= max_dev or sin_dev < np.sin(np.radians(2.0))):
                best = ax
        lines.append([(p + q) / 2, best if best is not None else d, L])
    if len(lines) < 3:
        return None
    # Merge consecutive near-collinear lines (weighted by length).
    changed = True
    while changed and len(lines) > 3:
        changed = False
        out = []
        i = 0
        while i < len(lines):
            cur = lines[i]
            nxt = lines[(i + 1) % len(lines)] if i + 1 < len(lines) else None
            if nxt is not None:
                d0, d1 = cur[1], nxt[1]
                off = abs((nxt[0] - cur[0]) @ np.array([-d0[1], d0[0]]))
                if d0 @ d1 > np.cos(np.radians(1.0)) and off < merge_off:
                    w0, w1 = cur[2], nxt[2]
                    out.append([(cur[0] * w0 + nxt[0] * w1) / (w0 + w1), d0, w0 + w1])
                    i += 2
                    changed = True
                    continue
            out.append(cur)
            i += 1
        lines = out
    pts = []
    m = len(lines)
    for i in range(m):
        (p0, d0, _), (p1, d1, _) = lines[i], lines[(i + 1) % m]
        cr = d0[0] * d1[1] - d0[1] * d1[0]
        if abs(cr) < np.sin(np.radians(3.0)):
            # Parallel neighbours with an offset: connect their ends directly.
            pts.append(p0 + d0 * (lines[i][2] / 2))
            pts.append(p1 - d1 * (lines[(i + 1) % m][2] / 2))
            continue
        t = ((p1 - p0)[0] * d1[1] - (p1 - p0)[1] * d1[0]) / cr
        pts.append(p0 + d0 * t)
    return np.array(pts)


def regularize(poly: Polygon, max_dev: float = 0.25, merge_off: float = 0.2) -> Polygon:
    """Footprint regularisation: fewer, orthogonal edges (fewer parts, clean
    corners). Falls back to the input when the result drifts too far."""
    base = poly.simplify(0.2, preserve_topology=True)
    if base.is_empty or base.geom_type != "Polygon":
        return poly
    rings = [np.asarray(base.exterior.coords)[:-1], *[np.asarray(r.coords)[:-1] for r in base.interiors]]
    theta = _dominant_angle(rings)
    out = []
    for r in rings:
        q = _regularize_ring(r, theta, max_dev, merge_off)
        if q is None or len(q) < 3:
            return poly
        out.append(q)
    try:
        cand = Polygon(out[0], out[1:])
    except ValueError:
        return poly
    if not cand.is_valid:
        return poly
    if abs(cand.area - poly.area) > 0.04 * poly.area or cand.hausdorff_distance(poly) > 0.6:
        return poly
    return orient(cand, 1.0)


def clean_outline(poly: Polygon) -> Polygon:
    p = poly.simplify(0.12, preserve_topology=True)
    if not p.is_valid or p.is_empty:
        p = poly.buffer(0)
    if p.geom_type != "Polygon":
        p = max(getattr(p, "geoms", [p]), key=lambda g: g.area)
    return orient(regularize(orient(p, 1.0)), 1.0)


def ground_level(scene: Scene, poly: Polygon) -> float:
    ring = np.array(poly.exterior.coords)[:-1]
    out_n = -_inward_normals(ring)
    g = ring + out_n * 1.0
    return float(np.percentile(scene.dtm.sample(g[:, 0], g[:, 1]), 25))


def prepare(scene: Scene, b: Building) -> None:
    """Footprint, ground and LoD2 roof for a LiDAR building."""
    poly = clean_outline(b.outline)
    b.ground = ground_level(scene, poly)
    cut = courtyard_holes(scene, poly, b.ground)
    if len(cut.interiors) > len(poly.interiors):
        b.courtyards += len(cut.interiors) - len(poly.interiors)
        poly = clean_outline(cut)
    b.footprint = poly
    shrink = 0.45 if poly.area > 12 and poly.buffer(-0.45).area > 2 else 0.0
    rr, cc = raster_cells_in(scene.dsm, poly, shrink)
    if len(rr) < 3:
        rr, cc = raster_cells_in(scene.dsm, poly.buffer(0.6), 0.0)
    xs, ys = scene.dsm.cell_center(rr, cc)
    s = RoofSamples(np.column_stack([xs, ys]), scene.dsm.data[rr, cc].astype(np.float64), np.column_stack([rr, cc]))
    min_support = int(np.clip(len(rr) // 8, 8, PARAMS.ransac_min_support))
    parts = [p for p, _ in b.parts]
    roof = roofer.reconstruct(poly, s, b.ground, stable_hash(b.key) % 2**31, min_support, PARAMS.ransac_threshold, parts)
    floor_z = b.ground + 2.6
    for f in roof.faces:
        zs = f.z(np.asarray(f.poly.exterior.coords))
        if zs.min() < floor_z:
            if f.flat or zs.max() < floor_z + 0.5:
                f.plane = np.array([0.0, 0.0, max(floor_z, float(zs.max()))])
    b.roof = roof
    b.height = roof.top() - b.ground
    b.lidar_ok = b.height > 2.2 and len(rr) >= 3


# ------------------------------------------------------------------ wall runs

def _edge_profile(a: np.ndarray, c: np.ndarray, faces: list, parapet: bool) -> list[tuple[float, float, float, float]]:
    """Roof height along edge a->c as pieces (t0, t1, z0, z1) from the faces
    whose boundaries lie on it."""
    d = c - a
    L = float(np.linalg.norm(d))
    d = d / L
    inward = np.array([-d[1], d[0]])
    ts = {0.0, L}
    for f in faces:
        for ring in [f.poly.exterior, *f.poly.interiors]:
            p = np.asarray(ring.coords)
            rel = p - a
            t = rel @ d
            off = rel @ inward
            m = (np.abs(off) < 0.02) & (t > 0.03) & (t < L - 0.03)
            ts.update(t[m].tolist())
    ts = sorted(ts)
    merged = [ts[0]]
    for t in ts[1:]:
        if t - merged[-1] > 0.05:
            merged.append(t)
    merged[-1] = L
    pieces = []
    for t0, t1 in zip(merged[:-1], merged[1:]):
        mid = Point(a + d * (t0 + t1) / 2 + inward * 0.05)
        face = next((f for f in faces if f.poly.covers(mid)), None)
        if face is None:
            face = min(faces, key=lambda f: f.poly.distance(mid))
        z = face.z(np.array([a + d * t0, a + d * t1]))
        lift = PARAPET if (parapet and face.flat) else 0.0
        pieces.append((t0, t1, float(z[0]) + lift, float(z[1]) + lift))
    return pieces


def _wall_polygon(L: float, zb0: float, zb1: float, pieces) -> Polygon | None:
    top = []
    for t0, t1, z0, z1 in pieces:
        b0 = zb0 + (zb1 - zb0) * t0 / L
        b1 = zb0 + (zb1 - zb0) * t1 / L
        top.append((t0, max(z0, b0 + 0.01)))
        top.append((t1, max(z1, b1 + 0.01)))
    ring = [(0.0, zb0), (L, zb1)] + top[::-1]
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area < 1e-3:
        return None
    return poly


def wall_run(P: Parts, a, c, zb: float, pieces, ground: float, lk: Looks, retail: bool, windows: bool) -> None:
    """Wall from height zb up to the profile `pieces` along a->c (interior on
    the left). A facade module covers the rectangle up to the lowest point of
    the profile; the rest is plain wall, decomposed exactly."""
    a, c = np.asarray(a, dtype=np.float64), np.asarray(c, dtype=np.float64)
    L = float(np.linalg.norm(c - a))
    if L < 0.05 or not pieces:
        return
    d = (c - a) / L
    U = np.array([d[0], d[1], 0.0])
    V = np.array([0.0, 0.0, 1.0])
    origin = np.array([a[0], a[1], 0.0])
    z_eave = min(min(p[2], p[3]) for p in pieces)
    z_top = max(max(p[2], p[3]) for p in pieces)
    if z_top - zb < 0.05:
        return
    rest_from = zb
    if z_eave - zb >= 0.3:
        facade(P, a, c, zb, z_eave, ground, lk, retail, windows)
        rest_from = z_eave
    if z_top - rest_from > 0.02:
        poly = _wall_polygon(L, zb, zb, pieces)
        if poly is None:
            return
        upper = poly.intersection(box(-1.0, rest_from, L + 1.0, z_top + 1.0))
        if not upper.is_empty and upper.area > 1e-3:
            P.plate(upper, origin, U, V, WALL_DEPTH, lk.wall, angles=[0.0])


def facade(P: Parts, a, c, z0: float, z1: float, ground: float, lk: Looks, retail: bool, windows: bool) -> None:
    """Rectangular facade a->c from z0 to z1 (outward = right of travel)."""
    st = lk.style
    d = c - a
    L = float(np.linalg.norm(d))
    d = d / L
    n_out = np.array([d[1], -d[0]])
    H = z1 - z0
    plain = (not windows) or L < 2.2 or H < 2.6

    def band(za, zb, look=None, depth=WALL_DEPTH, offset=0.0, ext=0.0):
        if zb - za > 0.01:
            P.vbox(a - d * ext, c + d * ext, za, zb, depth, n_out, look or lk.wall, offset)

    if plain:
        band(z0, min(z1, ground + 2.5), lk.wall_solid)
        band(max(z0, ground + 2.5), z1)
        return
    if st.curtain:
        _curtain(P, a, c, d, n_out, z0, z1, ground, lk)
        return
    # Floors: (sill, head) pairs whose windows fit below the top band.
    shop_h = 4.2 if retail and z0 < ground + 1.0 and z1 > ground + 4.8 else 0.0
    floors = []
    base = ground + shop_h if shop_h else ground
    k = 0
    while True:
        fb = base + k * st.floor
        sill, head = fb + st.sill, fb + st.sill + st.win_h
        if head > z1 - 0.5:
            break
        if sill >= z0 + 0.25 and (shop_h == 0 or fb >= ground + shop_h - 1e-6):
            floors.append((sill, min(head, fb + st.floor - 0.3)))
        k += 1
    nb = max(1, int(round(L / st.bay)))
    bay = L / nb
    win_w = min(st.win_w, bay - 0.45)
    if not floors and not shop_h:
        band(z0, min(z1, ground + 2.5), lk.wall_solid)
        band(max(z0, ground + 2.5), z1)
        return
    if win_w < 0.5:
        floors = []
    # Ground floor shopfront.
    zcur = z0
    if shop_h:
        P.vbox(a, c, z0, ground + 0.45, WALL_DEPTH, n_out, lk.wall_solid, 0.0)
        P.vbox(a, c, ground + 0.45, ground + 3.4, 0.12, n_out, lk.shop, -0.16)
        P.vbox(a, c, ground + 3.4, ground + 4.15, 0.3, n_out, lk.fascia, 0.07)
        for u0, u1 in ((0.0, 0.45), (L - 0.45, L)):
            P.vbox(a + d * u0, a + d * u1, ground + 0.45, ground + 3.4, 0.35, n_out, lk.wall_solid, 0.04)
        zcur = ground + 4.15
    # Spandrel bands per floor; recessed glass in bands of up to three floors
    # (each band can light up on its own at night).
    first = True
    for sill, head in floors:
        band(zcur, sill, lk.wall_solid if first and zcur < ground + 2.5 else lk.wall)
        zcur = head
        first = False
    band(zcur, z1)
    for g0 in range(0, len(floors), 3):
        grp = floors[g0 : g0 + 3]
        P.vbox(a, c, grp[0][0], grp[-1][1], 0.08, n_out, lk.glass, -0.13)
    # Piers at bay boundaries, proud of the wall.
    if floors:
        zl, zh = floors[0][0], floors[-1][1]
        pw = bay - win_w
        proud = 0.12 if st.pointed else 0.03
        for k in range(nb + 1):
            u0 = max(0.0, k * bay - pw / 2)
            u1 = min(L, k * bay + pw / 2)
            if u1 - u0 > 0.02:
                P.vbox(a + d * u0, a + d * u1, zl, zh, WALL_DEPTH - 0.03, n_out, lk.wall, proud)
    # Cornice on classical facades.
    if st.name in ("portland", "stucco", "stock", "red") and H > 7.0 and L >= 4.0:
        P.vbox(a, c, z1 - 0.95, z1 - 0.62, 0.45, n_out, lk.trim, 0.2)


def _curtain(P: Parts, a, c, d, n_out, z0, z1, ground, lk: Looks) -> None:
    L = float(np.linalg.norm(c - a))
    st = lk.style
    P.vbox(a, c, z0, min(z1, ground + 0.6), WALL_DEPTH, n_out, Look(lk.mullion.material, lk.mullion.color, collide=True), 0.0)
    zb = min(z1, ground + 0.6)
    z = zb
    while z < z1 - 0.05:
        zt = min(z1, z + st.floor)
        P.vbox(a, c, z, zt, 0.1, n_out, lk.curtain, -0.04)
        if zt < z1 - 0.05:
            P.vbox(a, c, zt - 0.09, zt + 0.09, 0.12, n_out, lk.mullion, 0.05)
        z = zt
    nb = max(1, int(round(L / st.bay)))
    for k in range(nb + 1):
        u = L * k / nb
        u0, u1 = max(0.0, u - 0.05), min(L, u + 0.05)
        P.vbox(a + d * u0, a + d * u1, zb, z1, 0.12, n_out, lk.mullion, 0.05)
    P.vbox(a, c, z1 - 0.25, z1, 0.3, n_out, lk.mullion, 0.06)


# ------------------------------------------------------------------ roofs

def plane_frame(plane: np.ndarray, at_xy: np.ndarray):
    a, b, _ = plane
    g = np.hypot(a, b)
    N = np.array([-a, -b, 1.0])
    N /= np.linalg.norm(N)
    U = np.array([-b, a, 0.0]) / g if g > 1e-9 else np.array([1.0, 0.0, 0.0])
    V = np.cross(N, U)
    O = np.array([at_xy[0], at_xy[1], at_xy[0] * plane[0] + at_xy[1] * plane[1] + plane[2]])
    return O, U, V


def to_plane_coords(poly: Polygon, plane: np.ndarray, O, U, V) -> Polygon:
    def conv(ring):
        p = np.asarray(ring.coords)
        z = p[:, 0] * plane[0] + p[:, 1] * plane[1] + plane[2]
        q = np.column_stack([p, z]) - O
        return np.column_stack([q @ U, q @ V])

    return Polygon(conv(poly.exterior), [conv(r) for r in poly.interiors])


def roof_face(P: Parts, poly: Polygon, plane: np.ndarray, look: Look) -> None:
    if np.hypot(plane[0], plane[1]) < 1e-9:
        # A near-rectangular flat roof is one slab: the overshoot (< 0.35 m)
        # reads as coping behind the parapet.
        mrr = poly.minimum_rotated_rectangle
        if poly.area > 0.96 * mrr.area and mrr.hausdorff_distance(poly) < 0.35:
            poly = orient(mrr, 1.0)
        P.flat(poly, float(plane[2]), ROOF_THICK, look)
        return
    c = np.asarray(poly.centroid.coords[0])
    O, U, V = plane_frame(plane, c)
    P.plate(to_plane_coords(poly, plane, O, U, V), O, U, V, ROOF_THICK, look)


def plate3d(P: Parts, pts: np.ndarray, thickness: float, look: Look, up_hint=(0.0, 0.0, 1.0)) -> None:
    """Planar 3D polygon (BNG metres), outer face towards up_hint."""
    pts = np.asarray(pts, dtype=np.float64)
    n = np.zeros(3)
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])])
    if np.linalg.norm(n) < 1e-9:
        return
    N = n / np.linalg.norm(n)
    if N @ np.asarray(up_hint) < 0:
        N = -N
    h = np.array([-N[1], N[0], 0.0])
    U = h / np.linalg.norm(h) if np.linalg.norm(h) > 1e-6 else np.array([1.0, 0.0, 0.0])
    V = np.cross(N, U)
    O = pts.mean(axis=0)
    uv = np.column_stack([(pts - O) @ U, (pts - O) @ V])
    poly = Polygon(uv)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return
    P.plate(poly, O, U, V, thickness, look)


# ------------------------------------------------------------------ LiDAR building emission

class Exposure:
    """Height of whatever stands against a wall (neighbouring buildings)."""

    def __init__(self, scene: Scene, buildings: list[Building]):
        self.scene = scene
        polys = [b.footprint if b.footprint is not None else b.outline for b in buildings]
        self.polys = polys
        self.keys = [b.key for b in buildings]
        self.tree = STRtree(polys)

    def cover(self, key: int, a, c, n_out) -> float | None:
        """Neighbour roof height against edge a->c, or None if open."""
        a, c = np.asarray(a), np.asarray(c)
        probes = [a + (c - a) * t + n_out * 0.9 for t in (0.25, 0.5, 0.75)]
        hs = []
        for p in probes:
            pt = Point(p)
            hit = False
            for i in self.tree.query(pt):
                if self.keys[i] != key and self.polys[i].contains(pt):
                    hit = True
                    break
            if not hit:
                return None
            hs.append(float(self.scene.dsm.sample(np.array([p[0]]), np.array([p[1]]))[0]))
        return float(np.min(hs))


def split_by_passages(a, c, passages) -> list[tuple[np.ndarray, np.ndarray, bool]]:
    """Edge a->c cut where mapped roads pass through the building."""
    a, c = np.asarray(a, dtype=np.float64), np.asarray(c, dtype=np.float64)
    if passages is None or passages.is_empty:
        return [(a, c, False)]
    L = float(np.linalg.norm(c - a))
    seg = LineString([a, c])
    inter = seg.intersection(passages)
    cuts = []
    for g in getattr(inter, "geoms", [inter]):
        if g.geom_type == "LineString" and g.length > 0.3:
            q = np.asarray(g.coords)
            t = sorted(float(np.dot(p - a, (c - a) / L)) for p in (q[0], q[-1]))
            cuts.append((max(0.0, t[0]), min(L, t[1])))
    if not cuts:
        return [(a, c, False)]
    cuts.sort()
    out, t = [], 0.0
    d = (c - a) / L
    for t0, t1 in cuts:
        if t0 - t > 0.05:
            out.append((a + d * t, a + d * t0, False))
        out.append((a + d * t0, a + d * t1, True))
        t = t1
    if L - t > 0.05:
        out.append((a + d * t, c, False))
    return out


def passage_linings(P: Parts, footprint: Polygon, passages, ground: float, lk: "Looks") -> None:
    """Ceiling and side walls of road passages through a building."""
    if passages is None or passages.is_empty:
        return
    inside = passages.intersection(footprint)
    for poly in getattr(inside, "geoms", [inside]):
        if poly.geom_type != "Polygon" or poly.area < 1.0:
            continue
        P.flat(poly, ground + PASSAGE_H, 0.4, lk.wall_solid, up=False)
        ring = np.asarray(orient(poly, 1.0).exterior.coords)
        for i in range(len(ring) - 1):
            a, c = ring[i], ring[i + 1]
            if np.linalg.norm(c - a) < 0.2 or footprint.exterior.distance(Point((a + c) / 2)) < 0.1:
                continue
            d = (c - a) / np.linalg.norm(c - a)
            into_passage = np.array([-d[1], d[0]])
            P.vbox(a, c, ground - 0.5, ground + PASSAGE_H, WALL_DEPTH, into_passage, lk.wall_solid)


def emit_lidar_building(P: Parts, b: Building, exposure: Exposure, passages=None) -> None:
    lk = looks_for(b)
    faces = b.roof.faces
    poly = b.footprint
    passages = passages.intersection(poly.buffer(1.0)) if passages is not None and not passages.is_empty else None
    flat_edge_parapet = True
    for ri, ring in enumerate([poly.exterior, *poly.interiors]):
        pts = np.asarray(ring.coords)[:-1]
        n = len(pts)
        for i in range(n):
            a, c = pts[i], pts[(i + 1) % n]
            L = float(np.linalg.norm(c - a))
            if L < 0.05:
                continue
            d = (c - a) / L
            n_out = np.array([d[1], -d[0]])
            pieces = _edge_profile(a, c, faces, flat_edge_parapet)
            cover = exposure.cover(b.key, a, c, n_out)
            if cover is not None and cover < b.ground + 2.0:
                cover = None  # passage or courtyard at ground level: open
            zb = b.ground - 0.5
            windows = True
            if cover is not None:
                windows = False
                zb = max(zb, cover - 0.2)
                if zb >= max(max(p[2], p[3]) for p in pieces) - 0.1:
                    continue
            for pa, pc, through in split_by_passages(a, c, passages):
                sub = _edge_profile(pa, pc, faces, flat_edge_parapet) if through or not np.allclose(pa, a) or not np.allclose(pc, c) else pieces
                if through:
                    top = min(min(p[2], p[3]) for p in sub)
                    if top < b.ground + PASSAGE_H + 0.8:
                        continue  # too low to bridge the road: leave it open
                    wall_run(P, pa, pc, max(zb, b.ground + PASSAGE_H), sub, b.ground, lk, False, windows and ri == 0)
                else:
                    wall_run(P, pa, pc, zb, sub, b.ground, lk, b.retail and cover is None and ri == 0, windows and ri == 0)
    passage_linings(P, poly, passages, b.ground, lk)
    # Roof faces.
    for f in faces:
        roof_face(P, f.poly, f.plane, lk.roof_flat if f.flat else lk.roof_pitched)
    # Step walls between faces at different heights.
    if len(faces) > 1:
        tree = STRtree([f.poly for f in faces])
        for i, fi in enumerate(faces):
            for j in tree.query(fi.poly.buffer(0.01)):
                j = int(j)
                if j <= i:
                    continue
                fj = faces[j]
                for seg in roofer.shared_edges(fi.poly, fj.poly):
                    _step_walls(P, seg, fi, fj, b.ground, lk)


def _step_walls(P: Parts, seg: np.ndarray, fi, fj, ground: float, lk: Looks) -> None:
    p, q = seg[0], seg[1]
    zi, zj = fi.z(seg), fj.z(seg)
    dp, dq = zi[0] - zj[0], zi[1] - zj[1]
    if max(abs(dp), abs(dq)) < 0.15:
        return
    subs = [(0.0, 1.0)]
    if dp * dq < 0:
        tc = dp / (dp - dq)
        subs = [(0.0, tc), (tc, 1.0)]
    for t0, t1 in subs:
        a = p + (q - p) * t0
        c = p + (q - p) * t1
        if np.linalg.norm(c - a) < 0.05:
            continue
        mid = (a + c) / 2
        za = np.array([fi.z(a[None])[0], fj.z(a[None])[0]])
        zc = np.array([fi.z(c[None])[0], fj.z(c[None])[0]])
        hi_is_i = (za[0] + zc[0]) > (za[1] + zc[1])
        upper, lower = (fi, fj) if hi_is_i else (fj, fi)
        d = (c - a) / np.linalg.norm(c - a)
        n_out = np.array([d[1], -d[0]])
        if not lower.poly.buffer(0.02).contains(Point(mid + n_out * 0.25)):
            a, c = c, a
        zb0, zb1 = lower.z(np.array([a, c]))
        zt0, zt1 = upper.z(np.array([a, c]))
        lift = PARAPET if upper.flat else 0.0
        L = float(np.linalg.norm(c - a))
        if abs(zb0 - zb1) < 0.05 and abs(zt0 - zt1) < 0.05 and min(zt0, zt1) - max(zb0, zb1) >= 3.0:
            facade(P, a, c, float(max(zb0, zb1)), float(min(zt0, zt1)) + lift, ground, lk, False, True)
            continue
        poly = _wall_polygon(L, float(zb0), float(zb1), [(0.0, L, float(zt0) + lift, float(zt1) + lift)])
        if poly is not None:
            P.plate(poly, np.array([a[0], a[1], 0.0]), np.array([*((c - a) / L), 0.0]), np.array([0.0, 0.0, 1.0]), WALL_DEPTH, lk.wall, angles=[0.0])


# ------------------------------------------------------------------ mapped landmarks (S3DB)

def _obb(poly: Polygon):
    rect = poly.minimum_rotated_rectangle
    c = np.array(rect.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) >= np.linalg.norm(e2):
        u, L, v, W = e1 / np.linalg.norm(e1), np.linalg.norm(e1) / 2, e2 / max(np.linalg.norm(e2), 1e-9), np.linalg.norm(e2) / 2
    else:
        u, L, v, W = e2 / np.linalg.norm(e2), np.linalg.norm(e2) / 2, e1 / max(np.linalg.norm(e1), 1e-9), np.linalg.norm(e1) / 2
    return c.mean(axis=0), u, v, L, W, rect


def _coplanar_groups(V3: np.ndarray, T: np.ndarray) -> list[list[int]]:
    """Group triangles of a 2.5D surface into planar regions (edge-adjacent,
    same plane within 1.5 cm)."""
    planes = []
    for t in T:
        p = V3[t]
        A = np.column_stack([p[:, :2], np.ones(3)])
        try:
            planes.append(np.linalg.solve(A, p[:, 2]))
        except np.linalg.LinAlgError:
            planes.append(np.array([0.0, 0.0, p[:, 2].mean()]))
    planes = np.array(planes)
    parent = list(range(len(T)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    edge_owner: dict[tuple[int, int], int] = {}
    for ti, t in enumerate(T):
        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            key = (min(e), max(e))
            if key in edge_owner:
                tj = edge_owner[key]
                pi, pj = planes[ti], planes[tj]
                cen = V3[T[tj]][:, :2]
                if np.hypot(pi[0] - pj[0], pi[1] - pj[1]) < 0.01 and np.all(np.abs(cen @ pi[:2] + pi[2] - (cen @ pj[:2] + pj[2])) < 0.015):
                    parent[find(ti)] = find(tj)
            else:
                edge_owner[key] = ti
    groups: dict[int, list[int]] = {}
    for ti in range(len(T)):
        groups.setdefault(find(ti), []).append(ti)
    return list(groups.values())


def _profile_from_fn(a, c, f, ground: float, step: float = 0.25) -> list[tuple[float, float, float, float]]:
    L = float(np.linalg.norm(c - a))
    n = max(1, int(np.ceil(L / step)))
    ts = np.linspace(0.0, L, n + 1)
    pts = a + np.outer(ts / L, c - a)
    z = f(pts) + ground
    line = LineString(np.column_stack([ts, z])).simplify(0.02)
    q = np.asarray(line.coords)
    return [(float(q[i, 0]), float(q[i + 1, 0]), float(q[i, 1]), float(q[i + 1, 1])) for i in range(len(q) - 1)]


def emit_s3db_building(P: Parts, b: Building, dial_zone: Polygon | None, passages=None) -> None:
    style = STYLES[b.style]
    pitched_default, _ = roof_palette(b, style)
    info = []
    for poly, tags in b.parts:
        hh = part_heights(tags)
        if hh is None or hh[1] - hh[0] < 0.05:
            continue
        if dial_zone is not None and tags.get("building:colour") == "white" and tags.get("roof:shape") == "round" and poly.intersects(dial_zone):
            continue  # Big Ben's dials are built by the landmark module
        p = orient(poly.simplify(0.05, preserve_topology=True), 1.0)
        if p.is_valid and p.area > 0.05:
            info.append((p, tags, hh))
    if not info:
        return
    tree = STRtree([p for p, _, _ in info])
    for idx, (poly, tags, (h0, h1)) in enumerate(info):
        wall_c = parse_colour(tags.get("building:colour")) if tags.get("building:colour") else parse_colour(tags.get("colour"))
        roof_c = parse_colour(tags.get("roof:colour"))
        mat = tags.get("building:material", "")
        wall_mat = {"brick": "Brick", "glass": "Glass", "metal": "Metal", "concrete": "Concrete", "plaster": "Plaster"}.get(mat)
        gilt = wall_c is not None and is_gilt(wall_c)
        lk = Looks(style, b.key, wall_rgb=wall_c, roof_pitched=roof_c if roof_c is not None else pitched_default, roof_flat=roof_c, wall_material="Foil" if gilt else wall_mat)
        if roof_c is not None and is_gilt(roof_c):
            lk.roof_pitched = Look("Foil", rgb_tuple(roof_c))
            lk.roof_flat = lk.roof_pitched
        if tags.get("roof:material") in ("copper", "lead", "metal", "zinc"):
            lk.roof_pitched = Look("Metal", lk.roof_pitched.color)
        f, shape, rh, base, extra, steiner = roof_function(poly, tags, h0, h1)
        g = b.ground
        z_bot = g + h0 if h0 > 0.05 else g - 0.5
        windows = (not gilt) and h1 - h0 >= 4.5 and poly.area >= 20
        for ring in [poly.exterior, *poly.interiors]:
            pts = np.asarray(ring.coords)[:-1]
            n = len(pts)
            for i in range(n):
                a, c = pts[i], pts[(i + 1) % n]
                L = float(np.linalg.norm(c - a))
                if L < 0.05:
                    continue
                d = (c - a) / L
                n_out = np.array([d[1], -d[0]])
                probe = Point((a + c) / 2 + n_out * 0.3)
                zb = z_bot
                for j in tree.query(probe):
                    if j != idx and info[j][0].contains(probe):
                        jh0, jh1 = info[j][2]
                        if g + jh0 <= zb + 0.5:
                            zb = max(zb, g + jh1)
                for pa, pc, through in split_by_passages(a, c, passages):
                    Ls = float(np.linalg.norm(pc - pa))
                    pieces = _profile_from_fn(pa, pc, f, g) if shape != "flat" else [(0.0, Ls, g + h1, g + h1)]
                    zbs = max(zb, g + PASSAGE_H) if through else zb
                    if zbs >= max(max(p[2], p[3]) for p in pieces) - 0.05:
                        continue
                    wall_run(P, pa, pc, zbs, pieces, g, lk, False, windows and Ls >= 2.4)
        if h0 > 0.3:
            P.flat(poly, g + h0, 0.2, lk.wall, up=False)
        _s3db_roof(P, poly, tags, shape, f, rh, base, g, h1, extra, steiner, lk)


def _s3db_roof(P: Parts, poly: Polygon, tags: dict, shape: str, f, rh: float, base: float, g: float, h1: float, extra, steiner, lk: Looks) -> None:
    if shape == "flat" or rh <= 0.01:
        P.flat(poly, g + h1, ROOF_THICK, lk.roof_flat)
        return
    center, u, v, L, W, rect = _obb(poly)
    near_rect = poly.area / max(rect.area, 1e-9) > 0.93
    zb = g + base
    if shape in ("dome", "onion"):
        P.flat(poly, zb, ROOF_THICK, lk.roof_pitched)
        k = 1.12 if shape == "onion" else 1.0
        u3 = P.Sdir(np.array([u[0], u[1], 0.0]))
        y3 = np.array([0.0, 1.0, 0.0])
        size = np.array([2 * L * k, 2 * rh * (0.85 if shape == "onion" else 1.0), 2 * W * k]) / 0.3
        cz = zb + (0.15 * rh if shape == "onion" else 0.0)
        P.ellipsoid_s(P.S(np.array([center[0], center[1], cz])), u3, y3, np.cross(u3, y3), size, lk.roof_pitched)
        if shape == "onion":
            tip = np.array([center[0], center[1], zb + rh * 1.15])
            ring = [center + (u * np.cos(t) + v * np.sin(t)) * min(L, W) * 0.25 for t in np.linspace(0, 2 * np.pi, 7)[:-1]]
            for k2 in range(6):
                p0, p1 = ring[k2], ring[(k2 + 1) % 6]
                P.tri(np.array([p0[0], p0[1], zb + rh * 0.8]), np.array([p1[0], p1[1], zb + rh * 0.8]), tip, _tri_up(p0, p1, tip, zb + rh * 0.8), 0.1, lk.roof_pitched)
        return
    if shape == "round" and near_rect:
        n = 8
        prev = None
        for k in range(n + 1):
            vv = -W + 2 * W * k / n
            z = zb + rh * np.sqrt(max(0.0, 1 - (vv / W) ** 2))
            cur = (vv, z)
            if prev is not None:
                pts = []
                for (va, za) in (prev, cur):
                    pts.append(np.array([*(center - u * L + v * va), za]))
                    pts.append(np.array([*(center + u * L + v * va), za]))
                plate3d(P, np.array([pts[0], pts[1], pts[3], pts[2]]), ROOF_THICK, lk.roof_pitched)
            prev = cur
        return
    if shape in ("pyramidal", "cone"):
        apex_xy = center if poly.contains(Point(center)) else np.asarray(poly.representative_point().coords[0])
        apex = np.array([apex_xy[0], apex_xy[1], g + h1])
        ring = np.asarray(poly.exterior.coords)[:-1]
        for i in range(len(ring)):
            p0 = np.array([*ring[i], zb])
            p1 = np.array([*ring[(i + 1) % len(ring)], zb])
            P.tri(p0, p1, apex, _tri_up(ring[i], ring[(i + 1) % len(ring)], apex, zb), ROOF_THICK, lk.roof_pitched)
        return
    if shape == "hipped" and near_rect and L >= W:
        c1 = center - u * L - v * W
        c2 = center + u * L - v * W
        c3 = center + u * L + v * W
        c4 = center - u * L + v * W
        r1 = center - u * (L - W)
        r2 = center + u * (L - W)
        top = zb + rh
        Z = lambda p, z: np.array([p[0], p[1], z])
        plate3d(P, np.array([Z(c1, zb), Z(c2, zb), Z(r2, top), Z(r1, top)]), ROOF_THICK, lk.roof_pitched)
        plate3d(P, np.array([Z(c3, zb), Z(c4, zb), Z(r1, top), Z(r2, top)]), ROOF_THICK, lk.roof_pitched)
        plate3d(P, np.array([Z(c2, zb), Z(c3, zb), Z(r2, top)]), ROOF_THICK, lk.roof_pitched)
        plate3d(P, np.array([Z(c4, zb), Z(c1, zb), Z(r1, top)]), ROOF_THICK, lk.roof_pitched)
        return
    # Generic: triangulate the roof function, merge coplanar triangles.
    V2, T = triangulate(poly, steiner, extra)
    if len(T) == 0:
        P.flat(poly, g + h1, ROOF_THICK, lk.roof_flat)
        return
    V3 = np.column_stack([V2, f(V2) + g])
    for grp in _coplanar_groups(V3, T):
        tris = [Polygon(V2[T[t]]) for t in grp]
        region = unary_union(tris).buffer(0)
        A = np.column_stack([V3[T[grp[0]]][:, :2], np.ones(3)])
        try:
            plane = np.linalg.solve(A, V3[T[grp[0]]][:, 2])
        except np.linalg.LinAlgError:
            continue
        for part in getattr(region, "geoms", [region]):
            if part.geom_type == "Polygon" and part.area > 1e-3:
                roof_face(P, orient(part, 1.0), plane, lk.roof_pitched)


def _tri_up(p0, p1, apex, z) -> np.ndarray:
    a = np.array([p0[0], p0[1], z])
    b = np.array([p1[0], p1[1], z])
    n = np.cross(b - a, np.asarray(apex) - a)
    return n if n[2] >= 0 else -n
