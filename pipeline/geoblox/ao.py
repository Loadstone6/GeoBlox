"""Horizon-based ambient occlusion from the LiDAR DSM.

For a surface point p with normal n, sky visibility is estimated by scanning
K azimuths across the DSM heightfield and finding the horizon elevation angle
θ_k in each direction (the steepest DSM rise seen from p). The unoccluded
fraction of a direction's sky arc is 1 − sin θ_k; directions are weighted by
how much they face the surface (cosine with the horizontal part of n), which
for vertical walls restricts the integral to the half-space in front of the
wall. This is the heightfield form of horizon-based AO (Bavoil et al. 2008)
evaluated on measured city geometry instead of a depth buffer.
"""

from __future__ import annotations

import numba
import numpy as np

from .geo import Grid


@numba.njit(cache=True, parallel=True)
def _horizon_ao(px, py, pz, nx, ny, nz, dsm, e_min, n_max, k_dirs, max_dist):
    n = px.shape[0]
    out = np.ones(n)
    h, w = dsm.shape
    for i in numba.prange(n):
        acc = 0.0
        wsum = 0.0
        hn = np.sqrt(nx[i] * nx[i] + ny[i] * ny[i])
        for k in range(k_dirs):
            ang = 2.0 * np.pi * k / k_dirs
            dx = np.cos(ang)
            dy = np.sin(ang)
            if hn > 0.2:
                wgt = (dx * nx[i] + dy * ny[i]) / hn
                if wgt <= 0.0:
                    continue
                # Walls also see straight up: blend facing weight with a floor.
                wgt = 0.25 + 0.75 * wgt
            else:
                wgt = 1.0
            # March outwards with growing steps; skip the first metre to avoid
            # sampling the surface the point sits on.
            t = 1.2
            max_tan = 0.0
            while t < max_dist:
                x = px[i] + dx * t
                y = py[i] + dy * t
                c = int((x - e_min))
                r = int((n_max - y))
                if r < 0 or r >= h or c < 0 or c >= w:
                    break
                rise = dsm[r, c] - pz[i]
                if rise > 0.0:
                    tn = rise / t
                    if tn > max_tan:
                        max_tan = tn
                t += 0.6 + t * 0.12
            sin_t = max_tan / np.sqrt(1.0 + max_tan * max_tan)
            acc += wgt * (1.0 - sin_t)
            wsum += wgt
        if wsum > 0:
            v = acc / wsum
            # Upward-facing component of the normal sees the zenith fully.
            up = max(0.0, nz[i])
            out[i] = v * (1.0 - 0.3 * up) + 0.3 * up * min(1.0, v * 1.4)
    return out


class AmbientOcclusion:
    def __init__(self, dsm: Grid, directions: int = 16, max_dist: float = 70.0):
        self.dsm = dsm
        self.k = directions
        self.max_dist = max_dist
        self.data = np.ascontiguousarray(dsm.data, dtype=np.float64)

    def __call__(self, verts: np.ndarray, normals: np.ndarray) -> np.ndarray:
        if len(verts) == 0:
            return np.zeros(0)
        v = np.ascontiguousarray(verts, dtype=np.float64)
        n = np.ascontiguousarray(normals, dtype=np.float64)
        # Lift points slightly along the normal so they don't self-occlude.
        lift = v + n * 0.35
        lift[:, 2] += 0.15
        return _horizon_ao(
            lift[:, 0], lift[:, 1], lift[:, 2], n[:, 0], n[:, 1], n[:, 2],
            self.data, self.dsm.e_min, self.dsm.n_max, self.k, self.max_dist,
        )


def no_ao(verts: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Placeholder used during emission; AO is baked once per group afterwards."""
    return np.ones(len(verts))


def bake(collector, ao: AmbientOcclusion, floor: float = 0.36, skip: tuple[str, ...] = ()) -> None:
    """Multiply every group's vertex colours by sky visibility, in one batched pass."""
    from .mesh import GroupBuffer

    for key, buf in collector.groups.items():
        if key[2] in skip or not buf.pieces:
            continue
        merged = buf.merged()
        vis = ao(merged.v, merged.n)
        merged.c = merged.c * (floor + (1.0 - floor) * np.clip(vis, 0, 1))[:, None]
        collector.groups[key] = GroupBuffer([merged])
