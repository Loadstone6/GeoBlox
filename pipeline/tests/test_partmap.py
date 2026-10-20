"""Exactness of the geometry -> Roblox part mapping."""

import numpy as np
import pytest
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from geoblox.config import Region
from geoblox.partmap import (
    SHAPE_BLOCK,
    SHAPE_WEDGE,
    Look,
    Parts,
    decompose,
    matrix_from_quat,
    part_triangles,
    quat_from_matrix,
)

REGION = Region("test", (0.0, 0.0, 100.0, 100.0))
LOOK = Look()


def outer_face(parts: Parts, i: int, n_out: np.ndarray) -> np.ndarray:
    """Vertices of part i lying on its outer face (furthest along n_out)."""
    shape, center, R, size, _, extra = parts.rows[i]
    V, _ = part_triangles(shape, center, R, size, extra)
    d = V @ n_out
    return V[d > d.max() - 1e-6]


def test_wedge_convention_matches_right_triangle():
    rng = np.random.default_rng(1)
    for _ in range(50):
        R = rng.normal(size=3) * 10
        a = rng.normal(size=3)
        a /= np.linalg.norm(a)
        b = rng.normal(size=3)
        b -= a * (b @ a)
        b /= np.linalg.norm(b)
        A = R + a * rng.uniform(0.5, 9)
        B = R + b * rng.uniform(0.5, 9)
        n = np.cross(a, b)
        P = Parts(REGION)
        P.wedge_s(R, A, B, n, 0.4, LOOK)
        assert P.rows[0][0] == SHAPE_WEDGE
        face = outer_face(P, 0, n)
        assert len(face) == 3
        for target in (R, A, B):
            assert np.min(np.linalg.norm(face - target, axis=1)) < 1e-6


def test_general_triangle_two_wedges_cover_it_exactly():
    rng = np.random.default_rng(2)
    for _ in range(50):
        a, b, c = rng.normal(size=(3, 3)) * 8
        n = np.cross(b - a, c - a)
        n /= np.linalg.norm(n)
        P = Parts(REGION)
        k = P.tri_s(a, b, c, n, 0.3, LOOK)
        assert 1 <= k <= 2
        area = 0.0
        pts = []
        for i in range(k):
            f = outer_face(P, i, n)
            assert len(f) == 3
            area += 0.5 * np.linalg.norm(np.cross(f[1] - f[0], f[2] - f[0]))
            pts.append(f)
        target = 0.5 * np.linalg.norm(np.cross(b - a, c - a))
        assert area == pytest.approx(target, rel=1e-6)
        # Every outer-face vertex lies in the triangle's plane.
        for f in pts:
            assert np.all(np.abs((f - a) @ n) < 1e-6)


def test_quaternion_round_trip():
    rng = np.random.default_rng(3)
    for _ in range(100):
        M, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        if np.linalg.det(M) < 0:
            M[:, 0] = -M[:, 0]
        assert np.allclose(matrix_from_quat(quat_from_matrix(M)), M, atol=1e-9)


def pieces_polygon(pieces) -> list[Polygon]:
    return [Polygon(p) for _, p in pieces]


@pytest.mark.parametrize(
    "poly",
    [
        box(0, 0, 10, 4),
        Polygon([(0, 0), (12, 0), (12, 3), (5, 3), (5, 9), (0, 9)]),  # L shape
        Polygon([(0, 0), (10, 2), (8, 9), (-1, 6)]),  # general quad
        box(0, 0, 20, 20).difference(box(5, 5, 9, 12)),  # hole
        Point(0, 0).buffer(7, 16),  # curvy
        Polygon([(0, 0), (6, 0), (6, 2), (9, 2), (9, 0), (14, 0), (14, 5), (0, 5)]),  # notch
    ],
)
def test_decompose_is_an_exact_partition(poly):
    from shapely import affinity

    for rot in (0.0, 23.0):
        p = affinity.rotate(poly, rot, origin=(0, 0))
        pieces = decompose(p)
        polys = pieces_polygon(pieces)
        total = sum(q.area for q in polys)
        assert total == pytest.approx(p.area, rel=1e-6)
        union = unary_union(polys)
        assert union.symmetric_difference(p).area < 1e-6 * max(1.0, p.area)
        for kind, pts in pieces:
            if kind == "rect":
                e1, e2 = pts[1] - pts[0], pts[3] - pts[0]
                assert abs(e1 @ e2) < 1e-6 * np.linalg.norm(e1) * np.linalg.norm(e2)
            if kind == "rtri":
                e1, e2 = pts[1] - pts[0], pts[2] - pts[0]
                assert abs(e1 @ e2) < 1e-6 * np.linalg.norm(e1) * np.linalg.norm(e2)


def test_rectilinear_shapes_need_few_parts():
    from shapely import affinity

    L = Polygon([(0, 0), (12, 0), (12, 3), (5, 3), (5, 9), (0, 9)])
    for rot in (0.0, 37.0):
        pieces = decompose(affinity.rotate(L, rot, origin=(0, 0)))
        assert len(pieces) == 2 and all(k == "rect" for k, _ in pieces)
    gable_end = Polygon([(0, 0), (8, 0), (8, 5), (4, 8), (0, 5)])  # wall + gable
    pieces = decompose(gable_end, angles=[0.0])
    assert sorted(k for k, _ in pieces) == ["rect", "rtri", "rtri"]


def test_plate_on_sloped_plane_and_flat_roof():
    P = Parts(REGION)
    # A 35-degree roof plane rising to the north.
    slope = np.tan(np.radians(35))
    U = np.array([1.0, 0.0, 0.0])
    V = np.array([0.0, 1.0, slope]) / np.hypot(1.0, slope)
    origin = np.array([50.0, 50.0, 10.0])
    n = P.plate(box(0, 0, 8, 5), origin, U, V, 0.2, LOOK)
    assert n == 1 and P.rows[0][0] == SHAPE_BLOCK
    N = np.cross(U, V)
    face = outer_face(P, 0, P.Sdir(N))
    corners = P.S(np.array([origin, origin + 8 * U, origin + 8 * U + 5 * V, origin + 5 * V]))
    for c in corners:
        assert np.min(np.linalg.norm(face - c, axis=1)) < 1e-6
    Q = Parts(REGION)
    k = Q.flat(Polygon([(10, 10), (30, 10), (30, 20), (10, 20)]), 12.0, 0.3, LOOK)
    assert k == 1
    top = outer_face(Q, 0, np.array([0.0, 1.0, 0.0]))
    assert np.allclose(top[:, 1], (12.0 - (-12.0)) / 0.3)


def test_vertical_solid_query_matches_part_geometry():
    from geoblox.drivecheck import Solids

    rng = np.random.default_rng(5)
    for _ in range(30):
        P = Parts(REGION)
        a, b, c = rng.normal(size=(3, 3)) * 6
        n = np.cross(b - a, c - a)
        n /= np.linalg.norm(n)
        P.tri_s(a, b, c, n, 0.7, LOOK)
        Q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        if np.linalg.det(Q) < 0:
            Q[:, 0] = -Q[:, 0]
        P.box(rng.normal(size=3) * 4, Q[:, 0], Q[:, 1], Q[:, 2], rng.uniform(0.5, 4, 3), LOOK)
        entries = [(shape, center, R, size, "x") for shape, center, R, size, _, _ in P.rows]
        S = Solids(entries)
        # Brute force: dense point-in-solid test along a vertical line.
        for _ in range(10):
            x, z = rng.normal(size=2) * 4
            ivs = S.column(x, z)
            ys = np.linspace(-20, 20, 4001)
            inside = np.zeros_like(ys, dtype=bool)
            for shape, center, R, size, _ in entries:
                loc = (np.column_stack([np.full_like(ys, x), ys, np.full_like(ys, z)]) - center) @ R
                h = np.asarray(size) / 2
                ok = np.all(np.abs(loc) <= h + 1e-9, axis=1)
                if shape == SHAPE_WEDGE:
                    ok &= loc[:, 1] - size[1] / size[2] * loc[:, 2] <= 1e-9
                inside |= ok
            got = np.zeros_like(inside)
            for lo, hi, _ in ivs:
                got |= (ys >= lo - 1e-6) & (ys <= hi + 1e-6)
            assert np.mean(got != inside) < 0.003
