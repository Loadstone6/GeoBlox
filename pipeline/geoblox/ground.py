"""Ground model: surface classification, carriageway geometry from measured
widths, hardscape/pavement and kerb meshes, water bodies and terrain columns.

Carriageway width is *measured*, not assumed: from stations every ~5 m along
each road centreline, perpendicular rays are cast to the mapped kerb lines
(OSM barrier=kerb) and to building fronts. A kerb hit gives the half-width
directly; otherwise the default width (from lanes or road class) is capped so
that at least ~2 m of pavement remains in front of buildings. Half-widths are
median-filtered along the road before the carriageway polygon is swept.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from .config import PARAMS
from .geomutil import grid_points, triangulate
from .mesh import Collector, Piece
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


def ray_cast(origins: np.ndarray, dirs: np.ndarray, max_len: float, segs: np.ndarray) -> np.ndarray:
    """Distance along each ray to the nearest segment hit (inf if none)."""
    out = np.full(len(origins), np.inf)
    if len(segs) == 0 or len(origins) == 0:
        return out
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
    roads: list[Road] = []
    for f in scene.osm.lines:
        t = f.tags
        hw = t.get("highway")
        if hw not in MOTOR and hw != "cycleway":
            continue
        if t.get("area") == "yes" or t.get("tunnel") in ("yes", "building_passage", "covered") or str(t.get("layer", "0")).startswith("-"):
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
        kl = ray_cast(pts, nrm, 16.0, kerb_segs)
        kr = ray_cast(pts, -nrm, 16.0, kerb_segs)
        bl_ = ray_cast(pts, nrm, 30.0, bld_segs)
        br_ = ray_cast(pts, -nrm, 30.0, bld_segs)
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
            )
        )
    return roads


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
    roads = build_roads(scene, building_polys)
    carriage = unary_union([road_polygon(r) for r in roads if r.kind != "cycleway" or True])
    carriage = carriage.buffer(0.8, join_style="round").buffer(-0.8, join_style="round")
    # Traffic islands (area:highway=traffic_island / footway areas) cut back out.
    islands = [f.geom for f in scene.osm.areas if f.tags.get("area:highway") in ("traffic_island", "footway", "pedestrian", "sidewalk")]
    pedestrian = [f.geom.buffer(2.0) for f in scene.osm.lines if f.tags.get("highway") == "pedestrian"]
    pedestrian += [f.geom for f in scene.osm.areas if f.tags.get("highway") == "pedestrian" or f.tags.get("place") == "square"]
    if islands:
        carriage = carriage.difference(unary_union(islands))
    buildings = unary_union(building_polys)
    water = thames_and_lakes(scene)
    water_u = unary_union([w.poly for w in water]) if water else Polygon()
    carriage = carriage.difference(buildings.buffer(0.3)).difference(water_u.buffer(0.5)).intersection(clip)
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
    hard = hard.buffer(0)
    kerb_lines = []
    boundary = hard.boundary.intersection(carriage.buffer(0.05))
    from shapely.ops import linemerge

    merged = linemerge([g for g in getattr(boundary, "geoms", [boundary]) if g.geom_type == "LineString"]) if not boundary.is_empty else boundary
    for g in getattr(merged, "geoms", [merged]):
        if g.geom_type == "LineString" and g.length > 0.3:
            kerb_lines.append(g.simplify(0.08))
    return Ground(carriage, hard, green, paths, water, roads, [], kerb_lines)


# ---------------------------------------------------------------- meshes

ASPHALT = np.array([0.24, 0.24, 0.25])
PAVING = np.array([0.64, 0.62, 0.58])
GRANITE = np.array([0.55, 0.55, 0.56])


def _noise(xy: np.ndarray, seed: float) -> np.ndarray:
    x, y = xy[:, 0], xy[:, 1]
    return (np.sin(x * 0.071 + seed) * np.cos(y * 0.053 - seed) + np.sin((x + y) * 0.019 + 2 * seed)) * 0.5


def drape(scene: Scene, ground: Ground, xy: np.ndarray, lift: float) -> np.ndarray:
    z = scene.dtm.sample(xy[:, 0], xy[:, 1]) + lift
    for poly, fn in ground.bridge_decks:
        inside = shapely.contains_xy(poly, xy[:, 0], xy[:, 1])
        if inside.any():
            z[inside] = fn(xy[inside]) + lift
    return z


def surface_mesh(scene: Scene, ground: Ground, geom, lift: float, base: np.ndarray, spacing: float, uv_scale: float) -> Piece | None:
    vs, ts, off = [], [], 0
    for poly in polygons(geom):
        if poly.area < 0.5:
            continue
        poly = poly.simplify(0.05, preserve_topology=True)
        steiner = grid_points(poly, spacing, spacing * 0.25)
        V2, T = triangulate(poly, steiner)
        if len(T) == 0:
            continue
        vs.append(V2)
        ts.append(T + off)
        off += len(V2)
    if not vs:
        return None
    V2 = np.vstack(vs)
    T = np.vstack(ts)
    z = drape(scene, ground, V2, lift)
    v = np.column_stack([V2, z])
    n = np.tile([0, 0, 1.0], (len(v), 1))
    shade = 1.0 + 0.06 * _noise(V2, 1.3)
    c = base[None, :] * shade[:, None]
    return Piece(v, T, n, c, V2 / uv_scale)


def kerb_mesh(scene: Scene, ground: Ground) -> Piece | None:
    """Granite kerb: a vertical face (road side) plus a narrow top strip."""
    vs, ts, ns, cs = [], [], [], []
    off = 0
    kh = PARAMS.kerb_height
    for line in ground.kerb_lines:
        c = np.asarray(line.coords)
        for i in range(len(c) - 1):
            a, b = c[i], c[i + 1]
            seg = b - a
            L = np.linalg.norm(seg)
            if L < 0.05:
                continue
            d = seg / L
            n2 = np.array([d[1], -d[0]])
            mid = (a + b) / 2
            # Face the carriageway side.
            if not ground.carriageway.contains(shapely.Point(mid + n2 * 0.25)):
                n2 = -n2
            za, zb = drape(scene, ground, np.array([a, b]), PARAMS.pavement_lift)
            z_road_a, z_road_b = za, zb
            top_a, top_b = za + kh, zb + kh
            n3 = np.array([n2[0], n2[1], 0.0])
            quad_v = np.array([[a[0], a[1], z_road_a - 0.05], [b[0], b[1], z_road_b - 0.05], [b[0], b[1], top_b], [a[0], a[1], top_a]])
            if np.dot(np.cross(quad_v[1] - quad_v[0], quad_v[2] - quad_v[0]), n3) < 0:
                quad_v = quad_v[[1, 0, 3, 2]]
            vs.append(quad_v)
            ts.append(np.array([[0, 1, 2], [0, 2, 3]]) + off)
            ns.append(np.tile(n3, (4, 1)))
            cs.append(np.tile(GRANITE * 0.92, (4, 1)))
            off += 4
            # Top strip (0.18 m wide) on the pavement side.
            inward = -n2 * 0.18
            top = np.array([[a[0], a[1], top_a + 0.004], [b[0], b[1], top_b + 0.004], [b[0] + inward[0], b[1] + inward[1], top_b + 0.004], [a[0] + inward[0], a[1] + inward[1], top_a + 0.004]])
            if np.cross(top[1] - top[0], top[2] - top[0])[2] < 0:
                top = top[[1, 0, 3, 2]]
            vs.append(top)
            ts.append(np.array([[0, 1, 2], [0, 2, 3]]) + off)
            ns.append(np.tile([0, 0, 1.0], (4, 1)))
            cs.append(np.tile(GRANITE, (4, 1)))
            off += 4
    if not vs:
        return None
    v = np.vstack(vs)
    return Piece(v, np.vstack(ts), np.vstack(ns), np.vstack(cs), v[:, :2] / 2)


def river_walls(scene: Scene, ground: Ground) -> Piece | None:
    """Granite embankment walls along the Thames, with a parapet."""
    river = [w for w in ground.water if w.kind == "river"]
    if not river:
        return None
    vs, ts, ns, cs = [], [], [], []
    off = 0
    clip_edge = scene.fetch_clip.exterior.buffer(2.0)
    for w in river:
        for poly in polygons(w.poly):
            ring = np.asarray(poly.exterior.coords)
            for i in range(len(ring) - 1):
                a, b = ring[i], ring[i + 1]
                if clip_edge.contains(shapely.Point((a + b) / 2)):
                    continue
                seg = b - a
                L = np.linalg.norm(seg)
                if L < 0.1:
                    continue
                d = seg / L
                # Exterior ring is CCW: water on the left, so the wall faces left (into the river).
                n2 = np.array([-d[1], d[0]])
                land = (a + b) / 2 - n2 * 1.5
                if ground.bridge_decks and any(p.contains(shapely.Point((a + b) / 2)) for p, _ in ground.bridge_decks):
                    pass
                z_land = float(scene.dtm.sample(np.array([land[0]]), np.array([land[1]]))[0])
                top = max(z_land + 0.95, w.level + 1.0)
                bottom = w.level - 1.5
                n3 = np.array([n2[0], n2[1], 0.0])
                q = np.array([[a[0], a[1], bottom], [b[0], b[1], bottom], [b[0], b[1], top], [a[0], a[1], top]])
                if np.dot(np.cross(q[1] - q[0], q[2] - q[0]), n3) < 0:
                    q = q[[1, 0, 3, 2]]
                vs.append(q)
                ts.append(np.array([[0, 1, 2], [0, 2, 3]]) + off)
                ns.append(np.tile(n3, (4, 1)))
                cs.append(np.tile(np.array([0.60, 0.58, 0.54]), (4, 1)))
                off += 4
                # Parapet coping.
                back = -n2 * 0.6
                cap = np.array([[a[0], a[1], top], [b[0], b[1], top], [b[0] + back[0], b[1] + back[1], top], [a[0] + back[0], a[1] + back[1], top]])
                if np.cross(cap[1] - cap[0], cap[2] - cap[0])[2] < 0:
                    cap = cap[[1, 0, 3, 2]]
                vs.append(cap)
                ts.append(np.array([[0, 1, 2], [0, 2, 3]]) + off)
                ns.append(np.tile([0, 0, 1.0], (4, 1)))
                cs.append(np.tile(np.array([0.70, 0.68, 0.64]), (4, 1)))
                off += 4
    if not vs:
        return None
    v = np.vstack(vs)
    return Piece(v, np.vstack(ts), np.vstack(ns), np.vstack(cs), v[:, :2] / 2)


def emit(col: Collector, scene: Scene, ground: Ground) -> None:
    clip = scene.clip
    car = surface_mesh(scene, ground, ground.carriageway.intersection(clip), PARAMS.pavement_lift, ASPHALT, 7.0, 3.0)
    if car is not None:
        col.add_by_centroid((0, 1), "road", car)
    pav = surface_mesh(scene, ground, ground.hardscape.intersection(clip), PARAMS.pavement_lift + PARAMS.kerb_height, PAVING, 7.0, 1.8)
    if pav is not None:
        col.add_by_centroid((0, 1), "pavement", pav)
    kerb = kerb_mesh(scene, ground)
    if kerb is not None:
        col.add_by_centroid((0,), "kerb", kerb)
    walls = river_walls(scene, ground)
    if walls is not None:
        col.add_by_centroid((0, 1), "embankment", walls)


# ---------------------------------------------------------------- terrain columns

def terrain_columns(scene: Scene, ground: Ground, extent_studs: tuple[float, float, float, float]) -> dict:
    """Voxel-column grid (4-stud cells) with height (metres ODN), material code and water level."""
    mps = PARAMS.metres_per_stud
    x0, z0, x1, z1 = extent_studs
    e0, n0 = scene.region.origin
    nx = int(round((x1 - x0) / 4))
    nz = int(round((z1 - z0) / 4))
    xs = x0 + (np.arange(nx) + 0.5) * 4
    zs = z0 + (np.arange(nz) + 0.5) * 4
    gx, gz = np.meshgrid(xs, zs, indexing="ij")  # [ix, iz]
    E = e0 + gx * mps
    N = n0 - gz * mps
    h = scene.dtm.sample(E.ravel(), N.ravel()).reshape(nx, nz)
    mat = np.full((nx, nz), MAT["pavement"], dtype=np.uint8)
    water = np.full((nx, nz), np.nan)

    def mask(geom):
        if geom is None or geom.is_empty:
            return np.zeros((nx, nz), dtype=bool)
        return shapely.contains_xy(geom, E.ravel(), N.ravel()).reshape(nx, nz)

    mat[mask(ground.green)] = MAT["grass"]
    mat[mask(ground.paths)] = MAT["asphalt"]
    car = mask(ground.carriageway)
    mat[car] = MAT["asphalt"]
    # Terrain under the street meshes sits a little below them (so it never
    # pokes through); the client snaps feet to the visual surface.
    hard = mask(ground.hardscape)
    h = np.where(hard, h + PARAMS.kerb_height - 0.12, h)
    h = np.where(car, h - 0.12, h)
    for w in ground.water:
        m = mask(w.poly)
        water[m] = w.level
        h[m] = w.level - w.depth
        mat[m] = MAT["mud"]
    return {"x0": x0, "z0": z0, "nx": nx, "nz": nz, "height": h, "material": mat, "water": water}
