"""Landmark parameters measured from the data (geometry is built at runtime).

London Eye: the wheel's plane is found by principal component analysis of
the LiDAR returns more than 25 m above ground near the mapped footprint (the
rim and spokes form a thin vertical disc, so the first principal axis of their
plan positions is the wheel's horizontal direction). Hub height and radius
follow from the highest return and the 120 m rim diameter.

Elizabeth Tower: the four clock dials are the white, round building:parts at
~52-60 m; their centroids and outward directions drive live clock hands.
"""

from __future__ import annotations

import numpy as np

from .config import PARAMS
from .export import bng_to_studs
from .scene import Scene


def london_eye(scene: Scene) -> dict | None:
    feat = next((f for f in scene.osm.areas if f.tags.get("attraction") == "big_wheel" or f.tags.get("name") == "London Eye"), None)
    if feat is None:
        return None
    c = np.array(feat.geom.centroid.coords[0])
    dsm, dtm = scene.dsm, scene.dtm
    r0, c0 = int(dsm.n_max - c[1]), int(c[0] - dsm.e_min)
    R = 80
    win = (slice(max(0, r0 - R), r0 + R), slice(max(0, c0 - R), c0 + R))
    nd = dsm.data[win] - dtm.data[win]
    rr, cc = np.nonzero(nd > 45)  # only the wheel rises this high (County Hall is ~30 m)
    if len(rr) < 20:
        return None
    xs = dsm.e_min + cc + win[1].start + 0.5
    ys = dsm.n_max - (rr + win[0].start) - 0.5
    pts = np.column_stack([xs, ys])
    mean = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - mean, full_matrices=False)
    axis = vt[0] / np.linalg.norm(vt[0])
    top = float(np.nanmax(dsm.data[win]))
    ground = float(np.median(dtm.data[win]))
    radius = 60.0
    hub_z = top - radius - 2.0
    # The wheel's centre in plan is the mean of the elevated returns.
    hub = bng_to_studs(scene.region, np.array([[mean[0], mean[1], hub_z]]))[0]
    base = bng_to_studs(scene.region, np.array([[mean[0], mean[1], ground]]))[0]
    plane_dir = np.array([axis[0], 0.0, -axis[1]])
    # The axle is horizontal and perpendicular to the wheel plane.
    axle = np.array([-plane_dir[2], 0.0, plane_dir[0]])
    # Support legs lean from the landward side (away from the river).
    river_side = 1.0
    if scene.water_level is not None:
        probe = mean + np.array([-axis[1], axis[0]]) * 30
        zp = float(dtm.sample(np.array([probe[0]]), np.array([probe[1]]))[0])
        river_side = -1.0 if zp < scene.water_level + 0.5 else 1.0
    return {
        "hub": hub.tolist(),
        "base_y": float(base[1]),
        "radius": radius / PARAMS.metres_per_stud,
        "plane": plane_dir.tolist(),
        "axle": (axle * river_side).tolist(),
        "capsules": 32,
        "measured_top_m": round(top, 2),
        "rim_returns": int(len(rr)),
    }


def big_ben_dials(scene: Scene) -> list[dict]:
    tower = next((f for f in scene.osm.areas if f.tags.get("name") == "Elizabeth Tower" and "building" in f.tags), None)
    if tower is None:
        return []
    center = np.array(tower.geom.centroid.coords[0])
    ground = float(np.percentile(scene.dtm.sample(*np.array(tower.geom.exterior.coords).T), 25))
    dials = []
    for f in scene.osm.areas:
        t = f.tags
        if "building:part" not in t or t.get("building:colour") != "white" or t.get("roof:shape") != "round":
            continue
        if f.geom.distance(tower.geom) > 2.0:
            continue
        pc = np.array(f.geom.centroid.coords[0])
        out = pc - center
        if np.linalg.norm(out) < 1e-3:
            continue
        out /= np.linalg.norm(out)
        try:
            h0, h1 = float(t.get("min_height", 0)), float(t.get("height", 0))
        except ValueError:
            continue
        cz = ground + (h0 + h1) / 2
        p = bng_to_studs(scene.region, np.array([[pc[0] + out[0] * 0.9, pc[1] + out[1] * 0.9, cz]]))[0]
        dials.append({"center": p.tolist(), "normal": [float(out[0]), 0.0, float(-out[1])], "radius": 3.5 / PARAMS.metres_per_stud})
    return dials


def mask_eye(scene: Scene) -> int:
    """Remove the wheel's rim/spoke/leg returns from the DSM (they would
    otherwise be reconstructed as a building or detected as trees).
    Returns the number of masked cells."""
    feat = next((f for f in scene.osm.areas if f.tags.get("attraction") == "big_wheel" or f.tags.get("name") == "London Eye"), None)
    if feat is None:
        return 0
    c = np.array(feat.geom.centroid.coords[0])
    dsm, dtm = scene.dsm, scene.dtm
    r0, c0 = int(dsm.n_max - c[1]), int(c[0] - dsm.e_min)
    R = 85
    win = (slice(max(0, r0 - R), r0 + R), slice(max(0, c0 - R), c0 + R))
    nd = dsm.data[win] - dtm.data[win]
    rr, cc = np.nonzero(nd > 45)  # only the wheel rises this high (County Hall is ~30 m)
    if len(rr) < 20:
        return 0
    pts = np.column_stack([cc, rr]).astype(float)
    mean = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - mean, full_matrices=False)
    axis = vt[0] / np.linalg.norm(vt[0])
    perp = np.array([-axis[1], axis[0]])
    gr, gc = np.mgrid[win[0], win[1]]
    rel = np.stack([gc - win[1].start, gr - win[0].start], axis=-1) - mean
    along = rel @ axis
    across = rel @ perp
    band = (np.abs(across) < 5.0) & (np.abs(along) < 68)
    legs = (np.abs(across) < 40.0) & (np.abs(along) < 18) & (nd > 16)
    m = (band & (nd > 3.5)) | legs
    sub = dsm.data[win]
    sub[m] = dtm.data[win][m] + 0.5
    return int(m.sum())
