"""Buildings: OSM footprints and parts, architectural styles and palettes.

collect() gathers footprints, their building:parts and shop evidence. A
building whose parts are nearly all height-tagged is a mapped landmark
(Simple 3D Buildings); the rest are reconstructed from LiDAR (roofer.py,
buildparts.py). infer_style() picks an architectural style for the
facade modules: London stock brick, red brick, Portland stone, gothic,
curtain wall, concrete or stucco.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .osm import polygons
from .s3db import part_heights
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
class Building:
    key: int
    tags: dict
    outline: Polygon
    parts: list[tuple[Polygon, dict]]
    retail: bool = False
    style: str = "stock"
    ground: float = 0.0
    height: float = 0.0
    lidar_ok: bool = True
    mode: str = "lidar"  # "lidar" (measured roofs) or "s3db" (mapper-modelled parts)
    courtyards: int = 0
    footprint: Polygon | None = None  # cleaned outline with LiDAR courtyards cut out
    roof: object = None  # roofer.Roof (LoD2 faces)


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
