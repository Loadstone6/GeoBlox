"""Preview renders of the compiled world (offline, for validation): the
parts exactly as Roblox will place them, terrain columns as the ground, and
prefab trees approximated by their crown balls."""

from __future__ import annotations

import numpy as np

from .config import BUILD, PARAMS
from .partmap import SHAPE_BALL, part_triangles
from .render import render

TERRAIN_RGB = {
    0: (0.5, 0.5, 0.5), 1: (0.25, 0.25, 0.26), 2: (0.62, 0.60, 0.56), 3: (0.36, 0.50, 0.24), 4: (0.30, 0.44, 0.22),
    5: (0.40, 0.32, 0.24), 6: (0.6, 0.6, 0.58), 7: (0.2, 0.3, 0.32), 8: (0.32, 0.30, 0.26), 9: (0.35, 0.36, 0.38),
    10: (0.70, 0.66, 0.56), 11: (0.42, 0.40, 0.38), 12: (0.55, 0.35, 0.28),
}
# Rough albedo response of Roblox materials relative to their colour.
MATERIAL_GAIN = {"Glass": 0.8, "Neon": 1.3, "Metal": 0.9, "Asphalt": 0.85, "Slate": 0.9, "Foil": 1.1}


def parts_mesh(region, rows) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Flat-shaded triangles (BNG metres) for part rows from partmap.Parts."""
    e0, n0 = region.origin
    mps = PARAMS.metres_per_stud
    V, T, C, N = [], [], [], []
    off = 0
    for shape, center, R, size, look, extra in rows:
        v, t = part_triangles(shape, center, R, size, extra)
        # studs -> BNG metres (z up)
        w = np.column_stack([v[:, 0] * mps + e0, n0 - v[:, 2] * mps, v[:, 1] * mps + PARAMS.h0])
        tri = w[t]
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        if shape == SHAPE_BALL or extra:
            c = tri.mean(axis=1) - w.mean(axis=0)
            flip = (nrm * c).sum(axis=1) < 0
            nrm[flip] = -nrm[flip]
        col = np.array(look.color, dtype=np.float64) / 255.0 * MATERIAL_GAIN.get(look.material, 1.0)
        k = len(t)
        V.append(tri.reshape(-1, 3))
        T.append(np.arange(3 * k).reshape(k, 3) + off)
        C.append(np.tile(col, (3 * k, 1)))
        N.append(np.repeat(nrm, 3, axis=0))
        off += 3 * k
    if not V:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3)), np.zeros((0, 3))
    return np.vstack(V), np.vstack(T), np.vstack(C), np.vstack(N)


def terrain_mesh(cols, region, step: int = 1):
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
    Nn = n0 - gz * mps
    Z = np.where(np.isnan(w), h, w)
    v = np.column_stack([E.ravel(), Nn.ravel(), Z.ravel()])
    c = np.array([TERRAIN_RGB.get(int(k), (0.5, 0.5, 0.5)) for k in m.ravel()])
    c[~np.isnan(w.ravel())] = (0.22, 0.30, 0.32)
    ii, jj = np.meshgrid(np.arange(nx - 1), np.arange(nz - 1), indexing="ij")
    a = (ii * nz + jj).ravel()
    t = np.vstack([np.column_stack([a, a + nz, a + nz + 1]), np.column_stack([a, a + nz + 1, a + 1])])
    nrm = np.tile([0, 0, 1.0], (len(v), 1))
    return v, t, c, nrm


LEAF = {"tree_plane": (88, 112, 52), "tree_broadleaf": (72, 102, 46), "tree_conifer": (44, 70, 44), "tree_ornamental": (104, 128, 60)}


def tree_mesh(region, world):
    """Prefab trees approximated as a trunk and a crown ellipsoid (metres, z up)."""
    from .partmap import Look, Parts

    P = Parts(region)
    mps = PARAMS.metres_per_stud
    for m in world.models:
        if m.kind != "trees":
            continue
        for name, x, y, z, _yaw, h, crown, _seed in m.prefabs:
            base = np.array([x, y, z])
            hs, cs = h / mps, max(1.2, crown) / mps
            P.cylinder_s(base, base + np.array([0.0, hs * 0.45, 0.0]), max(0.25, h * 0.02) / mps, Look("Wood", (96, 84, 70)))
            c = base + np.array([0.0, hs * 0.66, 0.0])
            I = np.eye(3)
            P.ellipsoid_s(c, I[:, 0], I[:, 1], I[:, 2], np.array([2 * cs, hs * 0.62, 2 * cs]), Look("LeafyGrass", LEAF.get(name, (72, 102, 46))))
    return parts_mesh(region, P.rows)


VIEWS = {
    "overview": ((531150, 178750, 700), (530150, 179800, 0), 52),
    "westminster": ((530620, 179480, 70), (530260, 179640, 40), 62),
    "bridge_street": ((530470, 179668, 11.8), (530250, 179655, 30), 70),
    "whitehall": ((530175, 179880, 6.5), (530130, 180250, 14), 70),
    "parliament_sq": ((530020, 179700, 24), (530255, 179600, 34), 64),
    "trafalgar": ((530120, 180250, 40), (529990, 180460, 18), 62),
    "south_bank": ((530860, 179560, 60), (530580, 179980, 50), 62),
}


def render_views(meshes: list, names=None, size=(1600, 900), prefix: str = "preview_") -> list:
    V, T, C, N = [], [], [], []
    off = 0
    for v, t, c, n in meshes:
        if len(v) == 0:
            continue
        V.append(v)
        T.append(t + off)
        C.append(c)
        N.append(n)
        off += len(v)
    V, T, C, N = np.vstack(V), np.vstack(T), np.vstack(C), np.vstack(N)
    BUILD.mkdir(parents=True, exist_ok=True)
    out = []
    for name, (eye, tgt, fov) in VIEWS.items():
        if names and name not in names:
            continue
        img = render(V, T, C, N, eye=eye, target=tgt, fov_deg=fov, size=size, fog=2200)
        path = BUILD / f"{prefix}{name}.png"
        img.save(path)
        out.append(path)
        print("rendered", name, flush=True)
    return out


def terrain_from_modules(path, region) -> dict:
    """Terrain columns back from the generated Luau modules."""
    import base64
    import re
    import struct

    import zstandard

    from .export import TERRAIN_SCALE

    chunks = []
    for f in sorted(path.glob("*.luau")):
        payload = re.search(r"\[==\[(.*)\]==\]", f.read_text(), re.S).group(1)
        blob = zstandard.ZstdDecompressor().decompress(base64.b64decode(payload), max_output_size=1 << 26)
        x0, z0, nx, nz = struct.unpack("<iiHH", blob[6:18])
        rec = np.frombuffer(blob[18:], dtype=[("h", "<u2"), ("m", "u1"), ("w", "<u2")]).reshape(nx, nz)
        chunks.append((x0, z0, rec))
    X0 = min(c[0] for c in chunks)
    Z0 = min(c[1] for c in chunks)
    X1 = max(c[0] + c[2].shape[0] * 4 for c in chunks)
    Z1 = max(c[1] + c[2].shape[1] * 4 for c in chunks)
    nx, nz = (X1 - X0) // 4, (Z1 - Z0) // 4
    h = np.zeros((nx, nz))
    m = np.zeros((nx, nz), dtype=np.uint8)
    w = np.full((nx, nz), np.nan)
    for x0, z0, rec in chunks:
        i, j = (x0 - X0) // 4, (z0 - Z0) // 4
        a, b = rec.shape
        h[i : i + a, j : j + b] = rec["h"] / TERRAIN_SCALE * PARAMS.metres_per_stud + PARAMS.h0
        m[i : i + a, j : j + b] = rec["m"]
        ww = rec["w"].astype(np.float64)
        w[i : i + a, j : j + b] = np.where(ww > 0, ww / TERRAIN_SCALE * PARAMS.metres_per_stud + PARAMS.h0, np.nan)
    return {"x0": X0, "z0": Z0, "nx": nx, "nz": nz, "height": h, "material": m, "water": w}


def main() -> None:
    """Render previews from the compiled world on disk:
    python -m geoblox.preview [view ...]"""
    import sys

    from .config import ROOT, WESTMINSTER
    from .world import read

    world = read(ROOT / "world", WESTMINSTER)
    cols = terrain_from_modules(ROOT / "src" / "generated_server" / "Terrain", WESTMINSTER)
    rows = [row for m in world.models for row in m.parts.rows]
    render_views([parts_mesh(WESTMINSTER, rows), terrain_mesh(cols, WESTMINSTER), tree_mesh(WESTMINSTER, world)], names=sys.argv[1:] or None)


if __name__ == "__main__":
    main()
