"""Offline preview renderer: perspective z-buffer rasteriser with sun shading,
shadow mapping and fog, used to validate reconstructions without Roblox."""

from __future__ import annotations

import numba
import numpy as np
from PIL import Image


def look_at(eye, target, up=(0.0, 0.0, 1.0)):
    eye, target, up = (np.asarray(a, dtype=np.float64) for a in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return eye, r, u, f


@numba.njit(cache=True)
def _raster(sx, sy, sz, tris, colors, normals, W, H, zbuf, img, nbuf, wpos, wx, wy, wz):
    for t in range(tris.shape[0]):
        i0, i1, i2 = tris[t, 0], tris[t, 1], tris[t, 2]
        z0, z1, z2 = sz[i0], sz[i1], sz[i2]
        if z0 <= 0.5 or z1 <= 0.5 or z2 <= 0.5:
            continue
        x0, y0, x1, y1, x2, y2 = sx[i0], sy[i0], sx[i1], sy[i1], sx[i2], sy[i2]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        xmin = max(0, int(min(x0, min(x1, x2))))
        xmax = min(W - 1, int(max(x0, max(x1, x2))) + 1)
        ymin = max(0, int(min(y0, min(y1, y2))))
        ymax = min(H - 1, int(max(y0, max(y1, y2))) + 1)
        if xmin > xmax or ymin > ymax:
            continue
        iz0, iz1, iz2 = 1.0 / z0, 1.0 / z1, 1.0 / z2
        for py in range(ymin, ymax + 1):
            fy = py + 0.5
            for px in range(xmin, xmax + 1):
                fx = px + 0.5
                w0 = ((x1 - fx) * (y2 - fy) - (x2 - fx) * (y1 - fy)) / area
                w1 = ((x2 - fx) * (y0 - fy) - (x0 - fx) * (y2 - fy)) / area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                iz = w0 * iz0 + w1 * iz1 + w2 * iz2
                z = 1.0 / iz
                if z < zbuf[py, px]:
                    zbuf[py, px] = z
                    a0, a1, a2 = w0 * iz0 * z, w1 * iz1 * z, w2 * iz2 * z
                    for ch in range(3):
                        img[py, px, ch] = a0 * colors[i0, ch] + a1 * colors[i1, ch] + a2 * colors[i2, ch]
                        nbuf[py, px, ch] = a0 * normals[i0, ch] + a1 * normals[i1, ch] + a2 * normals[i2, ch]
                    wpos[py, px, 0] = a0 * wx[i0] + a1 * wx[i1] + a2 * wx[i2]
                    wpos[py, px, 1] = a0 * wy[i0] + a1 * wy[i1] + a2 * wy[i2]
                    wpos[py, px, 2] = a0 * wz[i0] + a1 * wz[i1] + a2 * wz[i2]


@numba.njit(cache=True)
def _shadow_map(sx, sy, sz, tris, W, H, zbuf):
    for t in range(tris.shape[0]):
        i0, i1, i2 = tris[t, 0], tris[t, 1], tris[t, 2]
        x0, y0, x1, y1, x2, y2 = sx[i0], sy[i0], sx[i1], sy[i1], sx[i2], sy[i2]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        xmin = max(0, int(min(x0, min(x1, x2))))
        xmax = min(W - 1, int(max(x0, max(x1, x2))) + 1)
        ymin = max(0, int(min(y0, min(y1, y2))))
        ymax = min(H - 1, int(max(y0, max(y1, y2))) + 1)
        for py in range(ymin, ymax + 1):
            for px in range(xmin, xmax + 1):
                w0 = ((x1 - px - 0.5) * (y2 - py - 0.5) - (x2 - px - 0.5) * (y1 - py - 0.5)) / area
                w1 = ((x2 - px - 0.5) * (y0 - py - 0.5) - (x0 - px - 0.5) * (y2 - py - 0.5)) / area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * sz[i0] + w1 * sz[i1] + w2 * sz[i2]
                if z < zbuf[py, px]:
                    zbuf[py, px] = z


def render(verts, tris, colors, normals, eye, target, fov_deg=55.0, size=(1600, 900), sun=(0.45, -0.35, 0.82), fog=900.0, sky=(0.70, 0.78, 0.86)):
    W, H = size
    eye, r, u, f = look_at(eye, target)
    rel = verts - eye
    cx, cy, cz = rel @ r, rel @ u, rel @ f
    focal = (W / 2) / np.tan(np.radians(fov_deg) / 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        sx = W / 2 + focal * cx / cz
        sy = H / 2 - focal * cy / cz
    zbuf = np.full((H, W), np.inf)
    img = np.zeros((H, W, 3))
    nbuf = np.zeros((H, W, 3))
    wpos = np.zeros((H, W, 3))
    tris = np.ascontiguousarray(tris, dtype=np.int64)
    _raster(sx, sy, cz, tris, colors.astype(np.float64), normals.astype(np.float64), W, H, zbuf, img, nbuf, wpos, verts[:, 0], verts[:, 1], verts[:, 2])

    # Orthographic shadow map from the sun.
    sun = np.asarray(sun, dtype=np.float64)
    sun /= np.linalg.norm(sun)
    sr = np.cross(sun, [0, 0, 1.0])
    sr /= np.linalg.norm(sr)
    su = np.cross(sr, sun)
    center = verts.mean(axis=0)
    rs = verts - center
    lx, ly, lz = rs @ sr, rs @ su, -(rs @ sun)
    S = 4096
    ext = max(np.ptp(lx), np.ptp(ly)) / 2 + 1
    scale = (S / 2) / ext
    smap = np.full((S, S), np.inf)
    _shadow_map(S / 2 + lx * scale, S / 2 - ly * scale, lz, tris, S, S, smap)

    hit = np.isfinite(zbuf)
    n = nbuf / np.maximum(np.linalg.norm(nbuf, axis=2, keepdims=True), 1e-9)
    lam = np.clip((n * sun).sum(axis=2), 0, 1)
    p = wpos[hit] - center
    plx, ply, plz = p @ sr, p @ su, -(p @ sun)
    ix = np.clip((S / 2 + plx * scale).astype(int), 0, S - 1)
    iy = np.clip((S / 2 - ply * scale).astype(int), 0, S - 1)
    lit = np.ones(H * W)
    lit_hit = (plz <= smap[iy, ix] + 0.6 + 0.004 * ext).astype(np.float64)
    lit[hit.ravel()] = lit_hit
    lit = lit.reshape(H, W)
    sky_amb = 0.55 + 0.25 * np.clip(n[..., 2], 0, 1)
    shade = sky_amb + 0.85 * lam * lit
    col = img * shade[..., None] * np.array([1.02, 1.0, 0.96])
    dist = np.where(hit, zbuf, fog * 4)
    fogf = 1 - np.exp(-(dist / fog) ** 1.4)
    sky_rgb = np.array(sky)
    vgrad = np.linspace(0.85, 1.05, H)[:, None, None]
    bg = sky_rgb[None, None, :] * vgrad
    out = np.where(hit[..., None], col * (1 - fogf[..., None]) + sky_rgb * fogf[..., None], bg)
    out = np.clip(out, 0, 1) ** (1 / 1.1)
    return Image.fromarray((out * 255).astype(np.uint8))
