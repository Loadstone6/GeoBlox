"""Map reconstructed geometry onto Roblox parts.

Minecraft converters such as GeoCraft and Arnis place blocks; Roblox's
equivalent is the BasePart: a box, a wedge (an extruded right triangle), a
cylinder or a ball. This module turns geometry into those primitives exactly,
with no visual approximation:

* a planar polygon (roof face, wall profile, bridge deck slab) is swept into
  trapezoids along its dominant direction. Each trapezoid is one box plus at
  most two wedges, so rectilinear shapes come out as a handful of boxes.
  A constrained Delaunay triangulation is the fallback for curvy outlines,
  whichever needs fewer parts.
* an arbitrary triangle is two wedges split at the altitude from its longest
  edge (the WedgePart technique popularised by EgoMoose); a right triangle is
  one wedge.
* walls, kerbs, beams and markings are boxes along a segment.

All geometry comes in as British National Grid metres (E, N, H) and leaves as
Roblox studs (X east, Y up, Z south), the orientation-preserving map used
everywhere else in the pipeline. Parts are plates: the visible surface lies on
the input plane and the thickness extends behind it.

Wedge convention (Roblox WedgePart, local axes): the solid is the right
triangle with corners (y, z) = (-h/2, -L/2), (-h/2, +L/2), (+h/2, +L/2),
extruded along X. The square corner is at bottom-back and the slope faces front
(-Z).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from .config import PARAMS, Region

MPS = PARAMS.metres_per_stud

SHAPE_BLOCK, SHAPE_WEDGE, SHAPE_CYLINDER, SHAPE_BALL = 0, 1, 2, 3

# Enum.Material names, by index (mirrored by tools/bake.luau via the manifest).
MATERIALS = [
    "SmoothPlastic", "Plastic", "Asphalt", "Pavement", "Concrete", "Brick", "Limestone", "Sandstone", "Slate",
    "Granite", "Marble", "Cobblestone", "Glass", "Metal", "DiamondPlate", "CorrodedMetal", "Wood", "WoodPlanks",
    "Grass", "LeafyGrass", "Neon", "Fabric", "Plaster", "Rock", "Basalt", "Ground", "Foil", "Rubber", "Pebble",
]
MAT = {name: i for i, name in enumerate(MATERIALS)}

# CollectionService tags for parts the runtime animates or lights.
TAGS = [
    "", "GeoBloxWindow", "GeoBloxShopfront", "GeoBloxLantern", "GeoBloxBeacon", "GeoBloxSignalRed",
    "GeoBloxSignalAmber", "GeoBloxSignalGreen", "GeoBloxDialGlow", "GeoBloxHourHand", "GeoBloxMinuteHand",
    "GeoBloxEyeWheel", "GeoBloxEyeCapsule",
]
TAG = {name: i for i, name in enumerate(TAGS)}

EXTRA_NONE, EXTRA_SPHERE_MESH = 0, 1


@dataclass(frozen=True)
class Look:
    material: str = "SmoothPlastic"
    color: tuple = (200, 200, 200)  # sRGB 0..255
    collide: bool = True
    shadow: bool = True
    transparency: float = 0.0
    reflectance: float = 0.0
    tag: str = ""

    def with_(self, **kw) -> "Look":
        d = {**self.__dict__, **kw}
        return Look(**d)


def rgb_tuple(c) -> tuple:
    """Float 0..1 colour (array) or 0..255 tuple -> 0..255 int tuple."""
    a = np.asarray(c, dtype=float)
    if a.max() <= 1.0 + 1e-9:
        a = a * 255.0
    return tuple(int(v) for v in np.clip(np.round(a), 0, 255))


# ------------------------------------------------------------------ records

PART_DTYPE = np.dtype(
    [
        ("shape", "u1"), ("material", "u1"), ("flags", "u1"), ("tag", "u1"),
        ("color", "u1", 3), ("transparency", "u1"),
        ("reflectance", "u1"), ("extra", "u1"), ("pad", "<u2"),
        ("pos", "<f4", 3), ("size", "<f4", 3), ("quat", "<f4", 4),
    ]
)
assert PART_DTYPE.itemsize == 52

FLAG_COLLIDE, FLAG_SHADOW, FLAG_QUERY = 1, 2, 4


def quat_from_matrix(R: np.ndarray) -> np.ndarray:
    """Rotation matrix (columns = local X, Y, Z axes) -> quaternion (x, y, z, w)."""
    m = R
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w])
    return q / np.linalg.norm(q)


def matrix_from_quat(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v * 0.0


MIN_SIZE = 0.001  # Roblox's minimum part dimension (studs)


@dataclass
class Parts:
    """Accumulates parts in stud space. Input helpers take BNG metres."""

    region: Region
    rows: list = field(default_factory=list)

    # ---------------------------------------------------------- conversion
    def S(self, p) -> np.ndarray:
        """BNG metres (E, N, H) -> studs (X, Y, Z)."""
        p = np.asarray(p, dtype=np.float64)
        e0, n0 = self.region.origin
        return np.stack([(p[..., 0] - e0) / MPS, (p[..., 2] - PARAMS.h0) / MPS, -(p[..., 1] - n0) / MPS], axis=-1)

    @staticmethod
    def Sdir(d) -> np.ndarray:
        d = np.asarray(d, dtype=np.float64)
        return np.stack([d[..., 0], d[..., 2], -d[..., 1]], axis=-1)

    def __len__(self) -> int:
        return len(self.rows)

    # ---------------------------------------------------------- primitives (studs)
    def add(self, shape: int, center, X, Y, Z, size, look: Look, extra: int = EXTRA_NONE) -> None:
        R = np.column_stack([X, Y, Z])
        if np.linalg.det(R) < 0:
            if shape in (SHAPE_BLOCK, SHAPE_BALL):
                R[:, 0] = -R[:, 0]
            else:  # a mirrored wedge/cylinder frame is a construction bug
                raise ValueError("left-handed frame for an asymmetric part")
        size = np.maximum(np.asarray(size, dtype=np.float64), MIN_SIZE)
        if not (np.all(np.isfinite(center)) and np.all(np.isfinite(R)) and np.all(np.isfinite(size))):
            return
        self.rows.append((shape, np.asarray(center, dtype=np.float64), R, size, look, extra))

    def box(self, center, X, Y, Z, size, look: Look, extra: int = EXTRA_NONE) -> None:
        self.add(SHAPE_BLOCK, center, X, Y, Z, size, look, extra)

    def rect_s(self, c0, c1, c2, c3, n_out, thickness: float, look: Look) -> None:
        """Box whose outer face is the rectangle c0 c1 c2 c3 (studs, in order)."""
        c0, c1, c3 = (np.asarray(c, dtype=np.float64) for c in (c0, c1, c3))
        U = c1 - c0
        V = c3 - c0
        a, b = np.linalg.norm(U), np.linalg.norm(V)
        if a < MIN_SIZE or b < MIN_SIZE:
            return
        U = U / a
        V = V - U * (V @ U)
        V = _unit(V)
        n = _unit(np.asarray(n_out, dtype=np.float64))
        center = c0 + U * (a / 2) + V * (b / 2) - n * (thickness / 2)
        X, Z = U, V
        Y = np.cross(Z, X)
        self.box(center, X, Y, Z, (a, thickness, b), look)

    def wedge_s(self, R, A, B, n_out, thickness: float, look: Look) -> None:
        """Wedge whose outer face is the right triangle with its square corner at R
        and legs R->A, R->B (studs)."""
        R, A, B = (np.asarray(p, dtype=np.float64) for p in (R, A, B))
        h = float(np.linalg.norm(B - R))
        L = float(np.linalg.norm(A - R))
        if h < MIN_SIZE or L < MIN_SIZE:
            return
        Y = (B - R) / h
        Z = (R - A) / L
        Z = _unit(Z - Y * (Z @ Y))
        X = np.cross(Y, Z)
        n = _unit(np.asarray(n_out, dtype=np.float64))
        center = R + Y * (h / 2) - Z * (L / 2) - n * (thickness / 2)
        self.add(SHAPE_WEDGE, center, X, Y, Z, (thickness, h, L), look)

    def tri_s(self, a, b, c, n_out, thickness: float, look: Look) -> int:
        """Any triangle (studs) as one or two wedges. Returns parts added."""
        P = [np.asarray(p, dtype=np.float64) for p in (a, b, c)]
        lens = [np.linalg.norm(P[(i + 1) % 3] - P[(i + 2) % 3]) for i in range(3)]  # edge opposite vertex i
        i = int(np.argmax(lens))
        p, q, r = P[i], P[(i + 1) % 3], P[(i + 2) % 3]
        qr = r - q
        L2 = float(qr @ qr)
        if L2 < 1e-12:
            return 0
        t = float(np.clip((p - q) @ qr / L2, 0.0, 1.0))
        d = q + qr * t
        if np.linalg.norm(p - d) < MIN_SIZE:
            return 0
        n = 0
        if np.linalg.norm(q - d) > 1e-3:
            self.wedge_s(d, q, p, n_out, thickness, look)
            n += 1
        if np.linalg.norm(r - d) > 1e-3:
            self.wedge_s(d, r, p, n_out, thickness, look)
            n += 1
        return n

    def beam_s(self, p0, p1, width: float, height: float, up, look: Look) -> None:
        """Box centred on segment p0-p1 (studs): length along it, `height` along
        `up` (made perpendicular), `width` across."""
        p0, p1 = np.asarray(p0, dtype=np.float64), np.asarray(p1, dtype=np.float64)
        d = p1 - p0
        L = float(np.linalg.norm(d))
        if L < MIN_SIZE:
            return
        X = d / L
        up = np.asarray(up, dtype=np.float64)
        Y = up - X * (up @ X)
        if np.linalg.norm(Y) < 1e-6:
            Y = np.array([0.0, 0.0, 1.0]) - X * X[2]
        Y = _unit(Y)
        Z = np.cross(X, Y)
        self.box((p0 + p1) / 2, X, Y, Z, (L, height, width), look)

    def cylinder_s(self, p0, p1, diameter: float, look: Look) -> None:
        p0, p1 = np.asarray(p0, dtype=np.float64), np.asarray(p1, dtype=np.float64)
        d = p1 - p0
        L = float(np.linalg.norm(d))
        if L < MIN_SIZE:
            return
        X = d / L
        ref = np.array([0.0, 1.0, 0.0]) if abs(X[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
        Y = _unit(ref - X * (ref @ X))
        Z = np.cross(X, Y)
        self.add(SHAPE_CYLINDER, (p0 + p1) / 2, X, Y, Z, (L, diameter, diameter), look)

    def ball_s(self, center, diameter: float, look: Look) -> None:
        I = np.eye(3)
        self.add(SHAPE_BALL, center, I[:, 0], I[:, 1], I[:, 2], (diameter, diameter, diameter), look)

    def ellipsoid_s(self, center, X, Y, Z, size, look: Look) -> None:
        """Block with a sphere SpecialMesh: an ellipsoid with diameters `size`."""
        self.add(SHAPE_BLOCK, center, X, Y, Z, size, look, EXTRA_SPHERE_MESH)

    # ---------------------------------------------------------- BNG-metre front ends
    def rect(self, c0, c1, c2, c3, n_out, thickness_m: float, look: Look) -> None:
        self.rect_s(*self.S(np.array([c0, c1, c2, c3])), self.Sdir(n_out), thickness_m / MPS, look)

    def tri(self, a, b, c, n_out, thickness_m: float, look: Look) -> int:
        return self.tri_s(*self.S(np.array([a, b, c])), self.Sdir(n_out), thickness_m / MPS, look)

    def beam(self, p0, p1, width_m: float, height_m: float, up, look: Look) -> None:
        q = self.S(np.array([p0, p1]))
        self.beam_s(q[0], q[1], width_m / MPS, height_m / MPS, self.Sdir(up), look)

    def cylinder(self, p0, p1, diameter_m: float, look: Look) -> None:
        q = self.S(np.array([p0, p1]))
        self.cylinder_s(q[0], q[1], diameter_m / MPS, look)

    def ball(self, c, diameter_m: float, look: Look) -> None:
        self.ball_s(self.S(np.asarray(c)), diameter_m / MPS, look)

    def vbox(self, a, c, z0: float, z1: float, depth_m: float, outward, look: Look, out_offset: float = 0.0) -> None:
        """Vertical box on a wall line: face from a to c (BNG xy), z0..z1, whose
        outer face sits `out_offset` m in front of the line and which extends
        `depth_m` behind it."""
        a, c = np.asarray(a, dtype=np.float64), np.asarray(c, dtype=np.float64)
        o = np.asarray(outward, dtype=np.float64)[:2] * out_offset
        if z1 - z0 < 1e-4 or np.linalg.norm(c - a) < 1e-4:
            return
        q = np.array([[a[0] + o[0], a[1] + o[1], z0], [c[0] + o[0], c[1] + o[1], z0], [c[0] + o[0], c[1] + o[1], z1], [a[0] + o[0], a[1] + o[1], z1]])
        n3 = np.array([outward[0], outward[1], 0.0])
        self.rect(q[0], q[1], q[2], q[3], n3, depth_m, look)

    def plate(self, poly2d: Polygon, origin, U, V, thickness_m: float, look: Look, angles: list[float] | None = None) -> int:
        """Planar polygon given in a plane's 2D frame (origin + u·U + v·V, BNG
        metres) as boxes and wedges. The outer face is the side U×V points to."""
        U, V = np.asarray(U, dtype=np.float64), np.asarray(V, dtype=np.float64)
        N = np.cross(U, V)
        origin = np.asarray(origin, dtype=np.float64)
        # Work near the origin: BNG coordinates are ~5e5 m.
        if poly2d.is_empty:
            return 0
        cu, cv = poly2d.centroid.coords[0]
        poly2d = shapely.affinity.translate(poly2d, -cu, -cv)
        origin = origin + U * cu + V * cv
        pieces = decompose(poly2d, angles)
        n_out = self.Sdir(N)
        t = thickness_m / MPS
        added = 0

        def P(uv):
            uv = np.asarray(uv, dtype=np.float64)
            return self.S(origin + np.outer(uv[:, 0], U) + np.outer(uv[:, 1], V))

        for kind, pts in pieces:
            if _piece_area(kind, pts) < 0.02:  # m^2: slivers are invisible
                continue
            q = P(pts)
            if kind == "rect":
                self.rect_s(q[0], q[1], q[2], q[3], n_out, t, look)
                added += 1
            elif kind == "rtri":
                self.wedge_s(q[0], q[1], q[2], n_out, t, look)
                added += 1
            else:
                added += self.tri_s(q[0], q[1], q[2], n_out, t, look)
        return added

    def flat(self, poly: Polygon, z: float, thickness_m: float, look: Look, up: bool = True) -> int:
        """Horizontal polygon (BNG xy) at height z; outer face up (or down)."""
        if up:
            return self.plate(poly, (0.0, 0.0, z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), thickness_m, look)
        mirrored = shapely.affinity.scale(poly, xfact=1.0, yfact=-1.0, origin=(0, 0))
        return self.plate(mirrored, (0.0, 0.0, z), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0), thickness_m, look)

    # ---------------------------------------------------------- export
    def records(self, anchor: np.ndarray) -> np.ndarray:
        out = np.zeros(len(self.rows), dtype=PART_DTYPE)
        for i, (shape, center, R, size, look, extra) in enumerate(self.rows):
            out[i]["shape"] = shape
            out[i]["material"] = MAT[look.material]
            out[i]["flags"] = (FLAG_COLLIDE if look.collide else 0) | (FLAG_SHADOW if look.shadow else 0)
            out[i]["tag"] = TAG[look.tag]
            out[i]["color"] = look.color
            out[i]["transparency"] = int(round(np.clip(look.transparency, 0, 1) * 255))
            out[i]["reflectance"] = int(round(np.clip(look.reflectance, 0, 1) * 255))
            out[i]["extra"] = extra
            out[i]["pos"] = center - anchor
            out[i]["size"] = size
            out[i]["quat"] = quat_from_matrix(R)
        return out

    def extend(self, other: "Parts") -> None:
        self.rows.extend(other.rows)


# ------------------------------------------------------------------ polygon decomposition

def _rings(poly: Polygon) -> list[np.ndarray]:
    poly = orient(poly, 1.0)
    out = []
    for ring in [poly.exterior, *poly.interiors]:
        c = np.asarray(ring.coords)[:-1]
        if len(c) >= 3:
            out.append(c)
    return out


def _rot(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s], [s, c]])


def _trapezoids(rings: list[np.ndarray], eps: float) -> list[list[float]]:
    """Sweep along x: maximal trapezoids [xa, xb, ba, bb, ta, tb] (bottom and top
    edge heights at both ends) covering the polygon."""
    allx = np.concatenate([r[:, 0] for r in rings])
    xs = np.sort(allx)
    keep = np.concatenate([[True], np.diff(xs) > eps])
    xs = xs[keep]
    edges = []
    for r in rings:
        a = r
        b = np.roll(r, -1, axis=0)
        for (x0, y0), (x1, y1) in zip(a, b):
            if abs(x1 - x0) <= eps * 0.5:
                continue
            if x0 > x1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            edges.append((x0, y0, x1, y1))
    if not edges:
        return []
    E = np.array(edges)
    slope_all = (E[:, 3] - E[:, 1]) / (E[:, 2] - E[:, 0])
    out: list[list[float]] = []
    open_: dict[tuple[int, int], list[float]] = {}
    q = 1.0 / eps
    for k in range(len(xs) - 1):
        xa, xb = float(xs[k]), float(xs[k + 1])
        if xb - xa <= eps:
            continue
        sel = (E[:, 0] <= xa + eps) & (E[:, 2] >= xb - eps)
        if not sel.any():
            open_ = {}
            continue
        S, sl = E[sel], slope_all[sel]
        ya = S[:, 1] + sl * (xa - S[:, 0])
        yb = S[:, 1] + sl * (xb - S[:, 0])
        order = np.argsort((ya + yb) / 2)
        nxt: dict[tuple[int, int], list[float]] = {}
        for j in range(0, len(order) - 1, 2):
            lo, hi = order[j], order[j + 1]
            ba, bb, ta, tb = float(ya[lo]), float(yb[lo]), float(ya[hi]), float(yb[hi])
            key = (int(round(ba * q)), int(round(ta * q)))
            prev = open_.get(key)
            merged = False
            if prev is not None:
                # Continue the previous trapezoid if both edges are collinear.
                pxa, pxb, pba, pbb, pta, ptb = prev
                w = pxb - pxa
                sb = (pbb - pba) / w
                st = (ptb - pta) / w
                if abs(pbb + sb * (xb - pxb) - bb) < 2 * eps and abs(ptb + st * (xb - pxb) - tb) < 2 * eps:
                    prev[1], prev[3], prev[5] = xb, bb, tb
                    nxt[(int(round(bb * q)), int(round(tb * q)))] = prev
                    merged = True
            if not merged:
                t = [xa, xb, ba, bb, ta, tb]
                out.append(t)
                nxt[(int(round(bb * q)), int(round(tb * q)))] = t
        open_ = nxt
    return out


def _split_trapezoid(t: list[float], eps: float) -> list[tuple[str, list]]:
    xa, xb, ba, bb, ta, tb = t
    if xb - xa <= eps:
        return []
    lo, hi = max(ba, bb), min(ta, tb)
    pieces: list[tuple[str, list]] = []
    if hi < lo - eps:
        # Steep edges cross inside the slab: two general triangles.
        pieces.append(("tri", [(xa, ba), (xb, bb), (xb, tb)]))
        pieces.append(("tri", [(xa, ba), (xb, tb), (xa, ta)]))
        return pieces
    if hi - lo > eps:
        pieces.append(("rect", [xa, xb, lo, hi]))
    hi = max(hi, lo)
    if ta - tb > eps:
        pieces.append(("rtri", [(xa, tb), (xb, tb), (xa, ta)]))
    elif tb - ta > eps:
        pieces.append(("rtri", [(xb, ta), (xa, ta), (xb, tb)]))
    if bb - ba > eps:
        pieces.append(("rtri", [(xa, bb), (xb, bb), (xa, ba)]))
    elif ba - bb > eps:
        pieces.append(("rtri", [(xb, ba), (xa, ba), (xb, bb)]))
    return pieces


def _merge_rects(rects: list[list[float]], eps: float) -> list[list[float]]:
    """Join rectangles that share a full vertical edge."""
    q = 1.0 / eps
    changed = True
    while changed:
        changed = False
        rects = sorted(rects, key=lambda r: r[0])
        by_left: dict[tuple, int] = {}
        for i, (x0, x1, y0, y1) in enumerate(rects):
            by_left[(int(round(x0 * q)), int(round(y0 * q)), int(round(y1 * q)))] = i
        used = set()
        out = []
        for i, r in enumerate(rects):
            if i in used:
                continue
            x0, x1, y0, y1 = r
            j = by_left.get((int(round(x1 * q)), int(round(y0 * q)), int(round(y1 * q))))
            while j is not None and j not in used and j != i:
                used.add(j)
                x1 = rects[j][1]
                changed = True
                j = by_left.get((int(round(x1 * q)), int(round(y0 * q)), int(round(y1 * q))))
            used.add(i)
            out.append([x0, x1, y0, y1])
        rects = out
    return rects


def _right_angle(p: np.ndarray, tol_deg: float = 0.3) -> int:
    """Index of the vertex with a right angle, or -1."""
    for i in range(3):
        a, b, c = p[i], p[(i + 1) % 3], p[(i + 2) % 3]
        u, v = b - a, c - a
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        if nu < 1e-9 or nv < 1e-9:
            return -1
        if abs(u @ v) / (nu * nv) < np.sin(np.radians(tol_deg)):
            return i
    return -1


def _slab_pieces(poly: Polygon, angle: float, eps: float) -> list[tuple[str, np.ndarray]]:
    R = _rot(-angle)
    Rinv = _rot(angle)
    rings = [r @ R.T for r in _rings(poly)]
    traps = _trapezoids(rings, eps)
    rects, others = [], []
    for t in traps:
        for kind, pts in _split_trapezoid(t, eps):
            if kind == "rect":
                rects.append(pts)
            else:
                others.append((kind, pts))
    rects = _merge_rects(rects, eps)
    out: list[tuple[str, np.ndarray]] = []
    for x0, x1, y0, y1 in rects:
        c = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) @ Rinv.T
        out.append(("rect", c))
    for kind, pts in others:
        out.append((kind, np.asarray(pts, dtype=np.float64) @ Rinv.T))
    return out


def _cdt_pieces(poly: Polygon) -> list[tuple[str, np.ndarray]]:
    from .geomutil import triangulate

    V2, T = triangulate(poly)
    out = []
    for t in T:
        p = V2[t]
        k = _right_angle(p)
        if k >= 0:
            out.append(("rtri", np.array([p[k], p[(k + 1) % 3], p[(k + 2) % 3]])))
        else:
            out.append(("tri", p))
    return out


def _piece_area(kind: str, pts) -> float:
    p = np.asarray(pts, dtype=np.float64)
    if kind == "rect":
        return float(np.linalg.norm(p[1] - p[0]) * np.linalg.norm(p[3] - p[0]))
    return float(abs((p[1, 0] - p[0, 0]) * (p[2, 1] - p[0, 1]) - (p[2, 0] - p[0, 0]) * (p[1, 1] - p[0, 1])) / 2)


def _cost(pieces) -> int:
    return sum(2 if k == "tri" else 1 for k, _ in pieces)


def dominant_angles(poly: Polygon, k: int = 3) -> list[float]:
    """Candidate sweep directions: the strongest edge orientations (mod 90°)."""
    weights: dict[int, float] = {}
    for r in _rings(poly):
        d = np.roll(r, -1, axis=0) - r
        L = np.hypot(d[:, 0], d[:, 1])
        ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90.0
        for a, w in zip(ang, L):
            b = int(round(a * 4)) % 360  # quarter-degree bins
            weights[b] = weights.get(b, 0.0) + w
    best = sorted(weights.items(), key=lambda kv: -kv[1])
    out: list[float] = []
    for b, _ in best:
        a = np.radians(b / 4.0)
        if all(abs((a - o + np.pi / 4) % (np.pi / 2) - np.pi / 4) > np.radians(1.0) for o in out):
            out.append(a)
        if len(out) >= k:
            break
    return out or [0.0]


def decompose(poly: Polygon, angles: list[float] | None = None, eps: float = 2e-4) -> list[tuple[str, np.ndarray]]:
    """Polygon (2D, holes allowed) -> exact pieces: ("rect", 4 corners in order),
    ("rtri", [square corner, leg end, leg end]) or ("tri", 3 points)."""
    if poly.is_empty or poly.area < 1e-6:
        return []
    if poly.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for g in poly.geoms:
            if g.geom_type in ("Polygon", "MultiPolygon"):
                out.extend(decompose(g, angles, eps))
        return out
    if poly.geom_type != "Polygon":
        return []
    poly = poly.buffer(0) if not poly.is_valid else poly
    if poly.geom_type != "Polygon":
        return decompose(poly, angles, eps)
    cands = angles if angles is not None else dominant_angles(poly)
    best = None
    for a in cands:
        try:
            pieces = _slab_pieces(poly, a, eps)
        except (ValueError, IndexError):
            continue
        if best is None or _cost(pieces) < _cost(best):
            best = pieces
    nverts = sum(len(r) for r in _rings(poly))
    if best is None or nverts <= 12 or _cost(best) > 2 * nverts:
        try:
            cdt = _cdt_pieces(poly)
            if best is None or _cost(cdt) < _cost(best):
                best = cdt
        except Exception:  # noqa: BLE001 - Triangle can reject degenerate input
            pass
    return best or []


# ------------------------------------------------------------------ geometry back-conversion (tests, previews)

def part_triangles(shape: int, center, R, size, extra: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """World-space triangles of a part (for previews and tests)."""
    sx, sy, sz = np.asarray(size) / 2
    center = np.asarray(center)
    if shape == SHAPE_BLOCK and extra != EXTRA_SPHERE_MESH:
        c = np.array([[x, y, z] for x in (-sx, sx) for y in (-sy, sy) for z in (-sz, sz)])
        T = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
        return center + c @ R.T, T
    if shape == SHAPE_WEDGE:
        tri = np.array([[-sy, -sz], [-sy, sz], [sy, sz]])
        c = np.array([[x, y, z] for x in (-sx, sx) for y, z in tri])
        T = np.array([[0, 2, 1], [3, 4, 5], [0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [2, 0, 3], [2, 3, 5]])
        return center + c @ R.T, T
    if shape == SHAPE_CYLINDER:
        n = 12
        ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
        ring = np.column_stack([np.cos(ang) * sy, np.sin(ang) * sz])
        c = np.vstack([np.column_stack([np.full(n, -sx), ring]), np.column_stack([np.full(n, sx), ring])])
        T = []
        for i in range(n):
            j = (i + 1) % n
            T += [[i, j, n + j], [i, n + j, n + i]]
        for i in range(1, n - 1):
            T += [[0, i + 1, i], [n, n + i, n + i + 1]]
        return center + c @ R.T, np.array(T)
    # Ball / ellipsoid: a coarse UV sphere.
    lat, lon = 6, 10
    pts = [[0, sy, 0]]
    for i in range(1, lat):
        th = np.pi * i / lat
        for j in range(lon):
            ph = 2 * np.pi * j / lon
            pts.append([np.sin(th) * np.cos(ph) * sx, np.cos(th) * sy, np.sin(th) * np.sin(ph) * sz])
    pts.append([0, -sy, 0])
    pts = np.array(pts)
    T = []
    for j in range(lon):
        T.append([0, 1 + (j + 1) % lon, 1 + j])
    for i in range(lat - 2):
        for j in range(lon):
            a = 1 + i * lon + j
            b = 1 + i * lon + (j + 1) % lon
            T += [[a, b, b + lon], [a, b + lon, a + lon]]
    last = len(pts) - 1
    base = 1 + (lat - 2) * lon
    for j in range(lon):
        T.append([last, base + j, base + (j + 1) % lon])
    return center + pts @ R.T, np.array(T)
