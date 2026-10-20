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

buildparts.py turns these into parts: exact planes for gables, hips and
pyramids, ellipsoids for domes, box strips for barrel vaults. Wall tops follow
the same function, so gable ends come out exact.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

from .geomutil import direction_deg, grid_points, parse_length

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


def is_gilt(c: np.ndarray) -> bool:
    return c[0] > 0.7 and c[1] > 0.55 and c[2] < 0.5 and (c[0] - c[2]) > 0.3
