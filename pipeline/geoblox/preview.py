"""Preview renders of the reconstructed model (offline, for validation)."""

from __future__ import annotations

import numpy as np

from .config import BUILD, PARAMS
from .mesh import Collector
from .render import render

TERRAIN_RGB = {
    0: (0.5, 0.5, 0.5), 1: (0.25, 0.25, 0.26), 2: (0.62, 0.60, 0.56), 3: (0.36, 0.50, 0.24), 4: (0.30, 0.44, 0.22),
    5: (0.40, 0.32, 0.24), 6: (0.6, 0.6, 0.58), 7: (0.2, 0.3, 0.32), 8: (0.32, 0.30, 0.26),
}


def _terrain_mesh(scene, cols, region, step=2):
    e0, n0 = region.origin
    mps = PARAMS.metres_per_stud
    h = cols["height"][::step, ::step]
    m = cols["material"][::step, ::step]
    w = cols["water"][::step, ::step]
    nx, nz = h.shape
    xs = cols["x0"] + (np.arange(0, cols["nx"], step) + 0.5) * 4
    zs = cols["z0"] + (np.arange(0, cols["nz"], step) + 0.5) * 4
    gx, gz = np.meshgrid(xs, zs, indexing="ij")
    E = e0 + gx * mps
    N = n0 - gz * mps
    Z = np.where(np.isnan(w), h - 0.05, w)
    v = np.column_stack([E.ravel(), N.ravel(), Z.ravel()])
    c = np.array([TERRAIN_RGB.get(int(k), (0.5, 0.5, 0.5)) for k in m.ravel()])
    c[~np.isnan(w.ravel())] = (0.22, 0.30, 0.32)
    t = []
    for i in range(nx - 1):
        for j in range(nz - 1):
            a = i * nz + j
            t.append([a, a + nz, a + nz + 1])
            t.append([a, a + nz + 1, a + 1])
    t = np.array(t)
    nrm = np.tile([0, 0, 1.0], (len(v), 1))
    return v, t, c, nrm


def _icosphere(sub=1):
    t = (1 + 5 ** 0.5) / 2
    v = [np.array(p, dtype=float) for p in [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
    v = [x / np.linalg.norm(x) for x in v]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
         (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(sub):
        cache = {}
        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = (v[a] + v[b]) / 2
                v.append(m / np.linalg.norm(m))
                cache[k] = len(v) - 1
            return cache[k]
        nf = []
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return np.array(v), np.array(f)


ICO_V, ICO_F = _icosphere(1)
LEAF = {0: [(88, 112, 52), (100, 122, 58), (78, 104, 48)], 1: [(72, 102, 46), (86, 114, 50), (64, 92, 42)], 2: [(44, 70, 44), (52, 78, 48), (40, 64, 40)], 3: [(104, 128, 60), (116, 138, 66), (96, 120, 56)]}


def _tree_mesh(trees):
    """Same crown model as the Roblox client (src/client/Trees.luau), in metres (z up)."""
    vs, ts, cs, ns = [], [], [], []
    off = 0
    for tr in trees:
        rng = np.random.default_rng(tr.seed)
        h, r = tr.height, max(1.2, tr.crown)
        base = np.array([tr.x, tr.y, tr.ground])
        cb = 0.25 if tr.kind == 2 else 0.38
        mid = base + np.array([0, 0, h * (cb + (1 - cb) * 0.5)])
        half_h = h * (1 - cb) * 0.5
        pal = np.array(LEAF.get(tr.kind, LEAF[1])) / 255.0
        n = 2 if tr.kind == 3 else int(rng.integers(4, 7))
        for k in range(1, n + 1):
            a = k / n * 2 * np.pi + rng.random() * 0.8
            spread = r * (0.1 if k == 1 else 0.45)
            c = mid + np.array([np.cos(a) * spread, np.sin(a) * spread, (rng.random() - 0.35) * half_h * 0.6])
            rx = r * (0.6 + 0.15 * rng.random())
            rz = half_h * (0.62 + 0.15 * rng.random())
            ph = rng.random() * 10
            d = ICO_V
            jit = 1 + 0.14 * np.sin(d[:, 0] * 3.1 + ph) * np.cos(d[:, 2] * 2.7 - ph)
            v = c + np.column_stack([d[:, 0] * rx * jit, d[:, 1] * rx * jit, d[:, 2] * rz * jit])
            shade = (0.62 + 0.38 * (d[:, 2] * 0.5 + 0.5)) * (0.82 if k == 1 else 1.0)
            vs.append(v)
            ts.append(ICO_F + off)
            cs.append(pal[k % 3][None, :] * shade[:, None])
            ns.append(d)
            off += len(v)
        top = base + np.array([0, 0, h * (cb + 0.12)])
        w = np.clip(h * 0.022, 0.2, 0.7)
        tv = np.array([base + [-w, 0, 0], base + [w, 0, 0], top + [w * 0.6, 0, 0], top + [-w * 0.6, 0, 0]])
        vs.append(tv)
        ts.append(np.array([[0, 1, 2], [0, 2, 3], [0, 2, 1], [0, 3, 2]]) + off)
        cs.append(np.tile([0.40, 0.36, 0.30], (4, 1)))
        ns.append(np.tile([0, -1.0, 0], (4, 1)))
        off += 4
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int), np.zeros((0, 3)), np.zeros((0, 3))
    return np.vstack(vs), np.vstack(ts), np.vstack(cs), np.vstack(ns)


def render_all(scene, col: Collector, gnd, trees, cols, region) -> None:
    V, T, C, N = [], [], [], []
    off = 0
    for (chunk, lod, group), buf in col.groups.items():
        if lod == 1 or group == "window_lit":
            continue
        p = buf.merged()
        V.append(p.v)
        T.append(p.t + off)
        C.append(p.c)
        N.append(p.n)
        off += len(p.v)
    for v, t, c, n in (_terrain_mesh(scene, cols, region), _tree_mesh(trees)):
        V.append(v)
        T.append(t + off)
        C.append(c)
        N.append(n)
        off += len(v)
    V, T, C, N = np.vstack(V), np.vstack(T), np.vstack(C), np.vstack(N)
    views = {
        "overview": ((531150, 178750, 700), (530150, 179800, 0), 52),
        "westminster": ((530620, 179480, 70), (530260, 179640, 40), 62),
        "bridge_street": ((530470, 179672, 9.5), (530250, 179655, 30), 70),
        "whitehall": ((530175, 179880, 6.5), (530130, 180250, 14), 70),
        "parliament_sq": ((529990, 179500, 22), (530200, 179680, 30), 68),
        "trafalgar": ((530120, 180250, 40), (529990, 180460, 18), 62),
        "south_bank": ((530860, 179560, 60), (530580, 179980, 50), 62),
    }
    BUILD.mkdir(parents=True, exist_ok=True)
    for name, (eye, tgt, fov) in views.items():
        img = render(V, T, C, N, eye=eye, target=tgt, fov_deg=fov, size=(1600, 900), fog=2200)
        img.save(BUILD / f"preview_{name}.png")
        print("rendered", name, flush=True)
