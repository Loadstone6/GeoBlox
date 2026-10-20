"""OpenStreetMap parsing into BNG shapely geometries.

Builds closed areas from ways and from multipolygon relations (ring merging),
open lines for linear features, and tagged points.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, polygonize, unary_union

from .geo import Projector


@dataclass
class Feature:
    id: str
    tags: dict
    geom: object  # shapely geometry in BNG metres
    nodes: list[int] = field(default_factory=list)


@dataclass
class OsmData:
    nodes_xy: dict[int, tuple[float, float]]
    ways: dict[int, dict]
    relations: dict[int, dict]
    points: list[Feature]
    lines: list[Feature]
    areas: list[Feature]

    def areas_where(self, pred) -> list[Feature]:
        return [f for f in self.areas if pred(f.tags)]

    def lines_where(self, pred) -> list[Feature]:
        return [f for f in self.lines if pred(f.tags)]

    def points_where(self, pred) -> list[Feature]:
        return [f for f in self.points if pred(f.tags)]


AREA_KEYS = {
    "building",
    "building:part",
    "landuse",
    "leisure",
    "natural",
    "amenity",
    "area:highway",
    "man_made",
    "place",
    "tourism",
    "water",
    "waterway",
}

# Linear features that are areas only when tagged area=yes.
LINEAR_UNLESS_AREA = {"highway", "barrier", "railway", "waterway"}


def _is_area(tags: dict, closed: bool) -> bool:
    if not closed:
        return False
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    if any(k in tags for k in LINEAR_UNLESS_AREA) and not any(
        k in tags for k in ("building", "building:part", "area:highway", "landuse", "leisure", "amenity", "man_made")
    ):
        return tags.get("highway") in ("pedestrian",) and tags.get("area") == "yes"
    if tags.get("natural") in ("tree_row", "coastline", "cliff"):
        return False
    if tags.get("man_made") in ("embankment", "pier") and "area" not in tags:
        return False
    if tags.get("barrier") and not any(k in tags for k in ("building", "landuse", "leisure", "amenity")):
        return False
    return any(k in tags for k in AREA_KEYS)


def _ring_merge(segments: list[list[tuple[float, float]]]) -> list[Polygon]:
    lines = [LineString(s) for s in segments if len(s) >= 2]
    if not lines:
        return []
    merged = linemerge(lines)
    return [p for p in polygonize(merged) if p.area > 0.01]


def parse(osm: dict, proj: Projector) -> OsmData:
    elements = osm["elements"]
    node_ids, lons, lats = [], [], []
    node_tags: dict[int, dict] = {}
    ways: dict[int, dict] = {}
    relations: dict[int, dict] = {}
    for e in elements:
        t = e["type"]
        if t == "node":
            node_ids.append(e["id"])
            lons.append(e["lon"])
            lats.append(e["lat"])
            if e.get("tags"):
                node_tags[e["id"]] = e["tags"]
        elif t == "way":
            ways[e["id"]] = e
        elif t == "relation":
            relations[e["id"]] = e

    xs, ys = proj.to_bng(np.array(lons), np.array(lats))
    nodes_xy = {nid: (float(x), float(y)) for nid, x, y in zip(node_ids, xs, ys)}

    points = [Feature(f"n{nid}", tags, Point(nodes_xy[nid])) for nid, tags in node_tags.items() if nid in nodes_xy]

    lines: list[Feature] = []
    areas: list[Feature] = []
    for wid, w in ways.items():
        tags = w.get("tags", {})
        nds = [n for n in w.get("nodes", []) if n in nodes_xy]
        if len(nds) < 2:
            continue
        coords = [nodes_xy[n] for n in nds]
        closed = len(nds) >= 4 and nds[0] == nds[-1]
        if tags and _is_area(tags, closed):
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = make_valid(poly)
            if poly.area > 0.05:
                areas.append(Feature(f"w{wid}", tags, poly, nds))
        if tags and (not closed or not _is_area(tags, closed) or "highway" in tags or "barrier" in tags):
            lines.append(Feature(f"w{wid}", tags, LineString(coords), nds))

    for rid, r in relations.items():
        tags = r.get("tags", {})
        if tags.get("type") not in ("multipolygon", "building"):
            continue
        outer, inner = [], []
        for m in r.get("members", []):
            if m["type"] != "way" or m["ref"] not in ways:
                continue
            nds = [n for n in ways[m["ref"]].get("nodes", []) if n in nodes_xy]
            seg = [nodes_xy[n] for n in nds]
            (inner if m.get("role") == "inner" else outer).append(seg)
        outers = _ring_merge(outer)
        inners = _ring_merge(inner)
        if not outers:
            continue
        geom = unary_union(outers)
        if inners:
            geom = geom.difference(unary_union(inners))
        geom = make_valid(geom)
        if geom.area > 0.05:
            areas.append(Feature(f"r{rid}", tags, geom))

    return OsmData(nodes_xy, ways, relations, points, lines, areas)


def polygons(geom) -> list[Polygon]:
    """Flatten any geometry to a list of polygons."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(polygons(g))
        return out
    return []


def water_from_overpass_geom(obj: dict, thames_ways: dict, proj: Projector, clip: Polygon) -> list[Feature]:
    """Water areas: small ways/relations with geometry, plus the Thames assembled
    from its full member ways and closed against the clip box."""
    feats: list[Feature] = []

    def ll(geom_list):
        pts = [(g["lon"], g["lat"]) for g in geom_list if g]
        if len(pts) < 2:
            return None
        lon, lat = np.array(pts).T
        x, y = proj.to_bng(lon, lat)
        return list(zip(x, y))

    for e in obj["elements"]:
        tags = e.get("tags", {})
        if e["type"] == "way" and e.get("geometry"):
            c = ll(e["geometry"])
            if c and len(c) >= 4:
                poly = make_valid(Polygon(c))
                if poly.area > 1:
                    feats.append(Feature(f"w{e['id']}", tags, poly))
        elif e["type"] == "relation" and e["id"] != 28934:
            outer = [ll(m.get("geometry", [])) for m in e.get("members", []) if m.get("role") != "inner"]
            polys = _ring_merge([c for c in outer if c])
            if polys:
                feats.append(Feature(f"r{e['id']}", tags, make_valid(unary_union(polys))))

    # Thames: bank lines + clip box boundary -> faces; keep faces whose interior is river.
    banks = []
    for e in thames_ways["elements"]:
        c = ll(e.get("geometry", []))
        if c:
            banks.append(LineString(c))
    if banks:
        noded = unary_union(banks + [clip.exterior])
        faces = [f for f in polygonize(noded) if f.within(clip.buffer(1.0))]
        feats.append(Feature("thames-candidates", {"natural": "water", "water": "river"}, MultiPolygon(faces)))
    return feats
