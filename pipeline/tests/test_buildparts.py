"""Buildings mapped to parts: facades, LoD2 roofs and mapped landmarks."""

import numpy as np
from shapely.geometry import box

from geoblox import buildparts as bp
from geoblox import roofer
from geoblox.buildings import STYLES, Building
from geoblox.config import Region
from geoblox.partmap import SHAPE_BLOCK, Parts, part_triangles

REGION = Region("test", (0.0, 0.0, 100.0, 100.0))
MPS = 0.3


class OpenStreet:
    """Exposure stub: every wall faces the street."""

    def cover(self, key, a, c, n_out):
        return None


def bng(P: Parts, row):
    shape, center, R, size, look, extra = row
    v, _ = part_triangles(shape, center, R, size, extra)
    e0, n0 = REGION.origin
    return np.column_stack([v[:, 0] * MPS + e0, n0 - v[:, 2] * MPS, v[:, 1] * MPS - 12.0])


def test_facade_module_part_budget_and_extent():
    P = Parts(REGION)
    lk = bp.Looks(STYLES["portland"], 1234)
    a, c = np.array([10.0, 10.0]), np.array([30.0, 10.0])  # 20 m facing south (interior to the north)
    bp.facade(P, a, c, 5.0, 20.0, 5.0, lk, retail=False, windows=True)
    st = STYLES["portland"]
    bays = round(20.0 / st.bay)
    floors = 3  # windows fitting under 20 m with a 0.5 m top band
    glass = [r for r in P.rows if r[4].tag == "GeoBloxWindow"]
    assert 1 <= len(glass) <= floors
    assert len(P) <= bays + 1 + (floors + 1) + len(glass) + 2
    for row in P.rows:
        v = bng(P, row)
        assert v[:, 0].min() > 9.9 and v[:, 0].max() < 30.1
        assert v[:, 1].min() > 10.0 - 0.21 - 1e-6  # nothing stands proud of the wall more than the cornice
        assert v[:, 1].max() < 10.0 + 0.45 + 1e-6  # and nothing reaches deeper than the wall
        assert v[:, 2].min() > 5.0 - 1e-6 and v[:, 2].max() < 20.0 + 1e-6


def test_lidar_building_gable_roof_is_watertight_and_on_the_planes():
    fp = box(0, 0, 16, 10)
    fn = lambda x, y: 20.0 - 0.7 * np.abs(y - 5.0)
    xs, ys = np.meshgrid(np.arange(0.5, 16), np.arange(0.5, 10))
    x, y = xs.ravel(), ys.ravel()
    rc = np.column_stack([(9.5 - y).astype(int), (x - 0.5).astype(int)])
    samples = roofer.RoofSamples(np.column_stack([x, y]), fn(x, y), rc)
    b = Building(1, {"building": "house"}, fp, [])
    b.style, b.ground, b.footprint = "stock", 10.0, fp
    b.roof = roofer.reconstruct(fp, samples, 10.0, 1, 12)
    assert len(b.roof.faces) == 2
    P = Parts(REGION)
    bp.emit_lidar_building(P, b, OpenStreet())
    roof_rows = [r for r in P.rows if r[4].material in ("Slate", "Brick", "Metal") and r[4].collide]
    assert roof_rows
    # The two roof planes are single boxes (rectangles in their own frames).
    assert sum(1 for r in roof_rows if r[0] == SHAPE_BLOCK) >= 2
    top = max(bng(P, r)[:, 2].max() for r in roof_rows)
    assert abs(top - 20.0) < 0.1
    # Gable ends close the walls up to the ridge.
    walls = [r for r in P.rows if r[4].material == "Brick" and not r[4].collide]
    assert max(bng(P, r)[:, 2].max() for r in walls) > 19.5


def test_s3db_pyramid_and_dome():
    P = Parts(REGION)
    b = Building(2, {"building": "yes"}, box(0, 0, 10, 10), [(box(0, 0, 10, 10), {"building:part": "yes", "height": "20", "roof:shape": "pyramidal", "roof:height": "5"})])
    b.style, b.ground, b.footprint = "portland", 0.0, b.outline
    bp.emit_s3db_building(P, b, None)
    tops = [bng(P, r)[:, 2].max() for r in P.rows]
    assert abs(max(tops) - 20.0) < 0.05
    Q = Parts(REGION)
    d = Building(3, {"building": "yes"}, box(0, 0, 10, 10), [(box(0, 0, 10, 10), {"building:part": "yes", "height": "15", "roof:shape": "dome", "roof:height": "5"})])
    d.style, d.ground, d.footprint = "portland", 0.0, d.outline
    bp.emit_s3db_building(Q, d, None)
    assert any(r[5] == 1 for r in Q.rows)  # ellipsoid (sphere mesh) for the dome


def test_regularize_snaps_noisy_orthogonal_footprint():
    from shapely import affinity
    from shapely.geometry import Polygon

    p = affinity.rotate(Polygon([(0, 0), (20, 0.15), (20.1, 10), (12, 10.05), (12, 14), (0.1, 14.1)]), 33, origin=(0, 0))
    r = bp.regularize(p)
    c = np.asarray(r.exterior.coords)
    d = np.diff(c, axis=0)
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90
    assert np.ptp(ang) < 0.01
    assert abs(r.area - p.area) < 0.03 * p.area
