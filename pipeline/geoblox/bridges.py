"""Bridges over the Thames.

Deck surface: the first-return DSM over the bridge polygon is binned along the
bridge's long axis; a low percentile per bin (vehicles and people are
outliers above the deck) gives the deck profile z(s), smoothed with a moving
median. Below the deck the structure is parametric: evenly spaced piers and
segmental arches whose spring line sits just above the measured water level.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from .geomutil import grid_points, triangulate
from .ground import Ground, Water
from .mesh import Collector, Piece, merge
from .osm import polygons
from .scene import Scene

PAINT = {
    "Westminster Bridge": np.array([0.20, 0.42, 0.30]),  # "Commons green"
    "Lambeth Bridge": np.array([0.62, 0.16, 0.14]),  # "Lords red"
}
STONE = np.array([0.72, 0.70, 0.64])


@dataclass
class Bridge:
    name: str
    poly: Polygon
    axis_c: np.ndarray
    u: np.ndarray  # long axis
    v: np.ndarray
    s_min: float
    s_max: float
    profile_s: np.ndarray
    profile_z: np.ndarray
    over_water: bool

    def deck_z(self, xy: np.ndarray) -> np.ndarray:
        s = (np.asarray(xy) - self.axis_c) @ self.u
        return np.interp(s, self.profile_s, self.profile_z)


def find(scene: Scene, ground: Ground) -> list[Bridge]:
    river = unary = None
    rivers = [w for w in ground.water if w.kind == "river"]
    if rivers:
        from shapely.ops import unary_union

        unary = unary_union([w.poly for w in rivers])
    feats = [f for f in scene.osm.areas if f.tags.get("man_made") == "bridge"]
    out = []
    for f in feats:
        for poly in polygons(f.geom):
            if poly.area < 150 or not poly.intersects(scene.fetch_clip):
                continue
            rect = poly.minimum_rotated_rectangle
            c = np.array(rect.exterior.coords)[:4]
            e1, e2 = c[1] - c[0], c[2] - c[1]
            u = e1 if np.linalg.norm(e1) > np.linalg.norm(e2) else e2
            u = u / np.linalg.norm(u)
            v = np.array([-u[1], u[0]])
            center = c.mean(axis=0)
            g = grid_points(poly, 1.0, 0.6)
            if len(g) < 20:
                continue
            z = scene.dsm.sample(g[:, 0], g[:, 1])
            s = (g - center) @ u
            bins = np.arange(s.min(), s.max() + 2.0, 2.0)
            idx = np.digitize(s, bins)
            ps, pz = [], []
            for k in range(1, len(bins)):
                m = idx == k
                if m.sum() >= 3:
                    ps.append((bins[k - 1] + bins[k]) / 2)
                    pz.append(np.percentile(z[m], 30))
            if len(ps) < 3:
                continue
            pz = np.array(pz)
            # Moving median to reject buses, lamp columns, people.
            pzs = np.array([np.median(pz[max(0, i - 3) : i + 4]) for i in range(len(pz))])
            over = unary is not None and poly.intersection(unary).area > 0.25 * poly.area
            out.append(Bridge(f.tags.get("name", ""), orient(poly, 1.0), center, u, v, float(s.min()), float(s.max()), np.array(ps), pzs, over))
    return out


def register_decks(ground: Ground, bridges: list[Bridge]) -> None:
    for b in bridges:
        ground.bridge_decks.append((b.poly, b.deck_z))


def _box(center, u, v, half_u, half_v, z0, z1, color):
    """Axis-aligned (in u/v) box as 5 quads (no bottom)."""
    cu, cv = u * half_u, v * half_v
    corners = [center - cu - cv, center + cu - cv, center + cu + cv, center - cu + cv]
    pieces = []
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        d = b - a
        n = np.array([d[1], -d[0]]) / max(np.linalg.norm(d), 1e-9)
        q = np.array([[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z1], [a[0], a[1], z1]])
        n3 = np.array([n[0], n[1], 0.0])
        if np.dot(np.cross(q[1] - q[0], q[2] - q[0]), n3) < 0:
            q = q[[1, 0, 3, 2]]
        pieces.append(Piece(q, np.array([[0, 1, 2], [0, 2, 3]]), np.tile(n3, (4, 1)), np.tile(color, (4, 1)), q[:, :2] / 2))
    top = np.array([[*corners[0], z1], [*corners[1], z1], [*corners[2], z1], [*corners[3], z1]])
    if np.cross(top[1] - top[0], top[2] - top[0])[2] < 0:
        top = top[[0, 3, 2, 1]]
    pieces.append(Piece(top, np.array([[0, 1, 2], [0, 2, 3]]), np.tile([0, 0, 1.0], (4, 1)), np.tile(color, (4, 1)), top[:, :2] / 2))
    return pieces


def emit(col: Collector, scene: Scene, ground: Ground, bridges: list[Bridge]) -> list[dict]:
    """Emit bridge structure meshes. Returns collision boxes for decks/parapets."""
    collisions = []
    water_level = scene.water_level
    for b in bridges:
        paint = next((c for k, c in PAINT.items() if k in b.name), STONE)
        pieces: list[Piece] = []
        deck_th = 1.4
        # Fascia (deck edge) all round the polygon, from deck top down to the soffit.
        ring = np.asarray(b.poly.exterior.coords)
        for i in range(len(ring) - 1):
            a, c = ring[i], ring[i + 1]
            seg = c - a
            L = np.linalg.norm(seg)
            if L < 0.05:
                continue
            d = seg / L
            n = np.array([d[1], -d[0]])
            za, zc = b.deck_z(np.array([a, c]))
            q = np.array([[a[0], a[1], za - deck_th], [c[0], c[1], zc - deck_th], [c[0], c[1], zc + 0.05], [a[0], a[1], za + 0.05]])
            pieces.append(Piece(q, np.array([[0, 1, 2], [0, 2, 3]]), np.tile([n[0], n[1], 0.0], (4, 1)), np.tile(paint * 0.9, (4, 1)), q[:, :2] / 2))
        # Soffit (underside).
        V2, T = triangulate(b.poly, grid_points(b.poly, 6.0, 1.0))
        if len(T):
            z = b.deck_z(V2) - deck_th
            v = np.column_stack([V2, z])
            pieces.append(Piece(v, T[:, [0, 2, 1]], np.tile([0, 0, -1.0], (len(v), 1)), np.tile(paint * 0.55, (len(v), 1)), V2 / 2))
        # Parapets along both long edges (inset 0.3 m), 1.1 m high, 0.4 m thick.
        width = float(np.ptp((ring - b.axis_c) @ b.v))
        length = b.s_max - b.s_min
        step = 2.0
        for side in (-1, 1):
            off = side * (width / 2 - 0.5)
            ss = np.arange(b.s_min + 1.0, b.s_max - 1.0, step)
            for s0 in ss:
                s1 = min(s0 + step, b.s_max - 1.0)
                p0 = b.axis_c + b.u * s0 + b.v * off
                p1 = b.axis_c + b.u * s1 + b.v * off
                if not b.poly.buffer(0.2).contains(shapely.Point((p0 + p1) / 2)):
                    continue
                z0, z1 = b.deck_z(np.array([p0, p1]))
                zc = (z0 + z1) / 2
                center = (p0 + p1) / 2
                pieces.extend(_box(center, b.u, b.v, (s1 - s0) / 2, 0.22, zc, zc + 1.1, paint))
                collisions.append({"c": [*center, zc + 0.55], "u": b.u.tolist(), "size": [s1 - s0, 0.44, 1.1]})
        # Deck collision boxes (walkable), every 6 m.
        for s0 in np.arange(b.s_min, b.s_max, 6.0):
            s1 = min(s0 + 6.0, b.s_max)
            center = b.axis_c + b.u * ((s0 + s1) / 2)
            zc = float(b.deck_z(center[None, :])[0])
            collisions.append({"c": [*center, zc - 0.6], "u": b.u.tolist(), "size": [s1 - s0 + 0.4, width, 1.2]})
        # Piers and arches for river crossings.
        if b.over_water and length > 60:
            rivers = [w for w in ground.water if w.kind == "river"]
            river = rivers[0].poly if rivers else None
            n_spans = 7 if "Westminster" in b.name else (5 if ("Lambeth" in b.name or "Waterloo" in b.name) else max(3, int(length // 45)))
            # Spans cover the part of the bridge over water.
            ss = np.linspace(b.s_min, b.s_max, 400)
            pts = b.axis_c + np.outer(ss, b.u)
            wet = shapely.contains_xy(river, pts[:, 0], pts[:, 1]) if river is not None else np.zeros(len(ss), dtype=bool)
            if wet.any():
                w0, w1 = ss[wet].min() - 4, ss[wet].max() + 4
                piers = np.linspace(w0, w1, n_spans + 1)
                pier_w = 4.0
                for k, s in enumerate(piers):
                    center = b.axis_c + b.u * s
                    deck = float(b.deck_z(center[None, :])[0])
                    pieces.extend(_box(center, b.u, b.v, pier_w / 2, width / 2 + 0.4, water_level - 3, deck - deck_th, STONE * 0.85))
                    # Cutwaters: pointed ends upstream/downstream.
                for k in range(n_spans):
                    sa, sb = piers[k] + 2.0, piers[k + 1] - 2.0
                    mid, half = (sa + sb) / 2, (sb - sa) / 2
                    spring = water_level + 1.6
                    nseg = 14
                    ts = np.linspace(-1, 1, nseg + 1)
                    for j in range(nseg):
                        t0, t1 = ts[j], ts[j + 1]
                        sA, sB = mid + t0 * half, mid + t1 * half
                        dA = b.deck_z((b.axis_c + b.u * sA)[None, :])[0] - deck_th
                        dB = b.deck_z((b.axis_c + b.u * sB)[None, :])[0] - deck_th
                        rise = max(0.5, min(dA, dB) - spring - 0.6)
                        zA = spring + rise * np.sqrt(max(0.0, 1 - t0 * t0))
                        zB = spring + rise * np.sqrt(max(0.0, 1 - t1 * t1))
                        # Soffit strip across the deck width.
                        pa = b.axis_c + b.u * sA
                        pb = b.axis_c + b.u * sB
                        hv = b.v * (width / 2)
                        q = np.array([[*(pa - hv), zA], [*(pb - hv), zB], [*(pb + hv), zB], [*(pa + hv), zA]])
                        nn = np.cross(q[1] - q[0], q[2] - q[0])
                        if nn[2] > 0:
                            q = q[[1, 0, 3, 2]]
                        nn = np.cross(q[1] - q[0], q[2] - q[0])
                        nn /= max(np.linalg.norm(nn), 1e-9)
                        pieces.append(Piece(q, np.array([[0, 1, 2], [0, 2, 3]]), np.tile(nn, (4, 1)), np.tile(paint * 0.7, (4, 1)), q[:, :2] / 2))
                        # Spandrel faces on both sides between arch and deck.
                        for sgn in (-1, 1):
                            e = b.v * (sgn * width / 2)
                            sp = np.array([[*(pa + e), zA], [*(pb + e), zB], [*(pb + e), dB], [*(pa + e), dA]])
                            n3 = np.array([*(b.v * sgn), 0.0])
                            if np.dot(np.cross(sp[1] - sp[0], sp[2] - sp[0]), n3) < 0:
                                sp = sp[[1, 0, 3, 2]]
                            pieces.append(Piece(sp, np.array([[0, 1, 2], [0, 2, 3]]), np.tile(n3, (4, 1)), np.tile(paint, (4, 1)), sp[:, :2] / 2))
        if pieces:
            col.add_by_centroid((0, 1), "bridge", merge(pieces))
    return collisions
