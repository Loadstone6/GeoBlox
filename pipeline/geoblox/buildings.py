"""Building reconstruction: OSM footprints + parts fused with LiDAR heights.

Per building:
  * faces   = planar arrangement of the outline and its building:part outlines
              (parts act as breaklines where roof heights change)
  * roofs   = per-face LiDAR roof TIN (see roofs.py)
  * walls   = vertical faces from the ground (or a lower neighbouring roof)
              up to the roof edge, with per-vertex eave heights
  * facade  = window grids derived from floor height and bay width of an
              inferred architectural style (stock brick, Portland stone, gothic,
              curtain wall, ...), ground-floor shopfronts where OSM shows shops
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from . import roofs
from .config import PARAMS
from .s3db import build_part, part_heights
from .mesh import Collector, Piece, crease_split, face_normals, orient_up, quad
from .osm import polygons
from .scene import Scene, raster_cells_in


def stable_hash(*parts) -> int:
    h = hashlib.blake2b(repr(parts).encode(), digest_size=8).digest()
    return int.from_bytes(h, "little")


def rgb(r: int, g: int, b: int) -> np.ndarray:
    return np.array([r, g, b], dtype=np.float64) / 255.0


# ---------------------------------------------------------------- styles

@dataclass(frozen=True)
class Style:
    name: str
    wall: tuple
    material: str  # runtime material group suffix
    bay: float
    win_w: float
    win_h: float
    sill: float
    floor: float
    frame: tuple
    glass: tuple
    pointed: bool = False
    curtain: bool = False
    bars: bool = True


STYLES = {
    "stock": Style("stock", (172, 150, 112), "brick", 3.3, 1.05, 1.75, 0.8, 3.15, (236, 234, 226), (26, 30, 36)),
    "red": Style("red", (150, 74, 54), "brick", 3.3, 1.05, 1.75, 0.8, 3.15, (236, 234, 226), (26, 30, 36)),
    "portland": Style("portland", (214, 205, 182), "stone", 3.9, 1.35, 2.25, 0.85, 3.9, (210, 204, 188), (30, 34, 40), bars=False),
    "gothic": Style("gothic", (196, 176, 136), "stone", 2.7, 0.95, 3.4, 1.3, 5.2, (176, 160, 126), (34, 36, 40), pointed=True, bars=False),
    "glass": Style("glass", (64, 86, 98), "glass", 1.6, 1.5, 3.4, 0.2, 3.8, (52, 58, 62), (40, 60, 72), curtain=True, bars=False),
    "concrete": Style("concrete", (178, 176, 168), "concrete", 2.9, 2.2, 1.6, 1.0, 3.6, (60, 62, 64), (34, 40, 46), bars=False),
    "stucco": Style("stucco", (232, 228, 214), "plaster", 3.3, 1.2, 2.1, 0.75, 3.6, (240, 240, 236), (26, 30, 36)),
}

CIVIC = {"government", "public", "civic", "palace", "museum", "townhall", "embassy", "courthouse", "train_station", "university", "college"}
SACRED = {"church", "cathedral", "chapel", "abbey", "religious"}
HOMES = {"residential", "apartments", "house", "terrace", "detached", "semidetached_house", "dormitory"}


def infer_style(tags: dict, height: float, key: int) -> str:
    mat = tags.get("building:material", "")
    colour = tags.get("building:colour", "").lower()
    btype = tags.get("building", tags.get("building:part", "yes"))
    name = tags.get("name", "")
    r = (key % 1000) / 1000.0
    if mat in ("glass",):
        return "glass"
    if "Palace of Westminster" in name or "Westminster Abbey" in name or btype in SACRED or tags.get("amenity") == "place_of_worship":
        return "gothic"
    if mat in ("stone", "sandstone", "limestone"):
        return "portland"
    if mat in ("brick",):
        return "red" if ("red" in colour or r < 0.3) else "stock"
    if mat in ("concrete",):
        return "concrete"
    if mat in ("plaster", "stucco") or colour in ("white", "#ffffff", "cream"):
        return "stucco"
    historic = any(k.startswith("historic") or k.startswith("heritage") or k == "listed_status" for k in tags)
    if height > 32 and not historic:
        return "glass" if r < 0.55 else "concrete"
    if btype in CIVIC or historic:
        return "portland"
    if btype in HOMES:
        return "stock" if r < 0.6 else ("red" if r < 0.82 else "stucco")
    if btype in ("office", "commercial", "hotel", "retail"):
        return "portland" if r < 0.45 else ("concrete" if r < 0.7 else ("stock" if r < 0.88 else "glass"))
    return "stock" if r < 0.5 else ("portland" if r < 0.8 else "red")


ROOF_PITCHED = {
    "slate": rgb(74, 80, 90),
    "tile": rgb(140, 76, 58),
    "lead": rgb(126, 132, 136),
    "copper": rgb(96, 150, 128),
}
ROOF_FLAT = [rgb(118, 116, 112), rgb(132, 130, 124), rgb(96, 96, 96), rgb(150, 146, 136)]


# ---------------------------------------------------------------- data

@dataclass
class Face:
    poly: Polygon
    tags: dict
    rings: list[np.ndarray] = field(default_factory=list)  # open rings, exterior CCW, holes CW
    ring_z: list[np.ndarray] = field(default_factory=list)
    ground: list[np.ndarray] = field(default_factory=list)  # ground z under each ring vertex
    tin: roofs.Tin | None = None
    top: float = 0.0
    median: float = 0.0


@dataclass
class Building:
    key: int
    tags: dict
    outline: Polygon
    parts: list[tuple[Polygon, dict]]
    retail: bool = False
    faces: list[Face] = field(default_factory=list)
    style: str = "stock"
    ground: float = 0.0
    height: float = 0.0
    lidar_ok: bool = True
    mode: str = "lidar"  # "lidar" (measured roofs) or "s3db" (mapper-modelled parts)
    courtyards: int = 0


# ---------------------------------------------------------------- collection

SKIP_BUILDING = {"no", "construction", "proposed", "demolished", "underground", "ruins"}
SPECIAL_STRUCTURES = ("London Eye",)


def collect(scene: Scene) -> list[Building]:
    data = scene.osm
    outlines, parts = [], []
    for f in data.areas:
        t = f.tags
        if "building" in t and t["building"] not in SKIP_BUILDING:
            if t.get("location") == "underground" or t.get("layer", "0").startswith("-"):
                continue
            if any(s in t.get("name", "") for s in SPECIAL_STRUCTURES):
                continue
            for p in polygons(f.geom):
                outlines.append((p, t, f.id))
        elif "building:part" in t and t["building:part"] not in SKIP_BUILDING:
            for p in polygons(f.geom):
                parts.append((p, t))

    inner = scene.clip.buffer(-2.0)
    outlines = [o for o in outlines if inner.contains(o[0].representative_point())]
    # The London Eye is rebuilt parametrically from LiDAR (landmarks.py); drop
    # the mapped rim segments (floating building:parts) inside its footprint.
    eye = next((f.geom for f in data.areas if f.tags.get("attraction") == "big_wheel"), None)
    if eye is not None:
        zone = eye.buffer(6.0)
        parts = [(p, t) for p, t in parts if not p.intersects(zone)]
    part_tree = STRtree([p for p, _ in parts]) if parts else None

    shops = [p.geom for p in data.points if ("shop" in p.tags or p.tags.get("amenity") in ("restaurant", "cafe", "pub", "bar", "fast_food", "bank"))]
    shop_tree = STRtree(shops) if shops else None

    buildings = []
    used_parts = set()
    for poly, tags, fid in outlines:
        poly = orient(poly.simplify(0.03, preserve_topology=True), 1.0)
        if not poly.is_valid or poly.area < 4.0:
            continue
        mine = []
        if part_tree is not None:
            for i in part_tree.query(poly):
                pp, pt = parts[i]
                inter = pp.intersection(poly).area
                if inter > 0.5 * pp.area:
                    mine.append((pp, pt))
                    used_parts.add(int(i))
        retail = tags.get("building") in ("retail", "commercial", "shop") or "shop" in tags
        if shop_tree is not None and not retail:
            retail = any(poly.contains(shops[i]) for i in shop_tree.query(poly))
        b = Building(stable_hash(fid), tags, poly, mine, retail)
        # Mapper-modelled landmarks: every part carries an explicit height.
        if mine and sum(1 for _, pt in mine if part_heights(pt) is not None) >= 0.85 * len(mine):
            b.mode = "s3db"
        buildings.append(b)

    # Orphan parts (no matching outline): mapped 3D detail is extruded from its
    # own tags; untagged ones fall back to LiDAR.
    for i, (pp, pt) in enumerate(parts):
        if i in used_parts or not inner.contains(pp.representative_point()):
            continue
        if part_heights(pt) is not None:
            o = Building(stable_hash("part", i), pt, orient(pp, 1.0), [(pp, pt)])
            o.mode = "s3db"
            buildings.append(o)
        elif pp.area >= 4.0:
            buildings.append(Building(stable_hash("part", i), pt, orient(pp, 1.0), []))
    return buildings


# ---------------------------------------------------------------- faces

def make_faces(b: Building) -> list[Face]:
    if not b.parts:
        return [Face(b.outline, b.tags)]
    lines = [b.outline.boundary] + [p.intersection(b.outline).boundary for p, _ in b.parts]
    noded = unary_union([g for g in lines if not g.is_empty])
    faces = [f for f in polygonize(noded) if b.outline.contains(f.representative_point()) and f.area > 0.05]
    if not faces:
        return [Face(b.outline, b.tags)]
    # Merge slivers into the neighbour sharing the longest edge.
    big = [f for f in faces if f.area >= 3.0]
    small = [f for f in faces if f.area < 3.0]
    for s in small:
        best, best_len = None, 0.0
        for i, g in enumerate(big):
            shared = s.boundary.intersection(g.boundary).length
            if shared > best_len:
                best, best_len = i, shared
        if best is not None:
            big[best] = big[best].union(s)
    out = []
    for f in big:
        f = f.buffer(0)
        for p in polygons(f):
            rp = p.representative_point()
            covering = [(pp, pt) for pp, pt in b.parts if pp.buffer(0.05).contains(rp)]
            if covering:
                def ph(item):
                    try:
                        return float(str(item[1].get("height", "0")).split()[0])
                    except ValueError:
                        return 0.0
                tags = {**b.tags, **max(covering, key=ph)[1]}
            else:
                tags = b.tags
            out.append(Face(orient(p, 1.0), tags))
    return out


def _rings(poly: Polygon, densify: float = 4.0) -> list[np.ndarray]:
    rings = []
    for ring in [poly.exterior, *poly.interiors]:
        pts = np.array(ring.coords)[:-1]
        dense = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            dense.append(a)
            seg = np.linalg.norm(b - a)
            k = int(seg // densify)
            for j in range(1, k + 1):
                f = j / (k + 1)
                dense.append(a + (b - a) * f)
        rings.append(np.array(dense))
    return rings


def _inward_normals(ring: np.ndarray) -> np.ndarray:
    """Inward unit normals for a ring whose interior is on the left (CCW outer / CW hole)."""
    prev = np.roll(ring, 1, axis=0)
    nxt = np.roll(ring, -1, axis=0)
    e1 = ring - prev
    e2 = nxt - ring
    n1 = np.column_stack([-e1[:, 1], e1[:, 0]])
    n2 = np.column_stack([-e2[:, 1], e2[:, 0]])
    n1 /= np.maximum(np.linalg.norm(n1, axis=1, keepdims=True), 1e-9)
    n2 /= np.maximum(np.linalg.norm(n2, axis=1, keepdims=True), 1e-9)
    n = n1 + n2
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    return n


def courtyard_holes(scene: Scene, poly: Polygon, ground: float) -> Polygon:
    """Cut open courtyards / light wells (LiDAR at ground level inside the
    footprint) out of the face so they get inward-facing walls."""
    from skimage import measure

    rr, cc = raster_cells_in(scene.dsm, poly, 0.0)
    if len(rr) < 60:
        return poly
    nd = scene.dsm.data[rr, cc] - ground
    low = nd < 1.6
    if low.sum() < 25:
        return poly
    r0, c0 = rr.min(), cc.min()
    img = np.zeros((rr.max() - r0 + 3, cc.max() - c0 + 3), dtype=np.uint8)
    img[rr[low] - r0 + 1, cc[low] - c0 + 1] = 1
    from scipy import ndimage

    img = ndimage.binary_opening(img, iterations=1).astype(np.uint8)
    lab, n = ndimage.label(img)
    holes = []
    for k in range(1, n + 1):
        comp = lab == k
        if comp.sum() < 25:
            continue
        for contour in measure.find_contours(comp.astype(float), 0.5):
            if len(contour) < 4:
                continue
            ys = scene.dsm.n_max - (contour[:, 0] + r0 - 1 + 0.5)
            xs = scene.dsm.e_min + (contour[:, 1] + c0 - 1 + 0.5)
            hp = Polygon(np.column_stack([xs, ys])).buffer(0)
            if hp.area >= 20:
                holes.append(hp.simplify(0.6))
    if not holes:
        return poly
    cut = poly.difference(unary_union(holes)).buffer(0)
    parts = polygons(cut)
    if not parts:
        return poly
    return orient(max(parts, key=lambda p: p.area), 1.0)


def reconstruct(scene: Scene, b: Building, eps: float, max_vertices: int, lod: int) -> None:
    dsm, dtm = scene.dsm, scene.dtm
    # Ground: robust low percentile just outside the outline.
    ring = np.array(b.outline.exterior.coords)[:-1]
    out_n = -_inward_normals(ring)
    g_pts = ring + out_n * 1.0
    gz = dtm.sample(g_pts[:, 0], g_pts[:, 1])
    b.ground = float(np.percentile(gz, 25))
    if b.mode == "s3db" and lod == 0:
        tops = [part_heights(t) for _, t in b.parts]
        b.height = max((h[1] for h in tops if h), default=10.0)
        b.faces = []
        return
    faces = make_faces(b) if lod == 0 else [Face(b.outline, b.tags)]
    if lod == 0:
        for face in faces:
            cut = courtyard_holes(scene, face.poly, b.ground)
            if len(cut.interiors) > len(face.poly.interiors):
                b.courtyards += len(cut.interiors) - len(face.poly.interiors)
            face.poly = cut

    for face in faces:
        poly = face.poly
        shrink = 0.45 if poly.area > 12 and poly.buffer(-0.45).area > 2 else 0.0
        rr, cc = raster_cells_in(dsm, poly, shrink)
        if len(rr) < 3:
            rr, cc = raster_cells_in(dsm, poly.buffer(0.6), 0.0)
        xs, ys = dsm.cell_center(rr, cc)
        z = dsm.data[rr, cc].astype(np.float64)
        samples = roofs.RoofSamples(np.column_stack([xs, ys]), z, np.column_stack([rr, cc]))
        seed = stable_hash(b.key, len(face.rings))
        if len(z) >= PARAMS.ransac_min_support and lod == 0:
            labels, planes = roofs.ransac_planes(samples.xy, z, samples.rc, PARAMS.ransac_threshold, PARAMS.ransac_min_support, seed=seed % 2**31)
        else:
            labels, planes = np.full(len(z), -1, dtype=np.int32), []
        if planes:
            labels = roofs.assign_residuals(samples, labels, planes)
        target = roofs.snapped_heights(samples, dsm.data, labels, planes) if len(z) else z
        face.median = float(np.median(target)) if len(target) else b.ground + 6.0
        # Tight tolerance on detected planes, loose on unexplained clutter
        # (sub-resolution pinnacles, plant, chimneys) to avoid fitting noise.
        eps_arr = np.where(labels >= 0, eps, max(eps * 2.6, 0.8)) if len(z) else eps

        rings = _rings(poly, 4.0 if lod == 0 else 12.0)
        tree = cKDTree(samples.xy) if len(z) else None
        ring_z, ring_g = [], []
        for rg in rings:
            nin = _inward_normals(rg)
            probe = rg + nin * 0.9
            if tree is not None:
                # Eave height: a low percentile of nearby roof samples, so
                # pinnacles and chimneys do not drag the wall top up.
                zz = np.full(len(rg), face.median)
                for i, near in enumerate(tree.query_ball_point(probe, r=2.2)):
                    if near:
                        zz[i] = np.percentile(target[near], 30)
            else:
                zz = np.full(len(rg), face.median)
            ring_z.append(zz)
            gp = rg - nin * 0.8
            ring_g.append(dtm.sample(gp[:, 0], gp[:, 1]))
        holes = [tuple(np.array(Polygon(h).representative_point().coords[0])) for h in poly.interiors]
        breaks = roofs.plane_breaklines(samples, labels, planes) if planes else None
        tin = roofs.greedy_tin(rings, ring_z, samples, target, (dsm.e_min, dsm.n_max), eps_arr, max_vertices, holes, breaks)
        tin.planes = len(planes)
        face.rings, face.ring_z, face.ground, face.tin = rings, ring_z, ring_g, tin
        face.top = float(tin.vertices[:, 2].max()) if len(tin.vertices) else face.median

    # Roofs never below the ground; flag footprints with no LiDAR evidence.
    for face in faces:
        if face.tin is not None and len(face.tin.vertices):
            face.tin.vertices[:, 2] = np.maximum(face.tin.vertices[:, 2], b.ground + 2.0)
        face.ring_z = [np.maximum(z, b.ground + 2.0) for z in face.ring_z]
    b.height = max(f.top for f in faces) - b.ground
    b.lidar_ok = b.height > 2.2
    b.faces = faces


def unify_seams(buildings: list[Building], tol: float = 0.45) -> None:
    """Shared boundary vertices whose roof heights agree get one common height."""
    table: dict[tuple[int, int], list[tuple[Face, int, int]]] = {}
    for b in buildings:
        for f in b.faces:
            for ri, rg in enumerate(f.rings):
                for vi, (x, y) in enumerate(rg):
                    table.setdefault((round(x * 20), round(y * 20)), []).append((f, ri, vi))
    for refs in table.values():
        if len(refs) < 2:
            continue
        zs = np.array([f.ring_z[ri][vi] for f, ri, vi in refs])
        if zs.max() - zs.min() < tol:
            m = float(zs.mean())
            for f, ri, vi in refs:
                f.ring_z[ri][vi] = m
                if f.tin is not None and f.tin.ring_map is not None:
                    base = sum(len(r) for r in f.rings[:ri])
                    if base + vi < len(f.tin.ring_map):
                        f.tin.vertices[f.tin.ring_map[base + vi], 2] = m


# ---------------------------------------------------------------- geometry emission

class FaceIndex:
    """Global lookup: which face (if any) is at a point, and its roof height there."""

    def __init__(self, buildings: list[Building]):
        self.faces: list[Face] = []
        for b in buildings:
            self.faces.extend(b.faces)
        self.tree = STRtree([f.poly for f in self.faces]) if self.faces else None

    def roof_at(self, x: float, y: float) -> float | None:
        """Highest reconstructed roof height at (x, y), from the face TINs."""
        if self.tree is None:
            return None
        p = Point(x, y)
        best = None
        for i in self.tree.query(p):
            f = self.faces[i]
            if f.poly.contains(p):
                h = tin_height(f, x, y)
                best = h if best is None else max(best, h)
        return best


def tin_height(face: Face, x: float, y: float) -> float:
    tin = face.tin
    if tin is None or len(tin.triangles) == 0:
        return face.median
    V, T = tin.vertices, tin.triangles
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    ok = np.abs(den) > 1e-12
    den = np.where(ok, den, 1.0)
    l0 = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / den
    l1 = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / den
    l2 = 1 - l0 - l1
    inside = ok & (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    if not inside.any():
        return face.median
    k = int(np.argmax(inside))
    return float(l0[k] * a[k, 2] + l1[k] * b[k, 2] + l2[k] * c[k, 2])


def _ao_color(base: np.ndarray, ao: np.ndarray) -> np.ndarray:
    return base[None, :] * (0.0 * ao + 1.0)[:, None]


def emit_roof(col: Collector, chunk, lod: int, b: Building, face: Face, roof_rgb_pitched, roof_rgb_flat, group_pitched: str, group_flat: str, ao_fn, wall_group: str = "wall_stone", wall_rgb=None) -> None:
    if wall_rgb is None:
        wall_rgb = roof_rgb_flat
    tin = face.tin
    if tin is None or len(tin.triangles) == 0:
        return
    v = tin.vertices.copy()
    t = orient_up(v, tin.triangles)
    nv, nt, nn, src = crease_split(v, t, 28.0)
    tri_n = face_normals(nv, nt)
    ao = ao_fn(nv, nn)
    # Near-vertical (> ~78°) = wall; steep but not vertical = mansard/pitched slate.
    kind = np.where(tri_n[:, 2] < 0.2, 2, np.where(tri_n[:, 2] < np.cos(np.radians(12)), 1, 0))
    for flag, group, colr in ((1, group_pitched, roof_rgb_pitched), (0, group_flat, roof_rgb_flat), (2, wall_group, wall_rgb)):
        m = kind == flag
        if not m.any():
            continue
        sub_t = nt[m]
        used = np.unique(sub_t)
        remap = -np.ones(len(nv), dtype=np.int64)
        remap[used] = np.arange(len(used))
        vv = nv[used]
        uv = vv[:, :2] / 2.0
        col.add(chunk, lod, group, Piece(vv, remap[sub_t], nn[used], _ao_color(colr, ao[used]), uv))


def emit_walls(col: Collector, chunk, lod: int, b: Building, face: Face, index: FaceIndex, style: Style, wall_rgb, ao_fn, rng) -> None:
    """Walls for every boundary edge of a face, from the neighbour height (ground or
    lower roof) up to this face's roof edge, plus windows on exposed parts."""
    wall_pieces: list[Piece] = []
    win_frame: list[Piece] = []
    win_glass: list[Piece] = []
    win_lit: list[Piece] = []
    shop_glass: list[Piece] = []
    trims: list[Piece] = []
    for ring, rz, rg in zip(face.rings, face.ring_z, face.ground):
        n = len(ring)
        dist = 0.0
        for i in range(n):
            a, c = ring[i], ring[(i + 1) % n]
            seg = c - a
            length = float(np.linalg.norm(seg))
            if length < 0.05:
                continue
            d = seg / length
            outward = np.array([d[1], -d[0]])  # interior is on the left
            mid = (a + c) / 2 + outward * 0.35
            neighbour = index.roof_at(mid[0], mid[1])
            za_top, zc_top = rz[i], rz[(i + 1) % n]
            ground_a, ground_c = rg[i], rg[(i + 1) % n]
            if neighbour is not None and not face.poly.buffer(0.01).contains(Point(mid)):
                bot_a = bot_c = neighbour
                exposed_from = neighbour
            else:
                bot_a, bot_c = ground_a - 0.4, ground_c - 0.4
                exposed_from = min(ground_a, ground_c)
            if min(za_top - bot_a, zc_top - bot_c) < 0.25 and max(za_top - bot_a, zc_top - bot_c) < 0.6:
                dist += length
                continue
            bot_a, bot_c = min(bot_a, za_top), min(bot_c, zc_top)
            nrm3 = np.array([outward[0], outward[1], 0.0])
            p0 = np.array([a[0], a[1], bot_a])
            p1 = np.array([c[0], c[1], bot_c])
            p2 = np.array([c[0], c[1], zc_top])
            p3 = np.array([a[0], a[1], za_top])
            ground_ref = b.ground
            uv = np.array([[dist, (bot_a - ground_ref)], [dist + length, (bot_c - ground_ref)], [dist + length, (zc_top - ground_ref)], [dist, (za_top - ground_ref)]]) / 2.0
            verts = np.array([p0, p1, p2, p3])
            ao = ao_fn(verts, np.tile(nrm3, (4, 1)))
            wall_pieces.append(quad(p0, p1, p2, p3, nrm3, _ao_color(wall_rgb, ao), uv))
            # Cornice band at the top of tall walls (classical / stock-brick parapet line).
            if style.name in ("portland", "stucco", "stock", "red") and min(za_top, zc_top) - exposed_from > 6:
                off = nrm3 * 0.18
                top = min(za_top, zc_top)
                q0 = np.array([a[0], a[1], top - 0.9]) + off
                q1 = np.array([c[0], c[1], top - 0.9]) + off
                q2 = np.array([c[0], c[1], top - 0.55]) + off
                q3 = np.array([a[0], a[1], top - 0.55]) + off
                trim_rgb = np.clip(wall_rgb * 1.12, 0, 1)
                trims.append(quad(q0, q1, q2, q3, nrm3, trim_rgb * 0.95, np.array([[dist, 0], [dist + length, 0], [dist + length, 0.2], [dist, 0.2]]) / 2))
                # Underside so the band reads as a projection, not a painted line.
                trims.append(quad(np.array([a[0], a[1], top - 0.9]), np.array([c[0], c[1], top - 0.9]), q1, q0, np.array([0, 0, -1.0]), trim_rgb * 0.55, np.zeros((4, 2))))
            if lod == 0:
                _windows(style, b, a, c, d, nrm3, exposed_from, min(za_top, zc_top), length, ao_fn, win_frame, win_glass, win_lit, shop_glass, rng)
            dist += length

    from .mesh import merge

    if wall_pieces:
        col.add(chunk, lod, f"wall_{style.material}", merge(wall_pieces))
    if trims:
        col.add(chunk, lod, f"wall_{style.material}", merge(trims))
    if win_frame:
        col.add(chunk, lod, "window_frame", merge(win_frame))
    if win_glass:
        col.add(chunk, lod, "window_glass" if not style.curtain else "curtain_glass", merge(win_glass))
    if win_lit:
        col.add(chunk, lod, "window_lit", merge(win_lit))
    if shop_glass:
        col.add(chunk, lod, "shopfront", merge(shop_glass))


def _rect(origin, d, z0, u0, u1, h, nrm3, offset, color):
    """Axis-aligned rectangle on a wall: from u0..u1 along d, z0..z0+h, pushed out by offset."""
    o = np.array([origin[0], origin[1], 0.0]) + nrm3 * offset
    d3 = np.array([d[0], d[1], 0.0])
    p0 = o + d3 * u0 + np.array([0, 0, z0])
    p1 = o + d3 * u1 + np.array([0, 0, z0])
    p2 = p1 + np.array([0, 0, h])
    p3 = p0 + np.array([0, 0, h])
    uv = np.array([[u0, z0], [u1, z0], [u1, z0 + h], [u0, z0 + h]]) / 2.0
    return quad(p0, p1, p2, p3, nrm3, color, uv)


def _windows(style: Style, b: Building, a, c, d, nrm3, z_from, z_to, length, ao_fn, frames, glass, lit, shop, rng):
    floor = style.floor
    usable_from = max(z_from, b.ground)
    if z_to - usable_from < 2.4 or length < 1.4:
        return
    frame_rgb = rgb(*style.frame)
    glass_rgb = rgb(*style.glass)
    if style.curtain:
        # Curtain wall: the whole face is glazing with a mullion/transom grid.
        h = z_to - usable_from
        glass.append(_rect(a, d, usable_from, 0.0, length, h, nrm3, 0.025, glass_rgb))
        nb = max(1, int(round(length / style.bay)))
        for k in range(nb + 1):
            u = length * k / nb
            frames.append(_rect(a, d, usable_from, max(0, u - 0.05), min(length, u + 0.05), h, nrm3, 0.05, frame_rgb))
        z = usable_from + floor
        while z < z_to - 0.5:
            frames.append(_rect(a, d, z - 0.18, 0.0, length, 0.36, nrm3, 0.05, frame_rgb))
            z += floor
        return
    nb = int(length // style.bay)
    if nb < 1:
        return
    margin = (length - nb * style.bay) / 2
    first_floor = 0
    ground_z = b.ground
    floor0 = int(max(0, np.floor((usable_from - ground_z) / floor - 0.01)))
    nfloors = int(np.floor((z_to - ground_z) / floor))
    for f in range(floor0, nfloors):
        fz = ground_z + f * floor
        if fz + style.sill + style.win_h > z_to - 0.35:
            break
        if f == 0 and b.retail:
            # Shopfront: wide glazing with a dark fascia above.
            for k in range(nb):
                u0 = margin + k * style.bay + 0.25
                u1 = u0 + style.bay - 0.5
                shop.append(_rect(a, d, fz + 0.35, u0, u1, 2.7, nrm3, 0.04, rgb(30, 38, 44)))
            frames.append(_rect(a, d, fz + 3.15, margin, length - margin, 0.65, nrm3, 0.06, rgb(36, 40, 44)))
            continue
        if f == 0 and fz + style.sill < usable_from:
            continue
        for k in range(nb):
            uc = margin + (k + 0.5) * style.bay
            u0, u1 = uc - style.win_w / 2, uc + style.win_w / 2
            z0 = fz + style.sill
            if z0 < usable_from + 0.2:
                continue
            fw = 0.12
            frames.append(_rect(a, d, z0 - fw, u0 - fw, u1 + fw, style.win_h + 2 * fw, nrm3, 0.03, frame_rgb))
            pane = _rect(a, d, z0, u0, u1, style.win_h, nrm3, 0.05, glass_rgb)
            glass.append(pane)
            if rng.random() < 0.32:
                lit.append(_rect(a, d, z0, u0, u1, style.win_h, nrm3, 0.06, rgb(255, 214, 150)))
            if style.bars:
                # Georgian sash: meeting rail + central glazing bar.
                frames.append(_rect(a, d, z0 + style.win_h * 0.5 - 0.04, u0, u1, 0.08, nrm3, 0.065, frame_rgb))
                frames.append(_rect(a, d, z0, uc - 0.03, uc + 0.03, style.win_h, nrm3, 0.065, frame_rgb))
            if style.pointed:
                o = np.array([a[0], a[1], 0.0]) + nrm3 * 0.05
                d3 = np.array([d[0], d[1], 0.0])
                top = z0 + style.win_h
                p0 = o + d3 * u0 + np.array([0, 0, top])
                p1 = o + d3 * u1 + np.array([0, 0, top])
                p2 = o + d3 * uc + np.array([0, 0, top + style.win_w * 0.8])
                glass.append(Piece(np.array([p0, p1, p2]), np.array([[0, 1, 2]]), np.tile(nrm3, (3, 1)), np.tile(glass_rgb, (3, 1)), np.zeros((3, 2))))


# ---------------------------------------------------------------- per-building emission

def wall_tint(style: Style, key: int) -> np.ndarray:
    base = rgb(*style.wall)
    j = ((key >> 8) % 1000) / 1000.0 - 0.5
    k = ((key >> 20) % 1000) / 1000.0 - 0.5
    tint = base * (1.0 + 0.14 * j) + np.array([0.025, 0.0, -0.025]) * k
    return np.clip(tint, 0, 1)


def roof_palette(b: Building, style: Style) -> tuple[np.ndarray, np.ndarray]:
    from .geomutil import parse_colour

    tagged = parse_colour(b.tags.get("roof:colour"))
    mat = b.tags.get("roof:material", "")
    r = ((b.key >> 32) % 1000) / 1000.0
    if mat in ROOF_PITCHED:
        pitched = ROOF_PITCHED[mat]
    elif mat in ("roof_tiles", "tiles", "clay"):
        pitched = ROOF_PITCHED["tile"]
    elif style.name in ("stock", "red") and r < 0.18:
        pitched = ROOF_PITCHED["tile"]
    elif r > 0.9:
        pitched = ROOF_PITCHED["lead"]
    else:
        pitched = ROOF_PITCHED["slate"]
    if tagged is not None:
        pitched = tagged
    flat = ROOF_FLAT[(b.key >> 40) % len(ROOF_FLAT)]
    return pitched, flat


def emit_building(col: Collector, chunk, lod: int, b: Building, index: FaceIndex, ao_fn, rng) -> None:
    style = STYLES[b.style]
    wall_rgb = wall_tint(style, b.key)
    pitched_rgb, flat_rgb = roof_palette(b, style)
    if b.mode == "s3db" and lod == 0:
        for poly, tags in b.parts:
            for group, piece, _ in build_part(poly, tags, b.ground, wall_rgb, pitched_rgb, ao_fn):
                col.add(chunk, lod, group, piece)
        s3db_windows(col, chunk, b, style, rng)
        return
    wall_group = f"wall_{style.material}"
    for face in b.faces:
        emit_roof(col, chunk, lod, b, face, pitched_rgb, flat_rgb, "roof_pitched", "roof_flat", ao_fn, wall_group, wall_rgb)
        emit_walls(col, chunk, lod, b, face, index, style, wall_rgb, ao_fn, rng)


def s3db_windows(col: Collector, chunk, b: Building, style: Style, rng) -> None:
    """Facade windows on mapper-modelled parts: per wall edge, between the part's
    base and eave, above whatever neighbouring part covers that wall."""
    from shapely.geometry import Point as _P

    from .mesh import merge
    from .s3db import roof_function

    info = []
    for poly, tags in b.parts:
        hh = part_heights(tags)
        if hh is None:
            continue
        info.append((poly, tags, hh))
    if not info:
        return
    tree = STRtree([p for p, _, _ in info])
    frames, glass, lit, shop = [], [], [], []
    fake = Building(b.key, b.tags, b.outline, [], False)
    fake.ground = b.ground
    for idx, (poly, tags, (h0, h1)) in enumerate(info):
        if poly.area < 25 or h1 - h0 < 4.5:
            continue
        try:
            _, _, rh, _, _, _ = roof_function(poly, tags, h0, h1)
        except Exception:
            rh = 0.0
        z_bot, z_top = b.ground + h0, b.ground + h1 - rh
        if tags.get("building:colour") and is_gilt_colour(tags.get("building:colour")):
            continue
        ring = np.asarray(orient(poly, 1.0).exterior.coords)[:-1]
        for i in range(len(ring)):
            a, c = ring[i], ring[(i + 1) % len(ring)]
            seg = c - a
            L = float(np.linalg.norm(seg))
            if L < 2.4:
                continue
            d = seg / L
            nrm3 = np.array([d[1], -d[0], 0.0])
            probe = _P(*((a + c) / 2 + nrm3[:2] * 0.6))
            cover = z_bot
            for j in tree.query(probe):
                if j != idx and info[j][0].contains(probe):
                    cover = max(cover, b.ground + info[j][2][1])
            if cover >= z_top - 2.0:
                continue
            _windows(style, fake, a, c, d, nrm3, cover, z_top, L, None, frames, glass, lit, shop, rng)
    if frames:
        col.add(chunk, 0, "window_frame", merge(frames))
    if glass:
        col.add(chunk, 0, "window_glass", merge(glass))
    if lit:
        col.add(chunk, 0, "window_lit", merge(lit))


def is_gilt_colour(value: str) -> bool:
    from .geomutil import parse_colour
    from .s3db import is_gilt

    c = parse_colour(value)
    return c is not None and is_gilt(c)
