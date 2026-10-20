"""Ground model: surface classification, carriageway geometry from measured
widths, hardscape/pavement and kerb meshes, water bodies and terrain columns.

Carriageway width is *measured*, not assumed: from stations every ~5 m along
each road centreline, perpendicular rays are cast to the mapped kerb lines
(OSM barrier=kerb) and to building fronts. A kerb hit gives the half-width
directly; otherwise the default width (from lanes or road class) is capped so
that at least ~2 m of pavement remains in front of buildings. Half-widths are
median-filtered along the road before the carriageway polygon is swept.

Roads through building passages (Admiralty Arch) and under railway viaducts
are kept at street level. Bridge ways split into stretches on the ground and
elevated deck stretches, which are built as parts (bridges.py). Heights and
terrain come from surface.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .geomutil import grid_points
from .osm import Feature, polygons
from .scene import Scene

MOTOR = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service",
    "living_street", "busway", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "road",
}
DEFAULT_WIDTH = {
    "trunk": 14.0, "primary": 12.0, "secondary": 10.0, "tertiary": 9.0, "unclassified": 7.0, "residential": 7.0,
    "service": 4.6, "living_street": 6.0, "busway": 7.0, "road": 7.0,
}
LANE_W = 3.25

# Terrain material codes (mirrored in the Roblox runtime).
MAT = {"none": 0, "asphalt": 1, "pavement": 2, "grass": 3, "leafygrass": 4, "ground": 5, "concrete": 6, "water": 7, "mud": 8, "slate": 9, "sand": 10, "cobble": 11, "brick": 12}
SURFACE_MAT = {
    "gravel": MAT["sand"], "fine_gravel": MAT["sand"], "compacted": MAT["sand"], "pebblestone": MAT["sand"],
    "sett": MAT["cobble"], "cobblestone": MAT["cobble"], "unhewn_cobblestone": MAT["cobble"],
    "concrete": MAT["concrete"], "grass": MAT["grass"], "asphalt": MAT["asphalt"],
}


@dataclass
class Road:
    feature: Feature
    line: LineString
    stations: np.ndarray  # (k, 2)
    tangents: np.ndarray  # (k, 2)
    left: np.ndarray  # (k,) half widths
    right: np.ndarray
    kind: str
    lanes: int
    oneway: bool
    bridge: bool
    measured: float = 0.0  # fraction of half-width samples taken from mapped kerbs
    passage: bool = False  # runs through a building at street level (arch, passage)
    deck_z: np.ndarray | None = None  # per-station deck height for elevated stretches


@dataclass
class Water:
    poly: Polygon
    level: float
    depth: float
    kind: str


@dataclass
class Ground:
    carriageway: object
    hardscape: object
    green: object
    paths: object
    water: list[Water]
    roads: list[Road]
    bridge_decks: list  # filled by bridges.py: [(polygon, deck_z_fn)]
    kerb_lines: list[LineString]
    deck_roads: list[Road] = None  # elevated stretches of bridge ways (built as parts)
    surfaces: list = None  # [(polygon, terrain material code)] from OSM surface tags


def _num(v, default=None):
    try:
        return float(str(v).split(";")[0].split()[0])
    except (TypeError, ValueError):
        return default


def _default_width(tags: dict) -> float:
    w = _num(tags.get("width"))
    if w and 2.0 < w < 40:
        return w
    lanes = _num(tags.get("lanes"))
    hw = tags.get("highway", "road").replace("_link", "")
    if lanes:
        w = lanes * LANE_W
        if tags.get("cycleway") in ("lane", "track") or tags.get("cycleway:both") == "lane":
            w += 1.5
        return float(np.clip(w, 3.0, 26.0))
    w = DEFAULT_WIDTH.get(hw, 6.0)
    if tags.get("oneway") in ("yes", "1", "-1"):
        w *= 0.7
    return w


def _segments(geoms) -> np.ndarray:
    segs = []
    for g in geoms:
        if g.is_empty:
            continue
        lines = []
        if g.geom_type in ("Polygon",):
            lines = [g.exterior, *g.interiors]
        elif g.geom_type in ("LineString", "LinearRing"):
            lines = [g]
        elif hasattr(g, "geoms"):
            for h in g.geoms:
                segs.append(_segments([h]))
            continue
        for ln in lines:
            c = np.asarray(ln.coords)
            if len(c) >= 2:
                segs.append(np.stack([c[:-1], c[1:]], axis=1))
    return np.vstack(segs) if segs else np.zeros((0, 2, 2))


def ray_cast(origins: np.ndarray, dirs: np.ndarray, max_len: float, segs: np.ndarray, tree=None) -> np.ndarray:
    """Distance along each ray to the nearest segment hit (inf if none).
    Pass a prebuilt STRtree of the segments when casting many batches."""
    out = np.full(len(origins), np.inf)
    if len(segs) == 0 or len(origins) == 0:
        return out
    if tree is None:
        tree = shapely.STRtree(shapely.linestrings(segs))
    rays = shapely.linestrings(np.stack([origins, origins + dirs * max_len], axis=1))
    ri, si = tree.query(rays)
    if len(ri) == 0:
        return out
    o, d = origins[ri], dirs[ri]
    a, b = segs[si, 0], segs[si, 1]
    e = b - a
    den = d[:, 0] * e[:, 1] - d[:, 1] * e[:, 0]
    ok = np.abs(den) > 1e-9
    den = np.where(ok, den, 1.0)
    w = a - o
    t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
    u = (w[:, 0] * d[:, 1] - w[:, 1] * d[:, 0]) / den
    hit = ok & (t > 0.05) & (t <= max_len) & (u >= 0) & (u <= 1)
    np.minimum.at(out, ri[hit], t[hit])
    return out


def _stations(line: LineString, step: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    L = line.length
    n = max(2, int(np.ceil(L / step)) + 1)
    ds = np.linspace(0, L, n)
    pts = np.array([line.interpolate(d).coords[0] for d in ds])
    ahead = np.array([line.interpolate(min(L, d + 1.0)).coords[0] for d in ds])
    behind = np.array([line.interpolate(max(0.0, d - 1.0)).coords[0] for d in ds])
    tan = ahead - behind
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-9)
    return pts, tan


def _median_filter(x: np.ndarray, k: int = 5) -> np.ndarray:
    if len(x) < 3:
        return x
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    return np.array([np.median(xp[i : i + k]) for i in range(len(x))])


def build_roads(scene: Scene, building_polys: list[Polygon]) -> list[Road]:
    kerbs = [f.geom for f in scene.osm.lines if f.tags.get("barrier") == "kerb"]
    kerb_segs = _segments(kerbs)
    bld_segs = _segments(building_polys)
    kerb_tree = shapely.STRtree(shapely.linestrings(kerb_segs)) if len(kerb_segs) else None
    bld_tree = shapely.STRtree(shapely.linestrings(bld_segs)) if len(bld_segs) else None
    roads: list[Road] = []
    for f in scene.osm.lines:
        t = f.tags
        hw = t.get("highway")
        if hw not in MOTOR and hw != "cycleway":
            continue
        if t.get("area") == "yes":
            continue
        layer = _num(t.get("layer"), 0) or 0
        tunnel = t.get("tunnel")
        passage = tunnel in ("building_passage", "covered") and layer >= 0
        # Streets under railway viaducts are mapped as layer -1 tunnels but run
        # at street level; deeper or service tunnels are genuinely underground.
        under_viaduct = tunnel == "yes" and layer == -1 and hw not in ("service", "cycleway")
        if tunnel not in (None, "no") and not (passage or under_viaduct):
            continue
        if layer <= -2 or (layer < 0 and not under_viaduct):
            continue
        if t.get("service") in ("parking_aisle", "drive-through") and hw == "service":
            pass
        line = f.geom
        if line.length < 1.0 or not line.intersects(scene.fetch_clip):
            continue
        pts, tan = _stations(line)
        nrm = np.column_stack([-tan[:, 1], tan[:, 0]])
        if hw == "cycleway":
            default = 3.0 if t.get("oneway") not in ("yes", "1") else 2.0
        else:
            default = _default_width(t)
        half = default / 2
        kl = ray_cast(pts, nrm, 16.0, kerb_segs, kerb_tree)
        kr = ray_cast(pts, -nrm, 16.0, kerb_segs, kerb_tree)
        bl_ = ray_cast(pts, nrm, 30.0, bld_segs, bld_tree)
        br_ = ray_cast(pts, -nrm, 30.0, bld_segs, bld_tree)
        left = np.full(len(pts), half)
        right = np.full(len(pts), half)
        # Kerb hits within a plausible band override the default.
        okl = (kl > 1.4) & (kl < max(2.2 * half, half + 4.0))
        okr = (kr > 1.4) & (kr < max(2.2 * half, half + 4.0))
        left[okl] = kl[okl]
        right[okr] = kr[okr]
        # Otherwise never closer than ~2 m to a building front.
        left[~okl] = np.minimum(left[~okl], np.maximum(1.4, bl_[~okl] - 2.0))
        right[~okr] = np.minimum(right[~okr], np.maximum(1.4, br_[~okr] - 2.0))
        left, right = _median_filter(left), _median_filter(right)
        roads.append(
            Road(
                f, line, pts, tan, left, right, hw,
                int(_num(t.get("lanes"), 0) or 0),
                t.get("oneway") in ("yes", "1", "-1") or hw.endswith("_link") and t.get("oneway") != "no",
                t.get("bridge") not in (None, "no"),
                float((okl.sum() + okr.sum()) / (2 * len(okl))),
                passage or under_viaduct,
            )
        )
    return roads


def _slice(r: Road, i0: int, i1: int) -> Road:
    from shapely.geometry import LineString as _LS

    st = r.stations[i0 : i1 + 1]
    out = Road(r.feature, _LS(st), st, r.tangents[i0 : i1 + 1], r.left[i0 : i1 + 1], r.right[i0 : i1 + 1], r.kind, r.lanes, r.oneway, r.bridge, r.measured, r.passage)
    if r.deck_z is not None:
        out.deck_z = r.deck_z[i0 : i1 + 1]
    return out


def split_bridges(scene: Scene, roads: list[Road], river) -> tuple[list[Road], list[Road]]:
    """Bridge ways -> (stretches on the ground, elevated deck stretches).

    The deck height at each station is a low percentile of the first-return
    DSM across the carriageway (vehicles and lamp columns are outliers),
    median-filtered along the way. A stretch is elevated where the deck stands
    clear of the bare-earth DTM or crosses the river (the EA DTM keeps some
    decks, e.g. Lambeth Bridge, so height alone is not enough)."""
    grounded, decks = [], []
    for r in roads:
        if not r.bridge or len(r.stations) < 2:
            grounded.append(r)
            continue
        nrm = np.column_stack([-r.tangents[:, 1], r.tangents[:, 0]])
        zs = []
        for f in np.linspace(-0.8, 0.8, 7):
            off = np.where(f >= 0, r.left, r.right) * abs(f)
            p = r.stations + nrm * (np.sign(f) * off)[:, None]
            zs.append(scene.dsm.sample(p[:, 0], p[:, 1]))
        zd = np.percentile(np.array(zs), 30, axis=0)
        zd = _median_filter(zd, 5)
        zg = scene.dtm.sample(r.stations[:, 0], r.stations[:, 1])
        wet = shapely.contains_xy(river, r.stations[:, 0], r.stations[:, 1]) if river is not None and not river.is_empty else np.zeros(len(zd), bool)
        up = (zd - zg > 0.6) | wet
        r.deck_z = zd
        i = 0
        n = len(up)
        while i < n:
            j = i
            while j + 1 < n and up[j + 1] == up[i]:
                j += 1
            if up[i]:
                decks.append(_slice(r, max(0, i - 1), min(n - 1, j + 1)))
            else:
                grounded.append(_slice(r, max(0, i - (1 if i > 0 else 0)), min(n - 1, j + (1 if j < n - 1 else 0))))
            i = j + 1
    return grounded, decks


def road_polygon(r: Road) -> Polygon:
    nrm = np.column_stack([-r.tangents[:, 1], r.tangents[:, 0]])
    lp = r.stations + nrm * r.left[:, None]
    rp = r.stations - nrm * r.right[:, None]
    ring = np.vstack([lp, rp[::-1]])
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    # Round caps so junctions knit together.
    caps = [shapely.Point(r.stations[i]).buffer(float(max(r.left[i], r.right[i])), 12) for i in (0, -1)]
    return unary_union([poly, *caps])


def thames_and_lakes(scene: Scene) -> list[Water]:
    out: list[Water] = []
    dsm, dtm = scene.dsm, scene.dtm
    # Thames: candidate faces whose LiDAR surface is flat water.
    for f in scene.water_raw:
        if f.id != "thames-candidates":
            continue
        faces = polygons(f.geom)
        stats = []
        for p in faces:
            if p.area < 1500:
                continue
            g = grid_points(p, 6.0, 1.0)
            if len(g) < 20:
                continue
            z = dsm.sample(g[:, 0], g[:, 1])
            stats.append((p, float(np.median(z)), float(np.percentile(z, 90) - np.percentile(z, 10))))
        if not stats:
            continue
        # The river is the large face with the lowest, flattest surface.
        stats.sort(key=lambda s: (s[1], -s[0].area))
        level = stats[0][1]
        river = [s[0] for s in stats if abs(s[1] - level) < 1.0 and s[2] < 2.5]
        if river:
            scene.water_level = level
            out.append(Water(unary_union(river), level, 6.0, "river"))
    for f in scene.water_raw:
        if f.id == "thames-candidates":
            continue
        for p in polygons(f.geom):
            if p.area < 3:
                continue
            ring = np.array(p.exterior.coords)
            rim = float(np.median(dtm.sample(ring[:, 0], ring[:, 1])))
            kind = f.tags.get("water", "pond")
            if p.area > 2000:
                out.append(Water(p, rim - 0.25, 1.4, "lake"))
            else:
                out.append(Water(p, rim + 0.25, 0.45, "fountain" if "Fountain" in f.tags.get("name", "") or p.area < 400 else kind))
    return out


GREEN_TAGS = (
    ("leisure", {"park", "garden", "pitch", "playground", "common", "golf_course"}),
    ("landuse", {"grass", "recreation_ground", "meadow", "village_green", "flowerbed", "cemetery", "allotments"}),
    ("natural", {"scrub", "wood", "grassland", "heath"}),
)


def build(scene: Scene, building_polys: list[Polygon]) -> Ground:
    clip = scene.fetch_clip
    all_roads = build_roads(scene, building_polys)
    water = thames_and_lakes(scene)
    river = unary_union([w.poly for w in water if w.kind == "river"]) if water else None
    roads, decks = split_bridges(scene, all_roads, river)
    open_roads = unary_union([road_polygon(r) for r in roads if not r.passage])
    passages = unary_union([road_polygon(r) for r in roads if r.passage])
    carriage = open_roads.buffer(0.8, join_style="round").buffer(-0.8, join_style="round")
    # Traffic islands (area:highway=traffic_island / footway areas) cut back out.
    islands = [f.geom for f in scene.osm.areas if f.tags.get("area:highway") in ("traffic_island", "footway", "pedestrian", "sidewalk")]
    pedestrian = [f.geom.buffer(2.0) for f in scene.osm.lines if f.tags.get("highway") == "pedestrian"]
    pedestrian += [f.geom for f in scene.osm.areas if f.tags.get("highway") == "pedestrian" or f.tags.get("place") == "square"]
    if islands:
        carriage = carriage.difference(unary_union(islands))
    buildings = unary_union(building_polys)
    water_u = unary_union([w.poly for w in water]) if water else Polygon()
    carriage = carriage.difference(buildings.buffer(0.3))
    if not passages.is_empty:
        carriage = carriage.union(passages)
    carriage = carriage.difference(water_u.buffer(0.5)).intersection(clip)
    green_polys = []
    for f in scene.osm.areas:
        for key, vals in GREEN_TAGS:
            if f.tags.get(key) in vals:
                green_polys.append(f.geom)
    green = unary_union(green_polys) if green_polys else Polygon()
    # Paths through parks become surfaced paths in the grass.
    park_paths = [
        f.geom.buffer(1.4 if f.tags.get("highway") != "pedestrian" else 3.0, cap_style="flat")
        for f in scene.osm.lines
        if f.tags.get("highway") in ("footway", "path", "pedestrian", "cycleway", "bridleway") and f.geom.intersects(green)
    ]
    paths = unary_union(park_paths).intersection(green) if park_paths else Polygon()
    green = green.difference(paths).difference(carriage).difference(buildings).difference(water_u)
    hard = clip.difference(carriage).difference(buildings).difference(green).difference(water_u)
    surfaces = []
    for f in scene.osm.areas:
        code = SURFACE_MAT.get(f.tags.get("surface", ""))
        if code is not None and (f.tags.get("highway") in ("pedestrian", "footway", "service") or f.tags.get("area:highway") or f.tags.get("leisure") or f.tags.get("place") == "square" or f.tags.get("amenity") == "parking"):
            surfaces.append((f.geom.intersection(hard), code))
    hard = hard.buffer(0)
    kerb_lines = []
    boundary = hard.boundary.intersection(carriage.buffer(0.05))
    from shapely.ops import linemerge

    merged = linemerge([g for g in getattr(boundary, "geoms", [boundary]) if g.geom_type == "LineString"]) if not boundary.is_empty else boundary
    merged = merged.intersection(scene.clip) if not merged.is_empty else merged
    for g in getattr(merged, "geoms", [merged]):
        if g.geom_type == "LineString" and g.length > 0.3:
            kerb_lines.append(g.simplify(0.08))
    return Ground(carriage, hard, green, paths, water, roads, [], kerb_lines, decks, surfaces)
