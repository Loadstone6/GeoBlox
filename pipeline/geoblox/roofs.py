"""Roof plane detection on airborne LiDAR (used by roofer.py).

For one footprint:

1. Sample the first-return DSM at cell centres inside it, eroded by ~0.4 m
   so wall and ground mixed pixels are excluded.
2. Sequential RANSAC (Fischler & Bolles 1981) extracts roof planes
   z = a·x + b·y + c with an orthogonal-distance threshold. Each plane's
   inliers are split into 4-connected components on the raster grid, so two
   coplanar but separate facets do not merge. Planes are refit by least
   squares.
3. Unexplained samples join a nearby facet when they are within its
   tolerance band (rooftop clutter below a parapet is not structure).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree


@dataclass
class RoofSamples:
    xy: np.ndarray  # (N, 2) BNG
    z: np.ndarray  # (N,) raw DSM
    rc: np.ndarray  # (N, 2) int raster coordinates (row, col)


def ransac_planes(
    xy: np.ndarray,
    z: np.ndarray,
    rc: np.ndarray,
    threshold: float,
    min_support: int,
    max_planes: int = 10,
    seed: int = 0,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Return (labels, planes): labels[i] = facet index or -1; planes[k] = (a, b, c)."""
    n = len(z)
    labels = np.full(n, -1, dtype=np.int32)
    planes: list[np.ndarray] = []
    if n < min_support:
        return labels, planes
    rng = np.random.default_rng(seed)
    remaining = np.arange(n)
    A_all = np.column_stack([xy[:, 0], xy[:, 1], np.ones(n)])
    max_slope = 2.75  # ~70 degrees: steeper "planes" are walls or noise
    r0, c0 = rc.min(axis=0)
    shape = tuple(rc.max(axis=0) - rc.min(axis=0) + 1)

    for _ in range(max_planes * 3):
        if len(remaining) < min_support or len(planes) >= max_planes:
            break
        best_inl: np.ndarray | None = None
        best_count = 0
        iters, it = 400, 0
        pts = remaining
        while it < iters:
            it += 1
            pick = pts[rng.choice(len(pts), 3, replace=False)]
            A = A_all[pick]
            if abs(np.linalg.det(A)) < 1e-6:
                continue
            a, b, c = np.linalg.solve(A, z[pick])
            if a * a + b * b > max_slope * max_slope:
                continue
            resid = np.abs(A_all[pts] @ np.array([a, b, c]) - z[pts]) / np.sqrt(1 + a * a + b * b)
            inl = pts[resid < threshold]
            if len(inl) > best_count:
                best_count = len(inl)
                best_inl = inl
                # Adaptive iteration count for 99.5% confidence.
                w = best_count / len(pts)
                if w > 0:
                    iters = min(400, int(np.log(0.005) / np.log(max(1e-9, 1 - w**3))) + 1)
        if best_inl is None or best_count < min_support:
            break
        # Split the consensus set into spatially connected facets.
        img = np.zeros(shape, dtype=bool)
        img[rc[best_inl, 0] - r0, rc[best_inl, 1] - c0] = True
        comp, ncomp = ndimage.label(img)
        comp_of = comp[rc[best_inl, 0] - r0, rc[best_inl, 1] - c0]
        accepted = False
        for k in range(1, ncomp + 1):
            members = best_inl[comp_of == k]
            if len(members) < min_support:
                continue
            A = A_all[members]
            sol, *_ = np.linalg.lstsq(A, z[members], rcond=None)
            planes.append(sol)
            labels[members] = len(planes) - 1
            accepted = True
        removed = np.isin(remaining, best_inl)
        remaining = remaining[~removed]
        if not accepted:
            continue
    return labels, planes


def assign_residuals(samples: RoofSamples, labels: np.ndarray, planes: list[np.ndarray]) -> np.ndarray:
    """Attach unexplained samples to a nearby facet when they are within its
    tolerance band: 0.6 m for pitched planes, 1.5 m for near-flat roofs (rooftop
    plant and parapets below that height are clutter, not structure). Only
    facets with inliers within 3 m qualify, so separate roof levels never merge."""
    labels = labels.copy()
    free = np.where(labels < 0)[0]
    if len(free) == 0 or not planes:
        return labels
    best_d = np.full(len(free), np.inf)
    best_k = np.full(len(free), -1)
    pts = samples.xy[free]
    for k, p in enumerate(planes):
        members = np.where(labels == k)[0]
        if len(members) == 0:
            continue
        near, _ = cKDTree(samples.xy[members]).query(pts, distance_upper_bound=3.0)
        flat = p[0] * p[0] + p[1] * p[1] < 0.01
        tol = 1.5 if flat else 0.6
        d = np.abs(pts @ p[:2] + p[2] - samples.z[free])
        ok = np.isfinite(near) & (d < tol) & (d < best_d)
        best_d[ok] = d[ok]
        best_k[ok] = k
    hit = best_k >= 0
    labels[free[hit]] = best_k[hit]
    return labels
