"""Road markings from road centrelines and measured carriageway widths.

Markings are generated as offset curves of each centreline: a station-wise
offset o(s) (relative to the centre of the measured carriageway) is applied
along the unit normal, resampled at 0.5 m, then cut into dashes by arc
length. Following UK TSRGD practice:
  * centre line (diag. 1004): 4 m mark / 5 m gap, 100 mm
  * lane line   (diag. 1005): 2 m mark / 7 m gap, 100 mm
  * no-waiting double lines 250 mm from each kerb (yellow; red on red routes)
  * zebra crossings: 600 mm bars + zig-zags; signal crossings: stud lines and
    stop lines on the approach side for left-hand traffic.
Everything is suppressed inside junction discs and crossing zones.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from .ground import Ground, Road
from .partmap import Look, Parts
from .scene import Scene

WHITE = Look("SmoothPlastic", (226, 226, 221), collide=False, shadow=False)
YELLOW = Look("SmoothPlastic", (219, 178, 46), collide=False, shadow=False)
RED = Look("SmoothPlastic", (168, 31, 26), collide=False, shadow=False)
RED_ROUTE = {"trunk", "primary", "trunk_link", "primary_link"}
TOP = 0.03  # m above the surface; markings are 8 cm parts so terrain noise never buries them
THICK = 0.08


class Builder:
    """Markings as thin, non-colliding parts lying on the road surface."""

    def __init__(self, P: Parts, zfn):
        self.P = P
        self.zfn = zfn
        self.count = 0

    def strip(self, look: Look, pts: np.ndarray, width: float, zfn=None) -> None:
        if len(pts) < 2:
            return
        if len(pts) > 2:
            pts = np.asarray(LineString(pts).simplify(0.03).coords)
        z = (zfn or self.zfn)(pts) + TOP - THICK / 2
        ext = min(width / 2, 0.05) if len(pts) > 2 else 0.0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            L = float(np.linalg.norm(b - a))
            if L < 0.02:
                continue
            d = (b - a) / L
            p0 = np.array([*(a - d * ext), z[i]])
            p1 = np.array([*(b + d * ext), z[i + 1]])
            self.P.beam(p0, p1, width, THICK, (0.0, 0.0, 1.0), look)
            self.count += 1

    def rect(self, look: Look, center, along, half_l, half_w, zfn=None):
        p0 = center - along * half_l
        p1 = center + along * half_l
        self.strip(look, np.array([p0, p1]), half_w * 2, zfn)


def _resample(r: Road, offset_fn, step: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Points along the road at arc-length step, shifted by offset_fn(station_index_float)."""
    seg = np.linalg.norm(np.diff(r.stations, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    L = s[-1]
    if L < 1:
        return np.zeros((0, 2)), np.zeros(0)
    ss = np.arange(0, L, step)
    x = np.interp(ss, s, r.stations[:, 0])
    y = np.interp(ss, s, r.stations[:, 1])
    tx = np.interp(ss, s, r.tangents[:, 0])
    ty = np.interp(ss, s, r.tangents[:, 1])
    tl = np.hypot(tx, ty)
    tx, ty = tx / np.maximum(tl, 1e-9), ty / np.maximum(tl, 1e-9)
    left = np.interp(ss, s, r.left)
    right = np.interp(ss, s, r.right)
    off = offset_fn(left, right)
    pts = np.column_stack([x - ty * off, y + tx * off])
    return pts, ss


def _dashes(pts: np.ndarray, ss: np.ndarray, mark: float, gap: float, phase: float = 0.0) -> list[np.ndarray]:
    if len(pts) < 2:
        return []
    period = mark + gap
    on = ((ss + phase) % period) < mark
    out, cur = [], []
    for i in range(len(pts)):
        if on[i]:
            cur.append(pts[i])
        elif cur:
            if len(cur) >= 2:
                out.append(np.array(cur))
            cur = []
    if len(cur) >= 2:
        out.append(np.array(cur))
    return out


def _cut(polys: list[np.ndarray], forbidden) -> list[np.ndarray]:
    """Split polylines where they enter forbidden zones or leave the carriageway."""
    out = []
    for p in polys:
        ok = ~shapely.contains_xy(forbidden, p[:, 0], p[:, 1]) if forbidden is not None else np.ones(len(p), bool)
        cur = []
        for i in range(len(p)):
            if ok[i]:
                cur.append(p[i])
            elif cur:
                if len(cur) >= 2:
                    out.append(np.array(cur))
                cur = []
        if len(cur) >= 2:
            out.append(np.array(cur))
    return out


def emit(P: Parts, scene: Scene, ground: Ground, road_z, deck_z) -> list[dict]:
    """Emit marking parts; return crossing records for props (beacons, signals).

    road_z(xy) is the terrain road surface; deck_z(road, xy) the deck surface
    of an elevated bridge stretch."""
    B = Builder(P, road_z)
    decks = list(ground.deck_roads or [])
    roads = [r for r in list(ground.roads) + decks if r.kind != "cycleway"]
    deck_ids = {id(r) for r in decks}

    def zf(r: Road):
        if id(r) in deck_ids:
            return lambda xy, r=r: deck_z(r, xy)
        return road_z
    # Junction discs: nodes shared by 3+ road ends/ways.
    node_count: Counter = Counter()
    for r in roads:
        for n in set(r.feature.nodes):
            node_count[n] += 1
    xy = scene.osm.nodes_xy
    junctions = []
    for r in roads:
        for n in r.feature.nodes:
            if node_count[n] >= 2 and (n == r.feature.nodes[0] or n == r.feature.nodes[-1] or node_count[n] >= 3):
                w = float(max(r.left.max(), r.right.max()))
                junctions.append(Point(xy[n]).buffer(w + 3.0, 12))
    crossings = []
    zones = []
    for p in scene.osm.points:
        t = p.tags
        if t.get("highway") != "crossing" and t.get("railway") != "crossing":
            continue
        kind = None
        if t.get("crossing:markings") == "zebra" or t.get("crossing") == "zebra" or t.get("crossing_ref") == "zebra":
            kind = "zebra"
        elif t.get("crossing") == "traffic_signals" or t.get("crossing:markings") == "dots" or t.get("crossing:signals") == "yes":
            kind = "signals"
        elif t.get("crossing") == "marked":
            kind = "zebra"
        if kind:
            crossings.append((p, kind))
            zones.append(p.geom.buffer(4.0 if kind == "signals" else 18.0))
    forbidden = unary_union(junctions + zones) if (junctions or zones) else None
    outside = scene.clip.difference(ground.carriageway.buffer(-0.15))
    forbidden = outside if forbidden is None else forbidden.union(outside)

    for r in roads:
        z_of = zf(r)
        width = r.left + r.right
        two_way = not r.oneway
        lanes = r.lanes or (2 if two_way else max(1, int(np.median(width) // 3.3)))
        if r.kind not in ("service",) and np.median(width) >= 5.5:
            centre = lambda L, R: (L - R) / 2
            if two_way:
                pts, ss = _resample(r, centre)
                B_lines = _cut(_dashes(pts, ss, 4.0, 5.0), forbidden)
                for ln in B_lines:
                    B.strip(WHITE, ln, 0.13, z_of)
            # Lane lines.
            per_dir = lanes // 2 if two_way else lanes
            if per_dir >= 2:
                for k in range(1, per_dir):
                    for sgn in ((1, -1) if two_way else (1,)):
                        def lane_off(L, R, k=k, sgn=sgn):
                            c = (L - R) / 2
                            half = (L + R) / 2
                            if two_way:
                                return c + sgn * half * k / per_dir
                            return c - half + (L + R) * k / per_dir

                        pts, ss = _resample(r, lane_off)
                        for ln in _cut(_dashes(pts, ss, 2.0, 7.0), forbidden):
                            B.strip(WHITE, ln, 0.1, z_of)
            # Double no-waiting lines along both kerbs.
            if r.kind not in ("service", "busway"):
                look = RED if r.kind in RED_ROUTE else YELLOW
                for side in (1, -1):
                    for k, inset in enumerate((0.25, 0.47)):
                        off = (lambda L, R, side=side, inset=inset: L - inset) if side == 1 else (lambda L, R, inset=inset: -(R - inset))
                        pts, ss = _resample(r, off)
                        for ln in _cut([pts], forbidden):
                            B.strip(look, ln, 0.1, z_of)

    # Crossings.
    records = []
    road_lines = [(r, r.line) for r in roads]
    tree = shapely.STRtree([ln for _, ln in road_lines]) if road_lines else None
    for p, kind in crossings:
        if tree is None:
            break
        cand = tree.query(p.geom.buffer(1.0))
        if len(cand) == 0:
            continue
        r, line = road_lines[int(cand[0])]
        z_of = zf(r)
        s = line.project(p.geom)
        a = np.array(line.interpolate(max(0, s - 1)).coords[0])
        b = np.array(line.interpolate(min(line.length, s + 1)).coords[0])
        along = (b - a) / max(np.linalg.norm(b - a), 1e-9)
        across = np.array([-along[1], along[0]])
        i = int(np.argmin(np.linalg.norm(r.stations - np.array(p.geom.coords[0]), axis=1)))
        L, R = r.left[i], r.right[i]
        c = np.array(p.geom.coords[0]) + across * (L - R) / 2
        half = (L + R) / 2
        if kind == "zebra":
            n = int((L + R) // 1.2)
            for k in range(n):
                off = -half + 0.6 + k * 1.2
                B.rect(WHITE, c + across * off, along, 1.6, 0.3, z_of)
            # Zig-zags on both approaches along both kerbs and the centre.
            for sgn in (1, -1):
                for lat in (-half + 0.35, half - 0.35, 0.0):
                    zz = []
                    for j in range(17):
                        d = sgn * (3.0 + j * 0.9)
                        wob = 0.25 * (1 if j % 2 else -1)
                        zz.append(c + along * d + across * (lat + wob))
                    B.strip(WHITE, np.array(zz), 0.1, z_of)
            # Give-way line (dashes) 1 m before each side.
            for sgn in (1, -1):
                for k in range(int(L + R)):
                    off = -half + 0.5 + k
                    B.rect(WHITE, c + along * sgn * 2.4 + across * off, across, 0.3, 0.1, z_of)
        else:
            # Signalised crossing: two lines of 200 mm studs 2.4 m apart.
            for sgn in (1, -1):
                k = 0.0
                while k < L + R:
                    B.rect(WHITE, c + along * sgn * 1.4 + across * (-half + k + 0.2), along, 0.1, 0.1, z_of)
                    k += 0.6
            # Stop lines on the approach half (left-hand traffic).
            if not r.oneway:
                B.rect(WHITE, c - along * 3.4 + across * (half / 2), across, half / 2, 0.15, z_of)
                B.rect(WHITE, c + along * 3.4 - across * (half / 2), across, half / 2, 0.15, z_of)
            else:
                B.rect(WHITE, c - along * 3.4, across, half, 0.15, z_of)
        records.append({"kind": kind, "c": c, "along": along, "across": across, "half": half, "deck": id(r) in deck_ids})
    return records
