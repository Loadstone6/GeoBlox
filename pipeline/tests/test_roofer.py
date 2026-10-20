"""LoD2 roof partition on synthetic LiDAR roofs."""

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from geoblox import roofer
from geoblox.roofs import RoofSamples


def lidar(footprint: Polygon, fn, noise: float = 0.04, seed: int = 0) -> RoofSamples:
    """1 m first-return samples of a roof surface fn(x, y) inside a footprint."""
    rng = np.random.default_rng(seed)
    minx, miny, maxx, maxy = footprint.bounds
    e_min, n_max = np.floor(minx) - 2, np.ceil(maxy) + 2
    rows = int(n_max - np.floor(miny) + 2)
    cols = int(np.ceil(maxx) + 2 - e_min)
    rr, cc = np.mgrid[0:rows, 0:cols]
    x = e_min + cc.ravel() + 0.5
    y = n_max - rr.ravel() - 0.5
    inside = shapely.contains_xy(footprint.buffer(-0.45), x, y)
    x, y = x[inside], y[inside]
    z = fn(x, y) + rng.normal(0, noise, len(x))
    return RoofSamples(np.column_stack([x, y]), z, np.column_stack([rr.ravel()[inside], cc.ravel()[inside]]))


def check_partition(roof: roofer.Roof, footprint: Polygon):
    u = unary_union([f.poly for f in roof.faces])
    assert u.symmetric_difference(footprint).area < 0.02 * footprint.area
    total = sum(f.poly.area for f in roof.faces)
    assert total == pytest.approx(footprint.area, rel=0.02)


def surface_error(roof: roofer.Roof, fn, footprint: Polygon) -> float:
    g = np.array([[x, y] for x in np.arange(footprint.bounds[0] + 0.7, footprint.bounds[2] - 0.7, 0.5) for y in np.arange(footprint.bounds[1] + 0.7, footprint.bounds[3] - 0.7, 0.5)])
    g = g[shapely.contains_xy(footprint.buffer(-0.7), g[:, 0], g[:, 1])]
    return float(np.percentile(np.abs(roofer.height_at(roof, g) - fn(g[:, 0], g[:, 1])), 95))


def test_gable_roof_two_faces_meeting_on_the_ridge():
    fp = box(0, 0, 16, 10)
    fn = lambda x, y: 20.0 - 0.7 * np.abs(y - 5.0)  # ridge along x at y = 5
    roof = roofer.reconstruct(fp, lidar(fp, fn), 10.0, 1, 12)
    check_partition(roof, fp)
    assert len(roof.faces) == 2
    assert surface_error(roof, fn, fp) < 0.12
    # Faces meet at the ridge with equal heights (watertight).
    a, b = roof.faces
    edge = roofer.shared_edges(a.poly, b.poly)
    assert edge, "faces should share the ridge"
    for seg in edge:
        assert np.allclose(a.z(seg), b.z(seg), atol=0.05)
        assert np.allclose(seg[:, 1], 5.0, atol=0.25)


def test_hipped_roof_four_faces():
    fp = box(0, 0, 20, 10)

    def fn(x, y):
        return 18.0 - 0.6 * np.maximum(np.abs(y - 5.0), np.abs(x - 10.0) - 5.0)

    roof = roofer.reconstruct(fp, lidar(fp, fn, seed=3), 10.0, 3, 12)
    check_partition(roof, fp)
    assert len(roof.faces) == 4
    assert surface_error(roof, fn, fp) < 0.15


def test_stepped_flat_roofs_with_plant_room():
    fp = Polygon([(0, 0), (30, 0), (30, 12), (14, 12), (14, 24), (0, 24)])  # L shape

    def fn(x, y):
        z = np.where(x > 14.0, 16.0, 22.0)  # lower east wing
        plant = (x > 3) & (x < 8) & (y > 15) & (y < 20)
        return np.where(plant, 25.0, z)

    roof = roofer.reconstruct(fp, lidar(fp, fn, seed=5), 4.0, 5, 12)
    check_partition(roof, fp)
    levels = sorted(round(float(f.plane[2])) for f in roof.faces)
    assert all(f.flat for f in roof.faces)
    assert {16, 22, 25} <= set(levels)
    assert len(roof.faces) <= 5
    assert surface_error(roof, fn, fp) < 0.3
