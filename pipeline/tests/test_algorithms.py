"""Unit tests for the reconstruction algorithms on synthetic data with known answers."""

import numpy as np
from shapely.geometry import Polygon, box

from geoblox import roofs, s3db
from geoblox.export import bng_to_studs, encode_chunk, pack, split_piece
from geoblox.ground import ray_cast
from geoblox.geomutil import parse_colour, parse_length, triangulate
from geoblox.mesh import Piece, crease_split, face_normals
from geoblox.config import WESTMINSTER


def gable_samples(w=12, d=20, eave=10.0, ridge=14.0, noise=0.03, seed=0):
    """A gable roof on a w x d footprint sampled at 1 m cell centres."""
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(0.5, w), np.arange(0.5, d))
    x, y = xs.ravel(), ys.ravel()
    z = ridge - (ridge - eave) * np.abs(x - w / 2) / (w / 2) + rng.normal(0, noise, x.size)
    rc = np.column_stack([(d - 0.5 - y).astype(int), (x - 0.5).astype(int)])
    return roofs.RoofSamples(np.column_stack([x, y]), z, rc)


def test_ransac_finds_both_gable_planes():
    s = gable_samples()
    labels, planes = roofs.ransac_planes(s.xy, s.z, s.rc, threshold=0.15, min_support=15, seed=1)
    assert len(planes) == 2
    slopes = sorted(abs(p[0]) for p in planes)
    # Rise 4 m over half-width 6 m -> |dz/dx| = 0.667
    assert np.allclose(slopes, [0.667, 0.667], atol=0.05)
    assert (labels >= 0).mean() > 0.95


def test_greedy_tin_meets_tolerance_with_few_vertices():
    w, d = 12, 20
    s = gable_samples(w, d, noise=0.0)
    labels, planes = roofs.ransac_planes(s.xy, s.z, s.rc, 0.15, 15, seed=1)
    grid = np.zeros((d, w))
    target = s.z.copy()
    ring = np.array([[0, 0], [w, 0], [w, d], [0, d]], dtype=float)
    ring_z = 14.0 - 4.0 * np.abs(ring[:, 0] - w / 2) / (w / 2)
    breaks = roofs.plane_breaklines(s, labels, planes)
    assert len(breaks) >= 2
    assert np.allclose(breaks[:, 0], 6.0, atol=0.05)  # ridge recovered at x = w/2
    tin = roofs.greedy_tin([ring], [ring_z], s, target, (0.0, float(d)), 0.1, 200, [], breaks)
    assert tin.max_error <= 0.1 + 1e-6
    # Corners + ridge points; no clouds of extra vertices hugging the ridge.
    assert len(tin.vertices) <= 4 + len(breaks) + 4
    from geoblox.buildings import Face, tin_height

    face = Face(box(0, 0, w, d), {})
    face.tin, face.median = tin, 12.0
    for y in (3.0, 10.0, 17.0):
        assert abs(tin_height(face, 6.0, y) - 14.0) < 0.05


def test_ray_cast_hits_nearest_kerb():
    segs = np.array([[[2.0, -5.0], [2.0, 5.0]], [[5.0, -5.0], [5.0, 5.0]]])
    d = ray_cast(np.array([[0.0, 0.0], [0.0, 0.0]]), np.array([[1.0, 0.0], [-1.0, 0.0]]), 10.0, segs)
    assert np.isclose(d[0], 2.0)
    assert np.isinf(d[1])


def test_triangulate_polygon_with_hole_is_ccw_and_covers_area():
    poly = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
    V, T = triangulate(poly)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    area = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    assert (area > 0).all()
    assert np.isclose(area.sum(), poly.area)


def test_s3db_pyramidal_roof_apex_and_gable_ends():
    poly = box(0, 0, 10, 10)
    f, shape, rh, base, *_ = s3db.roof_function(poly, {"roof:shape": "pyramidal", "roof:height": "5"}, 0.0, 20.0)
    assert shape == "pyramidal" and np.isclose(rh, 5.0)
    assert np.isclose(f(np.array([[5.0, 5.0]]))[0], 20.0)
    assert np.isclose(f(np.array([[0.0, 0.0]]))[0], 15.0)
    f2, *_ = s3db.roof_function(box(0, 0, 20, 8), {"roof:shape": "gabled", "roof:height": "3"}, 0.0, 10.0)
    assert np.isclose(f2(np.array([[10.0, 4.0]]))[0], 10.0)  # ridge
    assert np.isclose(f2(np.array([[10.0, 0.0]]))[0], 7.0)  # eave


def test_crease_split_keeps_ridge_sharp():
    v = np.array([[0, 0, 0], [2, 0, 1], [2, 2, 1], [0, 2, 0], [4, 0, 0], [4, 2, 0]], dtype=float)
    t = np.array([[0, 1, 2], [0, 2, 3], [1, 4, 5], [1, 5, 2]])
    nv, nt, nn, src = crease_split(v, t, 25.0)
    assert len(nv) == 8  # the two ridge vertices are duplicated
    fn = face_normals(nv, nt)
    for f in range(len(nt)):
        for k in range(3):
            assert np.dot(nn[nt[f, k]], fn[f]) > 0.99


def test_split_piece_respects_editablemesh_caps():
    n = 30000
    v = np.random.default_rng(0).random((n * 3, 3))
    t = np.arange(n * 3).reshape(n, 3)
    p = Piece(v, t, np.tile([0, 0, 1.0], (len(v), 1)), np.ones((len(v), 3)), np.zeros((len(v), 2)))
    parts = split_piece(p)
    assert sum(len(q.t) for q in parts) == n
    assert all(len(q.t) <= 19000 and len(q.v) <= 55000 for q in parts)


def test_chunk_encoding_roundtrip_header():
    v = np.array([[530200.0, 179850.0, 5.0], [530201.0, 179850.0, 5.0], [530200.0, 179851.0, 5.0]])
    p = Piece(v, np.array([[0, 1, 2]]), np.tile([0, 0, 1.0], (3, 1)), np.ones((3, 3)) * 0.5, np.zeros((3, 2)))
    anchor = bng_to_studs(WESTMINSTER, np.array([[530200.0, 179850.0, 0.0]]))[0]
    blob = encode_chunk(WESTMINSTER, anchor, [(3, 0, p)], [], [], [])
    assert blob[:4] == b"GBM2"
    assert len(blob) == 26 + 6 + 3 * 16 + 6
    assert len(pack(blob)) > 0


def test_parsers():
    assert np.allclose(parse_colour("#ddbb55"), [0xDD / 255, 0xBB / 255, 0x55 / 255])
    assert parse_colour("white") is not None and parse_colour("not-a-colour") is None
    assert parse_length("96") == 96.0 and np.isclose(parse_length("10 ft"), 3.048)
