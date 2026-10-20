"""LoD2 roof reconstruction by footprint partitioning, in the style of
3DBAG's roofer (Peters et al. 2022, "Automated 3D reconstruction of LoD2 and
LoD1 models for all 10 million buildings of the Netherlands").

The greedy TIN in roofs.py reproduces the LiDAR surface faithfully, but every
building comes out as hundreds of triangles. Mapped to Roblox parts that is
thousands of wedges, and the noise shows. Here each roof is a small set of
planar polygons instead:

1. Roof planes: sequential RANSAC on the first-return DSM inside the
   footprint (roofs.ransac_planes). Near-flat planes snap to horizontal, and
   near-duplicate planes merge.
2. Candidate lines:
   * intersection lines of planes whose inlier regions touch (ridges, hips,
     valleys), kept only if the shared boundary lies on them;
   * step lines where touching regions are at different heights, fitted to
     the boundary pixels and snapped to the footprint's dominant directions;
   * mapped building:part outlines, when present.
3. Arrangement: the lines cut the footprint into cells (shapely.polygonize).
4. Labelling: each cell takes a plane by minimising a Potts energy (iterated
   conditional modes). The data term is the cell area times the mean truncated
   residual of its LiDAR samples. The smoothness term is the shared boundary
   length where neighbours disagree. Zebedin et al. (2008) and roofer use
   graph cuts for the same energy.
5. Cells with the same label merge into roof faces. Where neighbouring faces
   meet at different heights, the caller builds step walls.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .roofs import RoofSamples, assign_residuals, ransac_planes

FLAT_SLOPE = np.tan(np.radians(5.0))
TRUNC = 1.5  # m, residual truncation in the data term
LAMBDA = 0.5  # smoothness weight (m^2 per m of disagreeing boundary)


@dataclass
class RoofFace:
    poly: Polygon  # BNG xy
    plane: np.ndarray  # (a, b, c): z = a x + b y + c

    @property
    def flat(self) -> bool:
        return float(np.hypot(self.plane[0], self.plane[1])) < 1e-6

    def z(self, xy: np.ndarray) -> np.ndarray:
        xy = np.atleast_2d(xy)
        return xy[:, 0] * self.plane[0] + xy[:, 1] * self.plane[1] + self.plane[2]


@dataclass
class Roof:
    faces: list[RoofFace]
    planes_detected: int = 0

    def top(self) -> float:
        zs = [f.z(np.asarray(f.poly.exterior.coords)).max() for f in self.faces]
        return float(max(zs)) if zs else 0.0


def _plane_at(p: np.ndarray, xy: np.ndarray) -> np.ndarray:
    return xy[:, 0] * p[0] + xy[:, 1] * p[1] + p[2]


def _align_gradient(p: np.ndarray, xy: np.ndarray, dirs: list[np.ndarray], tol_deg: float = 6.0) -> np.ndarray:
    """Turn a pitched plane's slope direction onto the nearest footprint axis
    (keeping slope and the height at its inliers' centre), so eaves, ridges
    and mansard breaks run parallel to the walls."""
    g = np.array([p[0], p[1]])
    s = float(np.hypot(*g))
    if s < 1e-9 or not dirs or len(xy) == 0:
        return p
    gd = g / s
    best, best_ang = None, np.radians(tol_deg)
    for e in dirs:
        for ax in (e, -e, np.array([-e[1], e[0]]), np.array([e[1], -e[0]])):
            ang = np.arccos(np.clip(gd @ ax, -1.0, 1.0))
            if ang < best_ang:
                best, best_ang = ax, ang
    if best is None:
        return p
    c = xy.mean(axis=0)
    zc = c[0] * p[0] + c[1] * p[1] + p[2]
    a, b = best * s
    return np.array([a, b, zc - a * c[0] - b * c[1]])


def _regularise(xy, z, labels, planes, dirs=None) -> tuple[np.ndarray, list[np.ndarray]]:
    """Flat-ish planes become horizontal, pitched planes face the footprint
    axes, near-duplicate planes merge and refit."""
    planes = [np.array(p, dtype=np.float64) for p in planes]
    K = len(planes)
    for k in range(K):
        m = labels == k
        if np.hypot(planes[k][0], planes[k][1]) < FLAT_SLOPE and m.any():
            planes[k] = np.array([0.0, 0.0, float(np.median(z[m]))])
    parent = list(range(K))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    centroid = [xy[labels == k].mean(axis=0) if (labels == k).any() else None for k in range(K)]
    for i in range(K):
        for j in range(i + 1, K):
            ri, rj = find(i), find(j)
            if ri == rj or centroid[i] is None or centroid[j] is None:
                continue
            pi, pj = planes[i], planes[j]
            if np.hypot(pi[0] - pj[0], pi[1] - pj[1]) > np.tan(np.radians(3.0)):
                continue
            c = np.vstack([centroid[i], centroid[j]])
            if np.all(np.abs(_plane_at(pi, c) - _plane_at(pj, c)) < 0.3):
                parent[rj] = ri
    roots = sorted({find(k) for k in range(K)})
    remap = {r: i for i, r in enumerate(roots)}
    new_labels = np.full_like(labels, -1)
    for k in range(K):
        new_labels[labels == k] = remap[find(k)]
    out = []
    for r in roots:
        m = new_labels == remap[r]
        p = planes[r]
        if m.sum() >= 3 and np.hypot(p[0], p[1]) >= FLAT_SLOPE:
            A = np.column_stack([xy[m], np.ones(int(m.sum()))])
            p = np.linalg.lstsq(A, z[m], rcond=None)[0]
        if np.hypot(p[0], p[1]) < FLAT_SLOPE and m.any():
            p = np.array([0.0, 0.0, float(np.median(z[m]))])
        elif dirs:
            p = _align_gradient(p, xy[m], dirs)
        out.append(np.asarray(p, dtype=np.float64))
    return new_labels, out


def _label_image(samples: RoofSamples, labels: np.ndarray):
    rc = samples.rc
    r0, c0 = rc.min(axis=0)
    h, w = rc.max(axis=0) - rc.min(axis=0) + 1
    img = np.full((h, w), -1, dtype=np.int32)
    img[rc[:, 0] - r0, rc[:, 1] - c0] = labels
    return img, r0, c0


def _grid_to_world(samples: RoofSamples, gc: np.ndarray, gr: np.ndarray) -> np.ndarray:
    ref_xy, ref_rc = samples.xy[0], samples.rc[0]
    return np.column_stack([ref_xy[0] + (gc - ref_rc[1]), ref_xy[1] - (gr - ref_rc[0])])


def _boundaries(samples: RoofSamples, labels: np.ndarray) -> dict[tuple[int, int], np.ndarray]:
    """World-space midpoints of raster edges between differently labelled facets."""
    img, r0, c0 = _label_image(samples, labels)
    h, w = img.shape
    pairs: dict[tuple[int, int], list] = {}
    for dr, dc in ((0, 1), (1, 0)):
        a = img[: h - dr, : w - dc]
        b = img[dr:, dc:]
        m = (a >= 0) & (b >= 0) & (a != b)
        ii, jj = np.nonzero(m)
        for r, c in zip(ii, jj):
            la, lb = int(a[r, c]), int(b[r, c])
            key = (min(la, lb), max(la, lb))
            pairs.setdefault(key, []).append((c + c0 + dc / 2, r + r0 + dr / 2))
    out = {}
    for k, v in pairs.items():
        g = np.array(v)
        out[k] = _grid_to_world(samples, g[:, 0], g[:, 1])
    return out


def _snap_direction(d: np.ndarray, dirs: list[np.ndarray], tol_deg: float = 12.0) -> np.ndarray:
    best, best_ang = d, np.radians(tol_deg)
    for e in dirs:
        for s in (e, np.array([-e[1], e[0]])):
            ang = np.arccos(np.clip(abs(d @ s), -1, 1))
            if ang < best_ang:
                best, best_ang = (s if d @ s >= 0 else -s), ang
    return best / np.linalg.norm(best)


def _fit_lines(pts: np.ndarray, dirs: list[np.ndarray], max_lines: int = 6, tol: float = 0.6) -> list[tuple[np.ndarray, np.ndarray]]:
    """Sequential line fitting (RANSAC on direction-snapped candidates)."""
    out = []
    rest = pts
    rng = np.random.default_rng(0)
    while len(rest) >= 3 and len(out) < max_lines:
        best_inl, best = None, None
        for _ in range(60):
            i, j = rng.choice(len(rest), 2, replace=False)
            d = rest[j] - rest[i]
            if np.linalg.norm(d) < 1.0:
                continue
            d = _snap_direction(d / np.linalg.norm(d), dirs)
            n = np.array([-d[1], d[0]])
            dist = np.abs((rest - rest[i]) @ n)
            inl = dist < tol
            if best_inl is None or inl.sum() > best_inl.sum():
                best_inl, best = inl, (rest[i], d)
        if best_inl is None or best_inl.sum() < 3:
            break
        p0, d = best
        n = np.array([-d[1], d[0]])
        off = float(np.median((rest[best_inl] - p0) @ n))
        out.append((p0 + n * off, d))
        rest = rest[~best_inl]
    return out


def _dominant_dirs(poly: Polygon) -> list[np.ndarray]:
    c = np.asarray(poly.exterior.coords)
    d = np.diff(c, axis=0)
    L = np.hypot(d[:, 0], d[:, 1])
    ang = np.arctan2(d[:, 1], d[:, 0]) % (np.pi / 2)
    hist: dict[int, float] = {}
    for a, w in zip(ang, L):
        b = int(round(np.degrees(a))) % 90
        hist[b] = hist.get(b, 0.0) + w
    out = []
    ranked = sorted(hist.items(), key=lambda kv: -kv[1])
    total = sum(hist.values())
    for i, (b, w) in enumerate(ranked[:2]):
        if i > 0 and w < 0.25 * total:
            break
        a = np.radians(b)
        out.append(np.array([np.cos(a), np.sin(a)]))
    return out


def _line_across(poly: Polygon, p0: np.ndarray, d: np.ndarray):
    minx, miny, maxx, maxy = poly.bounds
    R = float(np.hypot(maxx - minx, maxy - miny)) + 2.0
    seg = LineString([p0 - d * R, p0 + d * R])
    cut = seg.intersection(poly.buffer(0.01))
    return cut if not cut.is_empty else None


def candidate_lines(footprint: Polygon, samples: RoofSamples, labels: np.ndarray, planes: list[np.ndarray], part_outlines: list[Polygon]):
    lines = []
    dirs = _dominant_dirs(footprint)
    bnd = _boundaries(samples, labels)
    for (i, j), pts in bnd.items():
        if len(pts) < 3:
            continue
        pi, pj = planes[i], planes[j]
        nx, ny, k = pi[0] - pj[0], pi[1] - pj[1], pi[2] - pj[2]
        nn = np.hypot(nx, ny)
        crease = False
        if nn > 0.08:
            dist = np.abs(pts[:, 0] * nx + pts[:, 1] * ny + k) / nn
            if np.median(dist) < 1.0:
                d = np.array([-ny, nx]) / nn
                p0 = -k * np.array([nx, ny]) / (nn * nn)
                # Anchor near the evidence (numerically better than the foot from the origin).
                t = float(np.median((pts - p0) @ d))
                g = _line_across(footprint, p0 + d * t, d)
                if g is not None:
                    lines.append(g)
                    crease = True
        if not crease:
            dz = np.abs(_plane_at(pi, pts) - _plane_at(pj, pts))
            if np.median(dz) > 0.35:
                for p0, d in _fit_lines(pts, dirs):
                    g = _line_across(footprint, p0, d)
                    if g is not None:
                        lines.append(g)
    for p in part_outlines:
        inter = p.boundary.intersection(footprint.buffer(0.01))
        if not inter.is_empty:
            lines.append(inter)
    return lines


def _cells(footprint: Polygon, lines) -> list[Polygon]:
    if not lines:
        return [footprint]
    noded = unary_union([footprint.boundary, *lines])
    cells = [c for c in polygonize(noded) if footprint.contains(c.representative_point()) and c.area > 1e-3]
    return cells or [footprint]


def _icm(cells, cost: np.ndarray, area: np.ndarray, has_data: np.ndarray, adjacency) -> np.ndarray:
    n, K = cost.shape
    E = cost * area[:, None]
    lab = np.where(has_data, np.argmin(cost, axis=1), -1)
    # Cells without samples inherit from their longest-shared neighbour.
    for _ in range(n):
        changed = False
        for c in range(n):
            if lab[c] >= 0:
                continue
            best, best_len = -1, 0.0
            for d, L in adjacency[c]:
                if lab[d] >= 0 and L > best_len:
                    best, best_len = lab[d], L
            if best >= 0:
                lab[c] = best
                changed = True
        if not changed:
            break
    lab[lab < 0] = 0
    for _ in range(12):
        changed = False
        for c in range(n):
            e = E[c].copy()
            for d, L in adjacency[c]:
                e += LAMBDA * L * (np.arange(K) != lab[d])
            k = int(np.argmin(e))
            if k != lab[c]:
                lab[c] = k
                changed = True
        if not changed:
            break
    return lab


def reconstruct(
    footprint: Polygon,
    samples: RoofSamples,
    ground: float,
    seed: int,
    min_support: int,
    threshold: float = 0.18,
    part_outlines: list[Polygon] | None = None,
) -> Roof:
    footprint = orient(footprint, 1.0)
    n = len(samples.z)
    if n == 0:
        return Roof([RoofFace(footprint, np.array([0.0, 0.0, ground + 6.0]))])
    z = samples.z.astype(np.float64)
    labels, planes = (np.full(n, -1, dtype=np.int32), [])
    if n >= min_support:
        labels, planes = ransac_planes(samples.xy, z, samples.rc, threshold, min_support, max_planes=12, seed=seed)
    if not planes:
        h = float(np.percentile(z, 60))
        return Roof([RoofFace(footprint, np.array([0.0, 0.0, h]))])
    labels = assign_residuals(samples, labels, planes)
    labels, planes = _regularise(samples.xy, z, labels, planes, _dominant_dirs(footprint))
    detected = len(planes)
    # Unexplained clusters (plant rooms, lanterns, domes) get a flat level each.
    free = labels < 0
    if free.sum() >= 6:
        img, r0, c0 = _label_image(samples, np.where(free, 1, 0))
        comp, ncomp = ndimage.label(img == 1)
        cid = comp[samples.rc[:, 0] - r0, samples.rc[:, 1] - c0]
        for k in range(1, ncomp + 1):
            m = cid == k
            if m.sum() < 6:
                continue
            planes.append(np.array([0.0, 0.0, float(np.median(z[m]))]))
            labels[m] = len(planes) - 1
    lines = candidate_lines(footprint, samples, labels, planes, part_outlines or [])
    cells = _cells(footprint, lines)
    K = len(planes)
    cost = np.zeros((len(cells), K))
    has = np.zeros(len(cells), dtype=bool)
    area = np.array([c.area for c in cells])
    tree = shapely.STRtree(cells)
    pi, ci = tree.query(shapely.points(samples.xy), predicate="within")
    P = np.stack(planes)
    resid = np.minimum(np.abs(samples.xy[:, :1] * P[:, 0] + samples.xy[:, 1:2] * P[:, 1] + P[:, 2] - z[:, None]), TRUNC)
    sums = np.zeros((len(cells), K))
    cnt = np.zeros(len(cells))
    np.add.at(sums, ci, resid[pi])
    np.add.at(cnt, ci, 1.0)
    has = cnt > 0
    cost[has] = sums[has] / cnt[has, None]
    adjacency: list[list[tuple[int, float]]] = [[] for _ in cells]
    ai, bi = tree.query(cells, predicate="touches")
    for a, b in zip(ai, bi):
        if a < b:
            L = cells[a].boundary.intersection(cells[b].boundary).length
            if L > 1e-3:
                adjacency[a].append((int(b), L))
                adjacency[b].append((int(a), L))
    lab = _icm(cells, cost, area, has, adjacency)
    faces = []
    for k in np.unique(lab):
        merged = unary_union([cells[i] for i in np.where(lab == k)[0]]).buffer(0)
        # Drop the collinear vertices left where other lines crossed.
        merged = merged.simplify(0.02, preserve_topology=True).buffer(0)
        for g in getattr(merged, "geoms", [merged]):
            if g.geom_type == "Polygon" and g.area > 0.05:
                faces.append(RoofFace(orient(g, 1.0), planes[int(k)]))
    # Tiny faces merge into the neighbour with the longest shared boundary.
    big = [f for f in faces if f.poly.area >= 2.5]
    small = [f for f in faces if f.poly.area < 2.5]
    if not big:
        big, small = faces, []
    for s in small:
        best, best_len = None, 0.0
        for f in big:
            L = s.poly.boundary.intersection(f.poly.boundary).length
            if L > best_len:
                best, best_len = f, L
        if best is not None:
            u = best.poly.union(s.poly).buffer(0)
            if u.geom_type == "Polygon":
                best.poly = orient(u, 1.0)
    return Roof(big, detected)


def height_at(roof: Roof, xy: np.ndarray) -> np.ndarray:
    """Roof height at points (the face containing each point; else nearest face)."""
    xy = np.atleast_2d(xy)
    out = np.full(len(xy), np.nan)
    pts = shapely.points(xy)
    for f in roof.faces:
        inside = shapely.covers(f.poly, pts)
        sel = inside & np.isnan(out)
        if sel.any():
            out[sel] = f.z(xy[sel])
    miss = np.isnan(out)
    if miss.any():
        for i in np.where(miss)[0]:
            d = [f.poly.distance(pts[i]) for f in roof.faces]
            out[i] = roof.faces[int(np.argmin(d))].z(xy[i : i + 1])[0]
    return out


def shared_edges(a: Polygon, b: Polygon, tol: float = 1e-3) -> list[np.ndarray]:
    """Line segments shared by two faces' boundaries."""
    inter = a.boundary.intersection(b.boundary.buffer(tol)) if a.distance(b) < tol else None
    if inter is None or inter.is_empty:
        return []
    out = []
    geoms = getattr(inter, "geoms", [inter])
    for g in geoms:
        if isinstance(g, (LineString, MultiLineString)) and g.length > 0.05:
            for ln in getattr(g, "geoms", [g]):
                c = np.asarray(ln.coords)
                for i in range(len(c) - 1):
                    if np.linalg.norm(c[i + 1] - c[i]) > 0.05:
                        out.append(c[i : i + 2])
    return out
