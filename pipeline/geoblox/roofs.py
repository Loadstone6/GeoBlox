"""LiDAR roof reconstruction.

For one roof face (a planar polygon cell of a building footprint):

1. Sample the first-return DSM at cell centres inside the face, eroded by
   ~0.4 m so wall/ground mixed pixels are excluded.
2. Sequential RANSAC (Fischler & Bolles 1981) extracts roof planes
   z = a·x + b·y + c with an orthogonal-distance threshold. Each plane's inliers
   are split into 4-connected components on the raster grid, so two coplanar
   but separate facets do not merge. Planes are refit by least squares.
3. Every sample gets a "snapped" height: its facet plane if it has one,
   otherwise a 3×3 median of the DSM (domes, spires, plant).
4. Greedy-insertion TIN (Garland & Heckbert 1995, "Fast Polygonal
   Approximation of Terrains and Height Fields"): start from a constrained
   Delaunay triangulation (Shewchuk's Triangle) of the face boundary, then
   repeatedly insert, per triangle, the sample with the largest vertical error
   until every sample is within ε or the vertex budget is spent.

The result is a 2.5D roof surface that is exact on planar facets and
error-bounded everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba
import numpy as np
import triangle
from scipy import ndimage
from scipy.spatial import cKDTree


@dataclass
class RoofSamples:
    xy: np.ndarray  # (N, 2) BNG
    z: np.ndarray  # (N,) raw DSM
    rc: np.ndarray  # (N, 2) int raster coordinates (row, col)


@dataclass
class Tin:
    vertices: np.ndarray  # (K, 3)
    triangles: np.ndarray  # (L, 3) int
    n_boundary: int  # first n vertices are the (fixed) boundary vertices
    max_error: float
    planes: int
    ring_map: np.ndarray | None = None  # concatenated ring vertex -> TIN vertex index


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


def plane_breaklines(samples: RoofSamples, labels: np.ndarray, planes: list[np.ndarray], spacing: float = 2.0) -> np.ndarray:
    """Ridge / hip / valley lines from intersections of adjacent roof planes.

    For each pair of facets whose inlier regions touch on the raster grid,
    the intersection of their planes is a line in plan:
        (a_i - a_j) x + (b_i - b_j) y + (c_i - c_j) = 0.
    It is kept only if the facets' shared boundary actually lies on it (the
    planes meet, rather than step); its extent is the projection of that
    boundary. Points sampled along it carry the exact plane height, so the TIN
    gets crease edges exactly where the roof folds. Returns (k, 3) points.
    """
    if len(planes) < 2:
        return np.zeros((0, 3))
    rc = samples.rc
    r0, c0 = rc.min(axis=0)
    h, w = rc.max(axis=0) - rc.min(axis=0) + 1
    img = np.full((h, w), -1, dtype=np.int32)
    img[rc[:, 0] - r0, rc[:, 1] - c0] = labels
    pairs: dict[tuple[int, int], list[np.ndarray]] = {}
    for dr, dc in ((0, 1), (1, 0)):
        a = img[: h - dr, : w - dc]
        b = img[dr:, dc:]
        m = (a >= 0) & (b >= 0) & (a != b)
        ii, jj = np.nonzero(m)
        for r, c in zip(ii, jj):
            la, lb = int(a[r, c]), int(b[r, c])
            key = (min(la, lb), max(la, lb))
            # Midpoint between the two cell centres, in world coordinates.
            gr, gc = r + r0 + dr / 2, c + c0 + dc / 2
            pairs.setdefault(key, []).append(np.array([gc, gr]))
    out = []
    # Convert grid (col, row) to world via any sample (1 m cells, north-up).
    ref_xy, ref_rc = samples.xy[0], rc[0]
    for (i, j), pts in pairs.items():
        if len(pts) < 4:
            continue
        pi, pj = planes[i], planes[j]
        nx, ny, k = pi[0] - pj[0], pi[1] - pj[1], pi[2] - pj[2]
        nn = np.hypot(nx, ny)
        if nn < 0.08:
            continue  # near-parallel facets: a step, not a crease
        grid = np.array(pts)
        world = np.column_stack([ref_xy[0] + (grid[:, 0] - ref_rc[1]), ref_xy[1] - (grid[:, 1] - ref_rc[0])])
        dist = np.abs(world[:, 0] * nx + world[:, 1] * ny + k) / nn
        if np.median(dist) > 1.0:
            continue
        d = np.array([-ny, nx]) / nn
        p0 = -k * np.array([nx, ny]) / (nn * nn)
        t = (world - p0) @ d
        t0, t1 = t.min() - 0.5, t.max() + 0.5
        n = max(2, int(np.ceil((t1 - t0) / spacing)) + 1)
        ts = np.linspace(t0, t1, n)
        xy = p0 + np.outer(ts, d)
        z = xy[:, 0] * pi[0] + xy[:, 1] * pi[1] + pi[2]
        out.append(np.column_stack([xy, z]))
    return np.vstack(out) if out else np.zeros((0, 3))


@numba.njit(cache=True)
def _interp_tin(V, T, gx0, gy0, res, idx_img, out_z, out_t):
    """Rasterise triangles over the sample grid; barycentric z at each sample."""
    h, w = idx_img.shape
    for t in range(T.shape[0]):
        i0, i1, i2 = T[t, 0], T[t, 1], T[t, 2]
        x0, y0, z0 = V[i0, 0], V[i0, 1], V[i0, 2]
        x1, y1, z1 = V[i1, 0], V[i1, 1], V[i1, 2]
        x2, y2, z2 = V[i2, 0], V[i2, 1], V[i2, 2]
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-12:
            continue
        xmin = min(x0, min(x1, x2))
        xmax = max(x0, max(x1, x2))
        ymin = min(y0, min(y1, y2))
        ymax = max(y0, max(y1, y2))
        c_lo = max(0, int(np.floor((xmin - gx0) / res - 0.5)))
        c_hi = min(w - 1, int(np.ceil((xmax - gx0) / res - 0.5)))
        r_lo = max(0, int(np.floor((gy0 - ymax) / res - 0.5)))
        r_hi = min(h - 1, int(np.ceil((gy0 - ymin) / res - 0.5)))
        for r in range(r_lo, r_hi + 1):
            py = gy0 - (r + 0.5) * res
            for c in range(c_lo, c_hi + 1):
                s = idx_img[r, c]
                if s < 0:
                    continue
                px = gx0 + (c + 0.5) * res
                l0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / den
                l1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / den
                l2 = 1.0 - l0 - l1
                if l0 >= -1e-9 and l1 >= -1e-9 and l2 >= -1e-9:
                    out_z[s] = l0 * z0 + l1 * z1 + l2 * z2
                    out_t[s] = t


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


def snapped_heights(samples: RoofSamples, dsm: np.ndarray, labels: np.ndarray, planes: list[np.ndarray]) -> np.ndarray:
    z = samples.z.copy()
    # Median-filtered DSM for unexplained samples (robust to single noisy returns).
    r, c = samples.rc[:, 0], samples.rc[:, 1]
    win = np.stack(
        [dsm[np.clip(r + dr, 0, dsm.shape[0] - 1), np.clip(c + dc, 0, dsm.shape[1] - 1)] for dr in (-1, 0, 1) for dc in (-1, 0, 1)],
        axis=1,
    )
    med = np.nanmedian(win, axis=1)
    z = np.where(np.isnan(med), z, med)
    for k, p in enumerate(planes):
        m = labels == k
        z[m] = samples.xy[m, 0] * p[0] + samples.xy[m, 1] * p[1] + p[2]
    return z


def greedy_tin(
    rings: list[np.ndarray],
    ring_z: list[np.ndarray],
    samples: RoofSamples,
    target_z: np.ndarray,
    grid_origin: tuple[float, float],
    eps,
    max_vertices: int,
    hole_points: list[tuple[float, float]],
    fixed: np.ndarray | None = None,
) -> Tin:
    """Constrained greedy-insertion TIN over a polygon with holes.

    `fixed` are interior vertices inserted up-front (plane-intersection
    breaklines), so ridges and valleys are exact before refinement starts."""
    # Boundary vertices + segments (rings are open: last != first).
    bverts, bz, segs = [], [], []
    off = 0
    for ring, rz in zip(rings, ring_z):
        m = len(ring)
        bverts.append(ring)
        bz.append(rz)
        segs.extend([(off + i, off + (i + 1) % m) for i in range(m)])
        off += m
    V2 = np.vstack(bverts).astype(np.float64)
    Vz = np.concatenate(bz).astype(np.float64)
    # Triangle crashes on duplicate vertices / degenerate segments (rings that
    # touch at a vertex, consecutive duplicates). Weld vertices within 1 mm.
    key = np.round(V2 * 1000).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.ravel()
    order = np.argsort(first)  # unique ids in order of first appearance
    new_id = np.empty(len(first), dtype=np.int64)
    new_id[order] = np.arange(len(first))
    remap = new_id[inverse]  # input ring vertex -> welded vertex
    zsum = np.zeros(len(first))
    zcnt = np.zeros(len(first))
    np.add.at(zsum, remap, Vz)
    np.add.at(zcnt, remap, 1.0)
    V2 = V2[first[order]]
    Vz = zsum / np.maximum(zcnt, 1.0)
    ring_map = remap.copy()
    clean = set()
    for s0, s1 in segs:
        a, b = int(remap[s0]), int(remap[s1])
        if a != b:
            clean.add((min(a, b), max(a, b)))
    segs_a = np.array(sorted(clean), dtype=np.int32).reshape(-1, 2)
    n_boundary = len(V2)
    boundary_tree = cKDTree(V2) if len(V2) else None
    if fixed is not None and len(fixed):
        import shapely
        from shapely.geometry import Polygon as _Poly

        domain = _Poly(rings[0], rings[1:]).buffer(-0.35)
        if not domain.is_empty:
            f = np.asarray(fixed, dtype=np.float64)
            keep = shapely.contains_xy(domain, f[:, 0], f[:, 1])
            f = f[keep]
            if len(f):
                _, uniq = np.unique(np.round(f[:, :2] / 0.3).astype(np.int64), axis=0, return_index=True)
                f = f[np.sort(uniq)]
                V2 = np.vstack([V2, f[:, :2]])
                Vz = np.concatenate([Vz, f[:, 2]])
    boundary_dist = None
    if len(samples.xy) and len(segs_a):
        import shapely

        lines = shapely.linestrings([[V2[a], V2[b]] for a, b in segs_a])
        boundary_dist = shapely.distance(shapely.multilinestrings(lines), shapely.points(samples.xy))

    # Sample grid index image for fast barycentric evaluation.
    gx0, gy0 = grid_origin
    rc = samples.rc
    r0, c0 = rc.min(axis=0) if len(rc) else (0, 0)
    h = int(rc[:, 0].max() - r0 + 1) if len(rc) else 1
    w = int(rc[:, 1].max() - c0 + 1) if len(rc) else 1
    idx_img = np.full((h, w), -1, dtype=np.int64)
    if len(rc):
        idx_img[rc[:, 0] - r0, rc[:, 1] - c0] = np.arange(len(rc))
    # Window origin in world coords (top-left corner of cell (r0, c0)).
    wx0 = gx0 + c0
    wy0 = gy0 - r0

    inserted = np.zeros(len(target_z), dtype=bool)
    interior: list[int] = []
    last_err = 0.0
    tri_out = None
    for _ in range(64):
        pts = V2 if not interior else np.vstack([V2, samples.xy[interior]])
        zz = Vz if not interior else np.concatenate([Vz, target_z[interior]])
        tri_in = {"vertices": pts, "segments": segs_a}
        if hole_points:
            tri_in["holes"] = np.array(hole_points, dtype=np.float64)
        tri_out = triangle.triangulate(tri_in, "pQ")
        T = tri_out["triangles"].astype(np.int64)
        if len(tri_out["vertices"]) != len(pts):
            # Triangle added Steiner points (degenerate input): keep its vertices,
            # interpolating heights from the nearest known vertex.
            extra = tri_out["vertices"][len(pts) :]
            d = ((extra[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
            zz = np.concatenate([zz, zz[np.argmin(d, axis=1)]])
            pts = tri_out["vertices"]
        V = np.column_stack([pts, zz])
        if len(target_z) == 0:
            break
        out_z = np.full(len(target_z), np.nan)
        out_t = np.full(len(target_z), -1, dtype=np.int64)
        _interp_tin(V, T, wx0, wy0, 1.0, idx_img, out_z, out_t)
        err = np.abs(np.nan_to_num(out_z, nan=1e9) - target_z)
        err[inserted] = 0.0
        err[out_t < 0] = 0.0
        last_err = float(err.max()) if len(err) else 0.0
        excess = err - eps  # eps may be per-sample (tight on planes, loose on clutter)
        if excess.max() <= 0 or len(pts) >= max_vertices:
            break
        # Worst sample per triangle above tolerance (classic GH parallel insertion).
        cand = np.where(excess > 0)[0]
        order = cand[np.argsort(-excess[cand])]
        seen = set()
        new = []
        for s in order:
            t = out_t[s]
            if t in seen:
                continue
            # Never insert on (or numerically on) the outline: degenerate for Triangle.
            if boundary_tree is not None and boundary_tree.query(samples.xy[s])[0] < 0.05:
                continue
            if boundary_dist is not None and boundary_dist[s] < 0.08:
                continue
            seen.add(t)
            new.append(s)
            if len(pts) + len(new) >= max_vertices:
                break
        if not new:
            break
        interior.extend(new)
        inserted[new] = True

    verts = np.column_stack([tri_out["vertices"], zz]) if tri_out is not None else np.zeros((0, 3))
    tris = tri_out["triangles"].astype(np.int64) if tri_out is not None else np.zeros((0, 3), dtype=np.int64)
    return Tin(verts, tris, n_boundary, last_err, 0, ring_map)
