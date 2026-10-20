"""Unit tests for the reconstruction algorithms on synthetic data with known answers."""

import numpy as np
from shapely.geometry import box

from geoblox import roofs, s3db
from geoblox.export import encode_terrain, pack
from geoblox.geomutil import parse_colour, parse_length, triangulate
from geoblox.ground import ray_cast


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


def test_terrain_blob_layout_and_precision():
    h = np.full((64, 64), 5.0)
    h[3, 7] = 5.004
    w = np.full((64, 64), np.nan)
    w[0, 0] = 3.25
    blob = encode_terrain(-128, 256, h, np.ones((64, 64), dtype=np.uint8), w)
    assert blob[:4] == b"GBT2" and len(blob) == 18 + 64 * 64 * 5
    rec = np.frombuffer(blob[18:], dtype=[("h", "<u2"), ("m", "u1"), ("w", "<u2")])
    # 1/32 stud quantum: 9 mm in the real world.
    assert abs(rec["h"][3 * 64 + 7] / 32 * 0.3 - 12.0 - 5.004) < 0.005
    assert rec["w"][0] > 0 and rec["w"][1] == 0
    assert len(pack(blob)) > 0


def test_parsers():
    assert np.allclose(parse_colour("#ddbb55"), [0xDD / 255, 0xBB / 255, 0x55 / 255])
    assert parse_colour("white") is not None and parse_colour("not-a-colour") is None
    assert parse_length("96") == 96.0 and np.isclose(parse_length("10 ft"), 3.048)
