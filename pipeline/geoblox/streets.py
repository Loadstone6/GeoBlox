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
from shapely.geometry import Point
from shapely.ops import unary_union

from .config import PARAMS
from .ground import Ground, Road, drape
from .mesh import Collector, Piece
from .scene import Scene

WHITE = np.array([0.88, 0.88, 0.86])
YELLOW = np.array([0.86, 0.70, 0.18])
RED = np.array([0.66, 0.12, 0.10])
RED_ROUTE = {"trunk", "primary", "trunk_link", "primary_link"}


class Builder:
    def __init__(self, scene: Scene, ground: Ground):
        self.scene = scene
        self.ground = ground
        self.v: dict[str, list] = {"marking_white": [], "marking_yellow": [], "marking_red": []}

    def strip(self, group: str, color: np.ndarray, pts: np.ndarray, width: float) -> None:
        """Quad strip along a polyline (already on the road plane)."""
        if len(pts) < 2:
            return
        if len(pts) > 2:
            from shapely.geometry import LineString

            pts = np.asarray(LineString(pts).simplify(0.02).coords)
        d = np.gradient(pts, axis=0)
        d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
        n = np.column_stack([-d[:, 1], d[:, 0]]) * (width / 2)
        left, right = pts + n, pts - n
        xy = np.empty((2 * len(pts), 2))
        xy[0::2], xy[1::2] = left, right
        z = drape(self.scene, self.ground, xy, PARAMS.pavement_lift + PARAMS.marking_lift)
        v = np.column_stack([xy, z])
        t = []
        for i in range(len(pts) - 1):
            a, b, c, e = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
            t.append([b, c, a])
            t.append([b, e, c])
        t = np.array(t)
        # Ensure upward normals.
        nrm = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        flip = nrm[:, 2] < 0
        t[flip] = t[flip][:, [0, 2, 1]]
        self.v[group].append(Piece(v, t, np.tile([0, 0, 1.0], (len(v), 1)), np.tile(color, (len(v), 1)), xy / 2))

    def rect(self, group, color, center, along, half_l, half_w):
        p0 = center - along * half_l
        p1 = center + along * half_l
        self.strip(group, color, np.array([p0, p1]), half_w * 2)


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


def emit(col: Collector, scene: Scene, ground: Ground) -> list[dict]:
    """Emit marking meshes; return crossing records for props (beacons, signals)."""
    B = Builder(scene, ground)
    roads = [r for r in ground.roads if r.kind != "cycleway"]
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
        width = r.left + r.right
        two_way = not r.oneway
        lanes = r.lanes or (2 if two_way else max(1, int(np.median(width) // 3.3)))
        if r.kind not in ("service",) and np.median(width) >= 5.5:
            centre = lambda L, R: (L - R) / 2
            if two_way:
                pts, ss = _resample(r, centre)
                B_lines = _cut(_dashes(pts, ss, 4.0, 5.0), forbidden)
                for ln in B_lines:
                    B.strip("marking_white", WHITE, ln, 0.13)
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
                            B.strip("marking_white", WHITE, ln, 0.1)
            # Double no-waiting lines along both kerbs.
            if r.kind not in ("service", "busway"):
                group, color = ("marking_red", RED) if r.kind in RED_ROUTE else ("marking_yellow", YELLOW)
                for side in (1, -1):
                    for k, inset in enumerate((0.25, 0.47)):
                        off = (lambda L, R, side=side, inset=inset: L - inset) if side == 1 else (lambda L, R, inset=inset: -(R - inset))
                        pts, ss = _resample(r, off)
                        for ln in _cut([pts], forbidden):
                            B.strip(group, color, ln, 0.1)

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
                B.rect("marking_white", WHITE, c + across * off, along, 1.6, 0.3)
            # Zig-zags on both approaches along both kerbs and the centre.
            for sgn in (1, -1):
                for lat in (-half + 0.35, half - 0.35, 0.0):
                    zz = []
                    for j in range(17):
                        d = sgn * (3.0 + j * 0.9)
                        wob = 0.25 * (1 if j % 2 else -1)
                        zz.append(c + along * d + across * (lat + wob))
                    B.strip("marking_white", WHITE, np.array(zz), 0.1)
            # Give-way line (dashes) 1 m before each side.
            for sgn in (1, -1):
                for k in range(int(L + R)):
                    off = -half + 0.5 + k
                    B.rect("marking_white", WHITE, c + along * sgn * 2.4 + across * off, across, 0.3, 0.1)
        else:
            # Signalised crossing: two lines of 200 mm studs 2.4 m apart.
            for sgn in (1, -1):
                k = 0.0
                while k < L + R:
                    B.rect("marking_white", WHITE, c + along * sgn * 1.4 + across * (-half + k + 0.2), along, 0.1, 0.1)
                    k += 0.6
            # Stop lines on the approach half (left-hand traffic).
            if not r.oneway:
                B.rect("marking_white", WHITE, c - along * 3.4 + across * (half / 2), across, half / 2, 0.15)
                B.rect("marking_white", WHITE, c + along * 3.4 - across * (half / 2), across, half / 2, 0.15)
            else:
                B.rect("marking_white", WHITE, c - along * 3.4, across, half, 0.15)
        records.append({"kind": kind, "c": c, "along": along, "across": across, "half": half})

    from .mesh import merge

    for group, pieces in B.v.items():
        if pieces:
            col.add_by_centroid((0,), group, merge(pieces))
    return records
