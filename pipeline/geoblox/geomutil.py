"""Polygon triangulation and small geometry helpers."""

from __future__ import annotations

import re

import numpy as np
import triangle
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient


def _weld(points: np.ndarray, segments: list[tuple[int, int]]):
    key = np.round(points * 1000).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.ravel()
    order = np.argsort(first)
    new_id = np.empty(len(first), dtype=np.int64)
    new_id[order] = np.arange(len(first))
    remap = new_id[inverse]
    pts = points[first[order]]
    segs = set()
    for a, b in segments:
        a, b = int(remap[a]), int(remap[b])
        if a != b:
            segs.add((min(a, b), max(a, b)))
    return pts, np.array(sorted(segs), dtype=np.int32).reshape(-1, 2), remap


def triangulate(poly: Polygon, steiner: np.ndarray | None = None, extra_segments: list[np.ndarray] | None = None, quality: str = "") -> tuple[np.ndarray, np.ndarray]:
    """Constrained Delaunay triangulation of a polygon with holes.

    `steiner` adds interior points (e.g. a grid for draping or curved roofs);
    `extra_segments` adds constraint polylines (e.g. a roof ridge).
    Returns (vertices (n, 2), triangles (m, 3)) with counter-clockwise triangles.
    """
    poly = orient(poly, 1.0)
    pts, segs = [], []
    off = 0
    for ring in [poly.exterior, *poly.interiors]:
        c = np.array(ring.coords)[:-1]
        if len(c) < 3:
            continue
        pts.append(c)
        segs.extend([(off + i, off + (i + 1) % len(c)) for i in range(len(c))])
        off += len(c)
    interior = []
    if extra_segments:
        # Constraint lines that touch the outline make Triangle split boundary
        # segments at numerically-collinear points, which can abort the process.
        # Densify them into Steiner points instead (Delaunay keeps the creases).
        for line in extra_segments:
            line = np.asarray(line, dtype=np.float64)
            for i in range(len(line) - 1):
                a, b = line[i], line[i + 1]
                k = max(1, int(np.linalg.norm(b - a) / 0.45))
                interior.extend(a + (b - a) * (j / k) for j in range(k + 1))
    if steiner is not None and len(steiner):
        interior.extend(np.asarray(steiner, dtype=np.float64))
    if interior:
        import shapely

        cand = np.asarray(interior)
        keep = shapely.contains_xy(poly, cand[:, 0], cand[:, 1])
        keep &= shapely.distance(poly.boundary, shapely.points(cand)) > 0.06
        cand = cand[keep]
        if len(cand):
            pts.append(cand)
            off += len(cand)
    if not pts:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)
    P, S, _ = _weld(np.vstack(pts).astype(np.float64), segs)
    if len(P) < 3 or len(S) < 3:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)
    data = {"vertices": P, "segments": S}
    holes = [Polygon(h).representative_point().coords[0] for h in poly.interiors if Polygon(h).area > 1e-6]
    if holes:
        data["holes"] = np.array(holes, dtype=np.float64)
    try:
        out = triangle.triangulate(data, "pQ" + quality)
    except Exception:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)
    V = out["vertices"]
    T = out["triangles"].astype(np.int64)
    # Ensure CCW (positive area) orientation.
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    T[area < 0] = T[area < 0][:, [0, 2, 1]]
    return V, T


def grid_points(poly: Polygon, spacing: float, inset: float = 0.0) -> np.ndarray:
    import shapely

    g = poly.buffer(-max(inset, spacing * 0.3)) if inset >= 0 else poly
    if g.is_empty:
        return np.zeros((0, 2))
    e0, n0, e1, n1 = g.bounds
    xs = np.arange(e0 + spacing / 2, e1, spacing)
    ys = np.arange(n0 + spacing / 2, n1, spacing)
    if len(xs) == 0 or len(ys) == 0:
        return np.zeros((0, 2))
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    inside = shapely.contains_xy(g, gx, gy)
    return np.column_stack([gx[inside], gy[inside]])


NAMED = {
    "white": (238, 236, 230), "black": (30, 30, 32), "grey": (128, 128, 128), "gray": (128, 128, 128),
    "lightgrey": (190, 190, 186), "lightgray": (190, 190, 186), "darkgrey": (80, 80, 82), "darkgray": (80, 80, 82),
    "silver": (180, 182, 184), "red": (150, 52, 42), "darkred": (110, 36, 30), "brown": (120, 82, 58),
    "beige": (214, 200, 168), "yellow": (214, 182, 80), "gold": (204, 168, 72), "green": (70, 110, 70),
    "darkgreen": (40, 70, 44), "blue": (60, 80, 130), "navy": (32, 40, 80), "cream": (232, 222, 192),
    "orange": (196, 112, 52), "maroon": (110, 40, 36), "tan": (196, 168, 126), "sandybrown": (210, 160, 100),
    "olive": (120, 120, 60), "teal": (50, 110, 110), "pink": (210, 150, 150), "purple": (100, 60, 110),
    "khaki": (200, 190, 140), "ivory": (240, 236, 220), "wheat": (232, 214, 170), "copper": (96, 150, 128),
}


def parse_colour(value: str | None) -> np.ndarray | None:
    if not value:
        return None
    v = value.strip().lower().replace(" ", "")
    m = re.fullmatch(r"#?([0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        return np.array([int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)], dtype=np.float64) / 255.0
    m = re.fullmatch(r"#?([0-9a-f]{3})", v)
    if m:
        h = m.group(1)
        return np.array([int(c * 2, 16) for c in h], dtype=np.float64) / 255.0
    if v in NAMED:
        return np.array(NAMED[v], dtype=np.float64) / 255.0
    return None


def parse_length(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().lower().replace(",", ".")
    m = re.match(r"^(-?[0-9.]+)\s*(m|ft|')?", s)
    if not m:
        return None
    try:
        x = float(m.group(1))
    except ValueError:
        return None
    if m.group(2) in ("ft", "'"):
        x *= 0.3048
    return x


def direction_deg(value) -> float | None:
    if value is None:
        return None
    card = {"n": 0, "nne": 22.5, "ne": 45, "ene": 67.5, "e": 90, "ese": 112.5, "se": 135, "sse": 157.5, "s": 180,
            "ssw": 202.5, "sw": 225, "wsw": 247.5, "w": 270, "wnw": 292.5, "nw": 315, "nnw": 337.5}
    s = str(value).strip().lower()
    if s in card:
        return float(card[s])
    try:
        return float(s)
    except ValueError:
        return None
