"""Individual tree detection from LiDAR, fused with mapped OSM trees.

1. Canopy height model (CHM) = DSM − DTM, masked to vegetation: above 2.5 m,
   outside building footprints, and either rough (first-return surface is
   noisy over foliage but smooth over roofs and vehicles) or near a mapped tree.
2. Gaussian-smoothed CHM; tree tops are local maxima within a window whose
   radius grows with height (r = 1.5 + 0.06·h m, the usual crown-size
   allometry heuristic), above 4 m.
3. Marker-controlled watershed on −CHM partitions the canopy into crowns;
   crown radius = sqrt(area / π).
4. OSM trees snap to the nearest detected top within 4 m (taking its height
   and crown); unmatched OSM trees keep a height estimated from the CHM at
   their location; unmatched LiDAR tops become new trees.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.segmentation import watershed

from .config import PARAMS
from .scene import Scene


@dataclass
class Tree:
    x: float
    y: float
    ground: float
    height: float
    crown: float  # radius (m)
    kind: int  # 0 plane, 1 broadleaf, 2 conifer, 3 small/ornamental
    seed: int


def detect(scene: Scene, building_union, exclude=None) -> list[Tree]:
    dsm, dtm = scene.dsm.data, scene.dtm.data
    chm = np.clip(dsm - dtm, 0, 60)
    H, W = chm.shape
    rr, cc = np.mgrid[0:H, 0:W]
    ex, ny = scene.dsm.cell_center(rr.ravel(), cc.ravel())
    inside_bld = shapely.contains_xy(building_union.buffer(1.2), ex, ny).reshape(H, W) if not building_union.is_empty else np.zeros((H, W), bool)
    # Surface roughness: residual of the DSM against a 3x3 mean.
    resid = dsm - ndimage.uniform_filter(dsm, 3)
    rough = np.sqrt(np.maximum(ndimage.uniform_filter(resid * resid, 3), 0.0))
    osm_trees = [p for p in scene.osm.points if p.tags.get("natural") == "tree"]
    near_osm = np.zeros((H, W), dtype=bool)
    for p in osm_trees:
        x, y = p.geom.x, p.geom.y
        r, c = int(scene.dsm.n_max - y), int(x - scene.dsm.e_min)
        near_osm[max(0, r - 4) : r + 5, max(0, c - 4) : c + 5] = True
    veg = (chm > 2.5) & ~inside_bld & ((rough > 0.22) | near_osm)
    if exclude is not None and not exclude.is_empty:
        # Water (boats), bridge decks (buses) and carriageways (vehicles).
        veg &= ~shapely.contains_xy(exclude, ex, ny).reshape(H, W)
    veg = ndimage.binary_opening(veg, iterations=1)
    smooth = ndimage.gaussian_filter(np.where(veg, chm, 0), 1.0)
    # Height-adaptive local maxima: evaluate a few window sizes, pick per pixel.
    is_max = np.zeros_like(veg)
    for r_win, (lo, hi) in ((2, (0, 8)), (3, (8, 16)), (4, (16, 24)), (5, (24, 99))):
        mx = ndimage.maximum_filter(smooth, size=2 * r_win + 1)
        band = (smooth >= lo) & (smooth < hi)
        is_max |= band & (smooth == mx)
    tops = is_max & veg & (smooth > PARAMS.tree_min_height)
    markers, n = ndimage.label(tops)
    crowns = watershed(-smooth, markers, mask=veg)
    areas = ndimage.sum(np.ones_like(crowns), crowns, index=np.arange(1, n + 1))
    heights = ndimage.maximum(chm, crowns, index=np.arange(1, n + 1))
    centers = ndimage.center_of_mass(tops, markers, index=np.arange(1, n + 1))
    lidar = []
    for k in range(n):
        r, c = centers[k]
        x, y = scene.dsm.e_min + c + 0.5, scene.dsm.n_max - r - 0.5
        lidar.append((x, y, float(heights[k]), float(np.sqrt(max(areas[k], 1.0) / np.pi))))
    lidar = np.array(lidar) if lidar else np.zeros((0, 4))

    trees: list[Tree] = []
    used = np.zeros(len(lidar), dtype=bool)
    tree_idx = cKDTree(lidar[:, :2]) if len(lidar) else None
    clip = scene.clip
    for p in osm_trees:
        x, y = p.geom.x, p.geom.y
        if not clip.contains(p.geom):
            continue
        t = p.tags
        kind = 0
        genus = (t.get("genus", "") + t.get("species", "") + t.get("taxon", "")).lower()
        if t.get("leaf_type") == "needleleaved" or any(g in genus for g in ("pinus", "taxus", "cedrus", "picea")):
            kind = 2
        elif genus and "platanus" not in genus:
            kind = 1
        h, crown = None, None
        if tree_idx is not None:
            d, k = tree_idx.query([x, y], distance_upper_bound=4.0)
            if np.isfinite(d) and not used[k]:
                used[k] = True
                h, crown = lidar[k, 2], lidar[k, 3]
        if h is None:
            r, c = int(scene.dsm.n_max - y), int(x - scene.dsm.e_min)
            win = chm[max(0, r - 2) : r + 3, max(0, c - 2) : c + 3]
            h = float(win.max()) if win.size else 8.0
            if h < 3.0:  # recently planted / not in the survey
                h, kind = 5.0, 3
            crown = max(1.5, 0.28 * h)
        ground = float(scene.dtm.sample(np.array([x]), np.array([y]))[0])
        trees.append(Tree(x, y, ground, float(np.clip(h, 3.0, 40.0)), float(np.clip(crown, 1.2, 14.0)), kind, abs(hash((round(x, 1), round(y, 1)))) % 65536))
    for k in np.where(~used)[0]:
        x, y, h, crown = lidar[k]
        if not clip.contains(shapely.Point(x, y)):
            continue
        ground = float(scene.dtm.sample(np.array([x]), np.array([y]))[0])
        trees.append(Tree(float(x), float(y), ground, float(np.clip(h, 3.0, 40.0)), float(np.clip(crown, 1.2, 14.0)), 1, abs(hash((round(x, 1), round(y, 1), 7))) % 65536))
    return trees
