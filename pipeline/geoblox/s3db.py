"""Simple 3D Buildings (OSM S3DB) extrusion for landmark-quality parts.

Each building:part is a prism from min_height to height, topped by a roof whose
surface is an analytic height function over the footprint:

  flat       z = top
  gabled     z = top - rh * |v|/W                (v: offset across the ridge, W: half width)
  round      z = base + rh * sqrt(1 - (v/W)^2)    (barrel vault)
  hipped     z = base + rh * min(1, d/dmax)       (d: distance to the outline ≈ straight skeleton)
  pyramidal  z = base + rh * (1 - max(|u|/L, |v|/W))
  dome       z = base + rh * sqrt(1 - (1 - d/dmax)^2)
  skillion   z = top - rh * t                     (t: normalised position along roof:direction)

The footprint is triangulated (constrained Delaunay) with ridge constraints or
a Steiner grid fine enough for curved shapes, then each vertex is lifted by the
function. Wall tops follow the same function, so gable ends come out exact.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from .geomutil import direction_deg, grid_points, parse_colour, parse_length, triangulate
from .mesh import Piece, crease_split, face_normals, orient_up

DEFAULT_RH = {"gabled": 0.35, "hipped": 0.35, "pyramidal": 0.9, "dome": 1.0, "onion": 1.6, "round": 0.5, "skillion": 0.25, "gambrel": 0.45, "mansard": 0.3, "cone": 2.0, "half-hipped": 0.35}


def _num(tags: dict, key: str) -> float | None:
    return parse_length(tags.get(key))


def part_heights(tags: dict) -> tuple[float, float] | None:
    h1 = _num(tags, "height")
    if h1 is None and _num(tags, "building:levels") is not None:
        h1 = _num(tags, "building:levels") * 3.2 + (_num(tags, "roof:levels") or 0) * 2.8
    if h1 is None:
        return None
    h0 = _num(tags, "min_height")
    if h0 is None and _num(tags, "building:min_level") is not None:
        h0 = _num(tags, "building:min_level") * 3.2
    return (h0 or 0.0), h1


def _frame(poly: Polygon, tags: dict):
    rect = poly.minimum_rotated_rectangle
    c = np.array(rect.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) >= np.linalg.norm(e2):
        u, L, v, W = e1 / np.linalg.norm(e1), np.linalg.norm(e1) / 2, e2 / max(np.linalg.norm(e2), 1e-9), np.linalg.norm(e2) / 2
    else:
        u, L, v, W = e2 / np.linalg.norm(e2), np.linalg.norm(e2) / 2, e1 / max(np.linalg.norm(e1), 1e-9), np.linalg.norm(e1) / 2
    if tags.get("roof:orientation") == "across":
        u, v, L, W = v, u, W, L
    center = c.mean(axis=0)
    return center, u, v, max(L, 0.05), max(W, 0.05), rect


def _boundary_distance(poly: Polygon, xy: np.ndarray) -> np.ndarray:
    if len(xy) == 0:
        return np.zeros(0)
    return shapely.distance(poly.exterior, shapely.points(xy)) if not poly.interiors else shapely.distance(poly.boundary, shapely.points(xy))


def roof_function(poly: Polygon, tags: dict, h0: float, h1: float):
    shape = tags.get("roof:shape", "flat").lower()
    if shape in ("half-hipped", "mansard"):
        shape = "hipped"
    if shape in ("gambrel", "saltbox"):
        shape = "gabled"
    if shape not in DEFAULT_RH and shape != "flat":
        shape = "flat"
    center, u, v, L, W, rect = _frame(poly, tags)
    rh = _num(tags, "roof:height")
    if rh is None and _num(tags, "roof:levels") is not None:
        rh = _num(tags, "roof:levels") * 2.8
    if shape == "flat":
        rh = 0.0
    if rh is None:
        rh = DEFAULT_RH.get(shape, 0.0) * (W * 2 if shape in ("dome", "onion", "cone", "pyramidal") else W * 2)
        rh = min(rh, max(0.0, (h1 - h0) * 0.6))
    rh = float(np.clip(rh, 0.0, max(0.0, h1 - h0)))
    base = h1 - rh
    dmax = max(1e-3, shapely.distance(poly.boundary, shapely.points([poly.representative_point().coords[0]]))[0])
    # better dmax estimate: max over a grid
    g = grid_points(poly, max(0.25, min(L, W) / 6), 0.0)
    if len(g):
        dmax = max(dmax, float(_boundary_distance(poly, g).max()))
    direction = direction_deg(tags.get("roof:direction"))
    if direction is not None:
        rad = np.radians(direction)
        down = np.array([np.sin(rad), np.cos(rad)])
    else:
        down = v
    ring = np.array(poly.exterior.coords)[:-1]
    proj_all = (ring - center) @ down
    tmin, tmax = float(proj_all.min()), float(proj_all.max())

    def f(xy: np.ndarray) -> np.ndarray:
        d = xy - center
        du, dv = np.abs(d @ u), np.abs(d @ v)
        if shape == "flat" or rh <= 0.01:
            return np.full(len(xy), h1)
        if shape == "gabled":
            return h1 - rh * np.clip(dv / W, 0, 1)
        if shape == "round":
            return base + rh * np.sqrt(np.clip(1 - (dv / W) ** 2, 0, 1))
        if shape == "pyramidal" or shape == "cone":
            return base + rh * np.clip(1 - np.maximum(du / L, dv / W), 0, 1)
        if shape == "skillion":
            t = ((xy - center) @ down - tmin) / max(1e-6, tmax - tmin)
            return h1 - rh * np.clip(t, 0, 1)
        s = np.clip(_boundary_distance(poly, xy) / dmax, 0, 1)
        if shape == "hipped":
            return base + rh * s
        if shape == "dome":
            return base + rh * np.sqrt(np.clip(1 - (1 - s) ** 2, 0, 1))
        if shape == "onion":
            return base + rh * np.clip(np.sqrt(np.clip(1 - (1 - s) ** 2, 0, 1)) * 0.7 + 0.3 * s**3, 0, 1)
        return np.full(len(xy), h1)

    # Constraints / Steiner points for the roof triangulation.
    extra, steiner = None, None
    if shape in ("gabled",) and rh > 0.01:
        ridge = LineString([center - u * (L + 1), center + u * (L + 1)]).intersection(poly)
        lines = [np.array(g.coords) for g in getattr(ridge, "geoms", [ridge]) if not g.is_empty and g.geom_type == "LineString"]
        extra = lines or None
    elif shape in ("pyramidal", "cone") and rh > 0.01:
        steiner = np.array([center]) if poly.contains(shapely.points([center])[0]) else None
        # Hip lines to OBB corners make the pyramid exact on rectangles.
        corners = np.array(rect.exterior.coords)[:4]
        lines = []
        for cc in corners:
            seg = LineString([center, cc]).intersection(poly)
            for gg in getattr(seg, "geoms", [seg]):
                if not gg.is_empty and gg.geom_type == "LineString":
                    lines.append(np.array(gg.coords))
        extra = lines or None
    elif shape != "flat" and rh > 0.01:
        spacing = float(np.clip(min(L, W) / 5, 0.25, 2.5))
        steiner = grid_points(poly, spacing, spacing * 0.3)
    return f, shape, rh, base, extra, steiner


def _densify(ring: np.ndarray, step: float) -> np.ndarray:
    out = []
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        out.append(a)
        k = int(np.linalg.norm(b - a) // step)
        for j in range(1, k + 1):
            out.append(a + (b - a) * j / (k + 1))
    return np.array(out)


def is_gilt(c: np.ndarray) -> bool:
    return c[0] > 0.7 and c[1] > 0.55 and c[2] < 0.5 and (c[0] - c[2]) > 0.3


def build_part(poly: Polygon, tags: dict, ground: float, wall_default: np.ndarray, roof_default: np.ndarray, ao_fn) -> list[tuple[str, Piece, bool]]:
    """Returns [(group, piece, is_wall)]."""
    hh = part_heights(tags)
    if hh is None:
        return []
    h0, h1 = hh
    if h1 - h0 < 0.05 or poly.area < 0.01:
        return []
    poly = orient(poly, 1.0)
    f, shape, rh, base, extra, steiner = roof_function(poly, tags, h0, h1)
    wall_c = parse_colour(tags.get("building:colour"))
    if wall_c is None:
        wall_c = parse_colour(tags.get("colour"))
    roof_c = parse_colour(tags.get("roof:colour"))
    wall_rgb = wall_c if wall_c is not None else wall_default
    roof_rgb = roof_c if roof_c is not None else (wall_rgb if shape == "flat" else roof_default)
    mat = tags.get("building:material", "")
    wall_group = "wall_brick" if mat == "brick" else ("wall_glass" if mat == "glass" else ("wall_metal" if mat == "metal" else "wall_stone"))
    if is_gilt(wall_rgb):
        wall_group = "gilt"
    roof_mat = tags.get("roof:material", "")
    out: list[tuple[str, Piece, bool]] = []

    # Roof.
    V2, T = triangulate(poly, steiner, extra)
    if len(T):
        z = f(V2) + ground
        v3 = np.column_stack([V2, z])
        t = orient_up(v3, T)
        nv, nt, nn, _ = crease_split(v3, t, 32.0)
        ao = ao_fn(nv, nn)
        col = roof_rgb[None, :] * (0.0 * ao + 1.0)[:, None]
        pitched = shape != "flat"
        if is_gilt(roof_rgb) or roof_mat in ("gold", "metal", "copper"):
            group = "gilt" if is_gilt(roof_rgb) else "roof_metal"
        elif roof_mat in ("glass",):
            group = "roof_glass"
        else:
            group = "roof_pitched" if pitched else "roof_flat"
        out.append((group, Piece(nv, nt, nn, col, nv[:, :2] / 2.0), False))
        if h0 > 0.3:
            # Underside of floating parts (clock faces, overhangs).
            vb = np.column_stack([V2, np.full(len(V2), ground + h0)])
            tb = T[:, [0, 2, 1]]
            nb = np.tile([0, 0, -1.0], (len(vb), 1))
            out.append((wall_group, Piece(vb, tb, nb, np.tile(wall_rgb * 0.6, (len(vb), 1)), vb[:, :2] / 2), True))

    # Walls (tops follow the roof function, so gables close exactly).
    bottom = ground + (h0 if h0 > 0.05 else -0.4)
    step = 1.0 if shape in ("gabled", "round", "skillion", "pyramidal", "cone") else 4.0
    pieces = []
    for ring_geom in [poly.exterior, *poly.interiors]:
        ring = _densify(np.array(ring_geom.coords)[:-1], step)
        tops = f(ring) + ground
        n = len(ring)
        dist = 0.0
        for i in range(n):
            a, c = ring[i], ring[(i + 1) % n]
            seg = c - a
            length = float(np.linalg.norm(seg))
            if length < 1e-3:
                continue
            d = seg / length
            nrm = np.array([d[1], -d[0], 0.0])
            za, zc = tops[i], tops[(i + 1) % n]
            p = np.array([[a[0], a[1], bottom], [c[0], c[1], bottom], [c[0], c[1], zc], [a[0], a[1], za]])
            uv = np.array([[dist, bottom - ground], [dist + length, bottom - ground], [dist + length, zc - ground], [dist, za - ground]]) / 2.0
            nn = np.tile(nrm, (4, 1))
            ao = ao_fn(p, nn)
            col = wall_rgb[None, :] * (0.0 * ao + 1.0)[:, None]
            pieces.append(Piece(p, np.array([[0, 1, 2], [0, 2, 3]]), nn, col, uv))
            dist += length
    if pieces:
        from .mesh import merge

        out.append((wall_group, merge(pieces), True))
    return out
