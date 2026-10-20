"""Bridges and embankments as parts.

Deck surface: the first-return DSM over the mapped bridge polygon is binned
along the bridge's long axis. A low percentile per bin (vehicles and people
are outliers above the deck) gives the deck profile z(s). It is smoothed with
a moving median and pulled onto the terrain road at both abutments, so cars
roll on and off without a lip. The profile is simplified into straight runs,
and each run is a tilted planar slab of the deck polygon, mapped exactly to
parts: asphalt where the bridge's carriageway runs, raised footway beside it,
granite kerbs between.

Under the deck the structure is parametric: evenly spaced piers and segmental
arches whose spring line sits just above the measured Thames level. Elevated
stretches of bridge ways with no mapped deck polygon (approach viaducts) are
built station by station from their own DSM profile.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .geomutil import grid_points
from .ground import Ground, Road, road_polygon
from .osm import polygons
from .partmap import Look, Parts
from .scene import Scene

PAINT = {
    "Westminster Bridge": (51, 107, 77),  # "Commons green"
    "Lambeth Bridge": (158, 41, 36),  # "Lords red"
}
STONE = (184, 178, 163)
DECK = 1.2  # m structural depth
KERB = 0.12
ASPHALT = Look("Asphalt", (66, 66, 68), collide=True)
FOOTWAY = Look("Pavement", (170, 166, 158), collide=True)
GRANITE = Look("Granite", (150, 150, 152), collide=True)
EMBANKMENT = Look("Granite", (160, 156, 146), collide=True)


@dataclass
class Bridge:
    name: str
    poly: Polygon
    axis_c: np.ndarray
    u: np.ndarray  # long axis
    v: np.ndarray
    s_min: float
    s_max: float
    profile_s: np.ndarray
    profile_z: np.ndarray
    over_water: bool
    railway: bool = False

    def deck_z(self, xy: np.ndarray) -> np.ndarray:
        s = (np.atleast_2d(xy) - self.axis_c) @ self.u
        return np.interp(s, self.profile_s, self.profile_z)


def find(scene: Scene, ground: Ground, surface) -> list[Bridge]:
    rivers = [w for w in ground.water if w.kind == "river"]
    river = unary_union([w.poly for w in rivers]) if rivers else None
    rails = [f.geom for f in scene.osm.lines if f.tags.get("railway") in ("rail", "light_rail") and f.tags.get("bridge") not in (None, "no")]
    out = []
    for f in scene.osm.areas:
        if f.tags.get("man_made") != "bridge":
            continue
        for poly in polygons(f.geom):
            if poly.area < 150 or not poly.intersects(scene.fetch_clip):
                continue
            rect = poly.minimum_rotated_rectangle
            c = np.array(rect.exterior.coords)[:4]
            e1, e2 = c[1] - c[0], c[2] - c[1]
            u = e1 if np.linalg.norm(e1) > np.linalg.norm(e2) else e2
            u = u / np.linalg.norm(u)
            v = np.array([-u[1], u[0]])
            center = c.mean(axis=0)
            g = grid_points(poly, 1.0, 0.6)
            if len(g) < 20:
                continue
            z = scene.dsm.sample(g[:, 0], g[:, 1])
            s = (g - center) @ u
            bins = np.arange(s.min(), s.max() + 2.0, 2.0)
            idx = np.digitize(s, bins)
            ps, pz = [], []
            for k in range(1, len(bins)):
                m = idx == k
                if m.sum() >= 3:
                    ps.append((bins[k - 1] + bins[k]) / 2)
                    pz.append(np.percentile(z[m], 30))
            if len(ps) < 3:
                continue
            pz = np.array(pz)
            pz = np.array([np.median(pz[max(0, i - 3) : i + 4]) for i in range(len(pz))])
            ps = np.array(ps)
            over = river is not None and poly.intersection(river).area > 0.25 * poly.area
            # Decks that sit on the bare earth (not over water) are left to the terrain.
            dtm = scene.dtm.sample(g[:, 0], g[:, 1])
            if not over and np.median(z - dtm) < 1.0:
                continue
            s_lo, s_hi = float(s.min()), float(s.max())
            ps = np.array(ps)
            # Landfall: walk outward from each end until the terrain road agrees
            # with the deck (bare-earth DTM is unreliable next to abutments), and
            # ramp the deck down onto it there.
            ends = []
            for sgn, s_end in ((-1, s_lo), (1, s_hi)):
                z_end = float(np.interp(s_end, ps, pz))
                land = None
                for k in np.arange(2.0, 16.0, 1.0):
                    pr = center + u * (s_end + sgn * k)
                    zl = surface.road(pr[None])[0]
                    ahead = surface.road(np.array([center + u * (s_end + sgn * (k + j)) for j in (1.0, 2.0, 3.0)]))
                    if np.isfinite(zl) and abs(zl - z_end) < 1.2 and np.ptp(np.append(ahead, zl)) < 0.25:
                        land = (k, float(zl))
                        break
                if land is None:
                    land = (3.0, z_end)
                ends.append((sgn, s_end, land))
            for sgn, s_end, (k, zl) in ends:
                if sgn < 0:
                    ps = np.concatenate([[s_end - k], ps])
                    pz = np.concatenate([[zl], pz])
                else:
                    ps = np.concatenate([ps, [s_end + k]])
                    pz = np.concatenate([pz, [zl]])
            ext_lo, ext_hi = ends[0][2][0], ends[1][2][0]
            is_rail = any(r.intersects(poly) for r in rails) and not f.tags.get("highway")
            # Run the deck out to its landfall at both ends: mapped deck outlines
            # and river banks rarely meet exactly, and a gap would be a hole.
            t = (np.asarray(poly.exterior.coords) - center) @ v
            slab = Polygon([center + u * (s_lo - ext_lo) + v * t.min(), center + u * (s_hi + ext_hi) + v * t.min(), center + u * (s_hi + ext_hi) + v * t.max(), center + u * (s_lo - ext_lo) + v * t.max()])
            steps = [shapely.affinity.translate(poly, *(u * d)) for d in np.arange(-ext_lo, ext_hi + 0.5, 1.0)]
            longer = unary_union([poly, *steps]).intersection(slab).buffer(0)
            if longer.geom_type == "Polygon" and longer.area > poly.area:
                poly = longer
                s_lo, s_hi = s_lo - ext_lo, s_hi + ext_hi
            out.append(Bridge(f.tags.get("name", ""), orient(poly, 1.0), center, u, v, s_lo, s_hi, ps, pz, over, is_rail))
    return out


def _simplify_profile(s: np.ndarray, z: np.ndarray, tol: float = 0.03) -> tuple[np.ndarray, np.ndarray]:
    line = LineString(np.column_stack([s, z])).simplify(tol)
    q = np.asarray(line.coords)
    return q[:, 0], q[:, 1]


def _slab_plate(P: Parts, poly: Polygon, b: Bridge, s0: float, s1: float, z0: float, z1: float, lift: float, thickness: float, look: Look) -> None:
    """Polygon on the deck between stations s0..s1 (a planar run of the profile)."""
    if poly.is_empty or poly.area < 1e-3:
        return
    slope = (z1 - z0) / max(s1 - s0, 1e-9)
    U = np.array([b.u[0], b.u[1], slope])
    U /= np.linalg.norm(U)
    V = np.array([b.v[0], b.v[1], 0.0])
    if np.cross(U, V)[2] < 0:
        V = -V
    O = np.array([*(b.axis_c + b.u * s0), z0 + lift])

    def conv(ring):
        p = np.asarray(ring.coords)
        s = (p - b.axis_c) @ b.u
        z = z0 + lift + (s - s0) * slope
        q = np.column_stack([p, z]) - O
        return np.column_stack([q @ U, q @ V])

    for part in polygons(poly):
        uv = Polygon(conv(part.exterior), [conv(r) for r in part.interiors])
        if not uv.is_valid:
            uv = uv.buffer(0)
        P.plate(uv, O, U, V, thickness, look)


def emit_one(P: Parts, scene: Scene, ground: Ground, b: Bridge) -> None:
    """Deck, kerbs, parapets and (over the river) piers and arches of one bridge."""
    deck_roads = ground.deck_roads or []
    road_polys = unary_union([road_polygon(r) for r in deck_roads]) if deck_roads else Polygon()
    paint = next((c for k, c in PAINT.items() if k in b.name), STONE)
    rail_look = Look("Metal", (92, 92, 96), collide=True)
    ps, pz = _simplify_profile(b.profile_s, b.profile_z)
    width = float(np.ptp((np.asarray(b.poly.exterior.coords) - b.axis_c) @ b.v))
    carriage = b.poly.intersection(road_polys.buffer(0.05)) if not road_polys.is_empty else Polygon()
    if carriage.area < 1.0:
        carriage = Polygon()
    footway = b.poly.difference(carriage) if not carriage.is_empty else b.poly
    R = float(np.hypot(*np.ptp(np.asarray(b.poly.exterior.coords), axis=0))) + 10
    for k in range(len(ps) - 1):
        s0, s1 = float(ps[k]), float(ps[k + 1])
        if s1 - s0 < 1e-3:
            continue
        band = Polygon([b.axis_c + b.u * s0 - b.v * R, b.axis_c + b.u * s1 - b.v * R, b.axis_c + b.u * s1 + b.v * R, b.axis_c + b.u * s0 + b.v * R])
        if not carriage.is_empty:
            _slab_plate(P, carriage.intersection(band), b, s0, s1, pz[k], pz[k + 1], 0.0, DECK, ASPHALT)
        _slab_plate(P, footway.intersection(band), b, s0, s1, pz[k], pz[k + 1], 0.0 if carriage.is_empty else KERB, DECK + KERB, rail_look if b.railway else FOOTWAY)
    metal = paint != STONE
    _parapets(P, b, Look("Metal" if metal else "Limestone", paint, collide=True), 0.0 if carriage.is_empty else KERB)
    if not carriage.is_empty:
        _deck_kerbs(P, b, carriage, footway)
    if b.over_water and (b.s_max - b.s_min) > 60:
        _piers_and_arches(P, b, ground, scene.water_level, width, Look("Limestone", STONE, collide=True), Look("Metal" if metal else "Limestone", paint, collide=False))


def _parapets(P: Parts, b: Bridge, look: Look, lift: float) -> None:
    ring = np.asarray(b.poly.exterior.coords)
    for i in range(len(ring) - 1):
        a, c = ring[i], ring[i + 1]
        L = float(np.linalg.norm(c - a))
        if L < 0.5:
            continue
        d = (c - a) / L
        if abs(d @ b.u) < 0.6:
            continue  # an end of the deck, where it meets the road
        inward = np.array([-d[1], d[0]])  # exterior ring is CCW: interior on the left
        sa, sc = (np.array([a, c]) - b.axis_c) @ b.u
        cuts = sorted({float(sa), float(sc), *[float(x) for x in b.profile_s if min(sa, sc) < x < max(sa, sc)]})
        for s0, s1 in zip(cuts[:-1], cuts[1:]):
            if s1 - s0 < 0.2:
                continue
            t0 = (s0 - sa) / (sc - sa)
            t1 = (s1 - sa) / (sc - sa)
            p0 = a + (c - a) * t0 + inward * 0.2
            p1 = a + (c - a) * t1 + inward * 0.2
            z0, z1 = b.deck_z(np.array([p0, p1])) + lift
            P.beam(np.array([*p0, z0 + 0.55]), np.array([*p1, z1 + 0.55]), 0.36, 1.1, (0.0, 0.0, 1.0), look)


def _deck_kerbs(P: Parts, b: Bridge, carriage, footway) -> None:
    edge = carriage.boundary.intersection(footway.buffer(0.05))
    for g in getattr(edge, "geoms", [edge]):
        if g.geom_type != "LineString" or g.length < 0.3:
            continue
        pts = np.asarray(g.simplify(0.1).coords)
        for i in range(len(pts) - 1):
            p, q = pts[i], pts[i + 1]
            L = float(np.linalg.norm(q - p))
            if L < 0.1:
                continue
            d = (q - p) / L
            nl = np.array([-d[1], d[0]])
            to_foot = nl if footway.contains(shapely.Point((p + q) / 2 + nl * 0.3)) else -nl
            zp, zq = b.deck_z(np.array([p, q])) + KERB
            mid0, mid1 = p + to_foot * 0.15, q + to_foot * 0.15
            P.beam(np.array([*mid0, zp - 0.15]), np.array([*mid1, zq - 0.15]), 0.3, 0.3, (0.0, 0.0, 1.0), GRANITE)


def _box(P: Parts, center2: np.ndarray, u: np.ndarray, len_u: float, len_v: float, z0: float, z1: float, look: Look) -> None:
    v = np.array([-u[1], u[0]])
    a = np.array([*(center2 - u * len_u / 2 - v * len_v / 2), z1])
    corners = [a, a + np.array([*(u * len_u), 0]), a + np.array([*(u * len_u + v * len_v), 0]), a + np.array([*(v * len_v), 0])]
    nrm = np.cross(corners[1] - corners[0], corners[3] - corners[0])
    if nrm[2] < 0:
        corners = corners[::-1]
    P.rect(*corners, (0.0, 0.0, 1.0), z1 - z0, look)


def _piers_and_arches(P: Parts, b: Bridge, ground: Ground, water_level: float, width: float, stone: Look, paint: Look) -> None:
    rivers = [w for w in ground.water if w.kind == "river"]
    river = rivers[0].poly if rivers else None
    n_spans = 7 if "Westminster" in b.name else (5 if ("Lambeth" in b.name or "Waterloo" in b.name) else max(3, int((b.s_max - b.s_min) // 45)))
    ss = np.linspace(b.s_min, b.s_max, 400)
    pts = b.axis_c + np.outer(ss, b.u)
    wet = shapely.contains_xy(river, pts[:, 0], pts[:, 1]) if river is not None else np.zeros(len(ss), dtype=bool)
    if not wet.any():
        return
    w0, w1 = ss[wet].min() - 4, ss[wet].max() + 4
    piers = np.linspace(w0, w1, n_spans + 1)
    pier_w = 4.0
    for s in piers:
        center = b.axis_c + b.u * s
        deck = float(b.deck_z(center[None])[0])
        _box(P, center, b.u, pier_w, width + 0.8, water_level - 3.0, deck - DECK, stone)
        # Cutwaters: pointed noses up- and downstream.
        for sgn in (-1, 1):
            base = center + b.v * sgn * (width / 2 + 0.4)
            tip = center + b.v * sgn * (width / 2 + 0.4 + pier_w * 0.6)
            l = base - b.u * pier_w / 2
            r = base + b.u * pier_w / 2
            ztop = min(deck - DECK, water_level + 2.5)
            for z in (ztop,):
                P.tri(np.array([*l, z]), np.array([*r, z]), np.array([*tip, z]), (0.0, 0.0, 1.0), z - (water_level - 3.0), stone)
    for k in range(n_spans):
        sa, sb = piers[k] + pier_w / 2, piers[k + 1] - pier_w / 2
        mid, half = (sa + sb) / 2, (sb - sa) / 2
        spring = water_level + 1.6
        nseg = 10
        ts = np.linspace(-1, 1, nseg + 1)
        prof = []
        for t in ts:
            s = mid + t * half
            d = float(b.deck_z((b.axis_c + b.u * s)[None])[0]) - DECK
            prof.append((s, d))
        rise = max(0.5, min(d for _, d in prof) - spring - 0.5)
        arch = [(s, spring + rise * np.sqrt(max(0.0, 1 - t * t))) for (s, _), t in zip(prof, ts)]
        # Soffit: tilted plates across the deck width.
        for j in range(nseg):
            (s0, z0), (s1, z1) = arch[j], arch[j + 1]
            hv = b.v * (width / 2)
            p0, p1 = b.axis_c + b.u * s0, b.axis_c + b.u * s1
            quad = np.array([[*(p0 - hv), z0], [*(p1 - hv), z1], [*(p1 + hv), z1], [*(p0 + hv), z0]])
            nrm = np.cross(quad[1] - quad[0], quad[3] - quad[0])
            if nrm[2] > 0:
                quad = quad[[1, 0, 3, 2]]
                nrm = -nrm
            P.rect(*quad, nrm, 0.5, paint)
        # Spandrels: the side faces between arch and deck, exact.
        for sgn in (-1, 1):
            side = b.axis_c + b.v * sgn * (width / 2)
            top = [(s, d) for s, d in prof]
            poly = Polygon([(s - mid, z) for s, z in arch] + [(s - mid, d) for s, d in reversed(top)])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            U = np.array([b.u[0], b.u[1], 0.0])
            Vv = np.array([0.0, 0.0, 1.0])
            if np.cross(U, Vv) @ np.array([*(b.v * sgn), 0.0]) < 0:
                U = -U
                poly = shapely.affinity.scale(poly, xfact=-1.0, yfact=1.0, origin=(0, 0))
            O = np.array([*(side + b.u * mid), 0.0])
            P.plate(poly, O, U, Vv, 0.6, paint, angles=[0.0])


def viaducts(P: Parts, ground: Ground, bridges: list[Bridge]) -> None:
    """Elevated bridge stretches outside any mapped deck polygon. Each station
    segment is a planar quad; the part already covered by a deck polygon is
    cut away, so viaduct and deck meet without gaps or overlaps."""
    decks = unary_union([b.poly for b in bridges]) if bridges else Polygon()
    rail = Look("Concrete", (150, 148, 142), collide=True)
    for r in ground.deck_roads or []:
        if r.deck_z is None:
            continue
        nrm = np.column_stack([-r.tangents[:, 1], r.tangents[:, 0]])
        left = r.stations + nrm * r.left[:, None]
        right = r.stations - nrm * r.right[:, None]
        for i in range(len(r.stations) - 1):
            z0, z1 = float(r.deck_z[i]), float(r.deck_z[i + 1])
            quad2 = Polygon([left[i], right[i], right[i + 1], left[i + 1]])
            if not quad2.is_valid:
                quad2 = quad2.buffer(0)
            rest = quad2.difference(decks) if not decks.is_empty else quad2
            if rest.is_empty or rest.area < 0.05:
                continue
            pts = np.array([[*left[i], z0], [*right[i], z0], [*right[i + 1], z1], [*left[i + 1], z1]])
            A = np.column_stack([pts[:, :2], np.ones(4)])
            plane = np.linalg.lstsq(A, pts[:, 2], rcond=None)[0]
            for part in polygons(rest):
                c = np.asarray(part.centroid.coords[0])
                from .buildparts import plane_frame, to_plane_coords

                O, U, V = plane_frame(plane, c)
                P.plate(to_plane_coords(orient(part, 1.0), plane, O, U, V), O, U, V, DECK, ASPHALT)
            if decks.is_empty or not decks.contains(shapely.Point((r.stations[i] + r.stations[i + 1]) / 2)):
                for edge, sgn in ((left, 1.0), (right, -1.0)):
                    p0 = edge[i] - nrm[i] * sgn * 0.2
                    p1 = edge[i + 1] - nrm[i + 1] * sgn * 0.2
                    P.beam(np.array([*p0, z0 + 0.55]), np.array([*p1, z1 + 0.55]), 0.35, 1.1, (0.0, 0.0, 1.0), rail)


def deck_height_fn(bridges: list[Bridge]):
    """deck_z(road, xy): bridge deck under points of an elevated road."""

    def fn(r: Road, xy: np.ndarray) -> np.ndarray:
        xy = np.atleast_2d(xy)
        out = np.full(len(xy), np.nan)
        for b in bridges:
            inside = shapely.contains_xy(b.poly.buffer(0.5), xy[:, 0], xy[:, 1])
            if inside.any():
                out[inside] = b.deck_z(xy[inside])
        miss = np.isnan(out)
        if miss.any() and r.deck_z is not None:
            line = LineString(r.stations)
            seg = np.linalg.norm(np.diff(r.stations, axis=0), axis=1)
            s_st = np.concatenate([[0], np.cumsum(seg)])
            s = np.array([line.project(shapely.Point(p)) for p in xy[miss]])
            out[miss] = np.interp(s, s_st, r.deck_z)
        return out

    return fn


def embankment(P: Parts, scene: Scene, ground: Ground, surface, bridges: list[Bridge] | None = None) -> int:
    """Granite river walls with a parapet, along the Thames banks. Under a
    bridge deck the wall stops below the deck, so it never crosses the road."""
    n = 0
    clip_edge = scene.fetch_clip.exterior.buffer(2.0)
    decks = [(b.poly.buffer(3.0), b) for b in (bridges or [])]
    for w in ground.water:
        if w.kind != "river":
            continue
        for poly in polygons(w.poly):
            ring = np.asarray(orient(poly, 1.0).exterior.simplify(0.4).coords)
            for i in range(len(ring) - 1):
                a, c = ring[i], ring[i + 1]
                L = float(np.linalg.norm(c - a))
                if L < 0.3 or clip_edge.contains(shapely.Point((a + c) / 2)):
                    continue
                d = (c - a) / L
                into_river = np.array([-d[1], d[0]])  # CCW: water on the left
                land = (a + c) / 2 - into_river * 1.6
                z_land = float(surface.top(land[None])[0])
                top = max(z_land + 0.95, w.level + 1.0)
                mid = shapely.Point((a + c) / 2)
                for zone, b in decks:
                    if zone.contains(mid):
                        top = min(top, float(b.deck_z(((a + c) / 2)[None])[0]) - DECK - 0.1)
                if top <= w.level - 1.0:
                    continue
                # Outer face towards the river; the wall extends back onto land.
                P.vbox(c, a, w.level - 1.5, top, 0.7, into_river, EMBANKMENT)
                n += 1
    return n
