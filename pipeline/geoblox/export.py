"""Binary export to Roblox: zstd-compressed, base64-encoded Luau modules.

Mesh chunk blob ("GBM2", little-endian):
  header : magic[4] u16 version, f32 cx, cy, cz (chunk anchor, studs), u16 nmesh, u16 ntree, u16 nprop, u16 ncoll
  mesh   : u8 group, u8 lod, u16 nverts, u16 ntris,
           i16[3] pos (studs * 32, relative to anchor), i8[3] normal (*127),
           u8[3] colour, i16[2] uv (*64)   -- per vertex
           u16[3] indices                   -- per triangle
  tree   : i16 x, y, z (studs * 4, rel), u8 height (*4 m), u8 crown (*10 m), u8 kind, u16 seed
  prop   : u8 kind, i16 x, y, z (studs * 8, rel), u8 yaw (/256 turn), u8 variant
  coll   : i16 cx, cy, cz (studs * 8, rel), u16 sx, sy, sz (studs * 8), u8 yaw (/256 turn)

Terrain chunk blob ("GBT1"): 64 x 64 voxel columns
  header : magic[4] u16 version, i32 x0, z0 (studs, chunk corner), u16 nx, nz
  then per column (x-major): u16 height (studs * 8), u8 material, u16 water (studs * 8, 0 = dry)
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import numpy as np
import zstandard

from .config import PARAMS, Region
from .mesh import Collector, Piece

MAX_V = 55000
MAX_T = 19000


def bng_to_studs(region: Region, v: np.ndarray) -> np.ndarray:
    e0, n0 = region.origin
    mps = PARAMS.metres_per_stud
    return np.column_stack([(v[:, 0] - e0) / mps, (v[:, 2] - PARAMS.h0) / mps, -(v[:, 1] - n0) / mps])


def bng_dir_to_studs(n: np.ndarray) -> np.ndarray:
    return np.column_stack([n[:, 0], n[:, 2], -n[:, 1]])


def split_piece(p: Piece) -> list[Piece]:
    """Split a piece into sub-meshes under the EditableMesh caps."""
    if len(p.v) <= MAX_V and len(p.t) <= MAX_T:
        return [p]
    out = []
    start = 0
    while start < len(p.t):
        end = min(len(p.t), start + MAX_T)
        sub_t = p.t[start:end]
        used = np.unique(sub_t)
        while len(used) > MAX_V:
            end = start + (end - start) // 2
            sub_t = p.t[start:end]
            used = np.unique(sub_t)
        remap = -np.ones(len(p.v), dtype=np.int64)
        remap[used] = np.arange(len(used))
        out.append(Piece(p.v[used], remap[sub_t], p.n[used], p.c[used], p.uv[used]))
        start = end
    return out


def encode_chunk(region: Region, anchor: np.ndarray, meshes: list[tuple[int, int, Piece]], trees: list, props: list, colls: list) -> bytes:
    out = bytearray()
    out += b"GBM2"
    out += struct.pack("<HfffHHHH", 2, *anchor.tolist(), len(meshes), len(trees), len(props), len(colls))
    for gid, lod, p in meshes:
        vs = bng_to_studs(region, p.v) - anchor
        ns = bng_dir_to_studs(p.n)
        q = np.clip(np.round(vs * 32), -32767, 32767).astype("<i2")
        nq = np.clip(np.round(ns * 127), -127, 127).astype("i1")
        cq = np.clip(np.round(p.c * 255), 0, 255).astype("u1")
        uq = np.clip(np.round(p.uv * 64), -32767, 32767).astype("<i2")
        out += struct.pack("<BBHH", gid, lod, len(p.v), len(p.t))
        rec = np.zeros(len(p.v), dtype=[("p", "<i2", 3), ("n", "i1", 3), ("c", "u1", 3), ("uv", "<i2", 2)])
        rec["p"], rec["n"], rec["c"], rec["uv"] = q, nq, cq, uq
        out += rec.tobytes()
        out += p.t.astype("<u2").tobytes()
    for t in trees:
        x, y, z = (bng_to_studs(region, np.array([[t.x, t.y, t.ground]]))[0] - anchor)
        out += struct.pack("<hhhBBBH", int(round(x * 4)), int(round(y * 4)), int(round(z * 4)), int(np.clip(t.height * 4, 0, 255)), int(np.clip(t.crown * 10, 0, 255)), t.kind, t.seed)
    for p in props:
        x, y, z = (bng_to_studs(region, np.array([[p.x, p.y, p.z]]))[0] - anchor)
        # BNG yaw (0 = north, clockwise) -> Roblox yaw about +Y (0 = -Z = north).
        yaw = int(round((p.yaw % (2 * np.pi)) / (2 * np.pi) * 256)) % 256
        out += struct.pack("<BhhhBB", p.kind, int(round(x * 8)), int(round(y * 8)), int(round(z * 8)), yaw, p.variant % 256)
    for c in colls:
        cx, cy, cz = (bng_to_studs(region, np.array([c["c"]]))[0] - anchor)
        sx, sz, sy = (np.array(c["size"]) / PARAMS.metres_per_stud)
        u = np.array(c["u"])
        yaw = int(round((np.arctan2(u[0], u[1]) % (2 * np.pi)) / (2 * np.pi) * 256)) % 256
        out += struct.pack("<hhhHHHB", int(round(cx * 8)), int(round(cy * 8)), int(round(cz * 8)), int(min(65535, sx * 8)), int(min(65535, sy * 8)), int(min(65535, sz * 8)), yaw)
    return bytes(out)


def encode_terrain(x0: int, z0: int, h: np.ndarray, mat: np.ndarray, water: np.ndarray) -> bytes:
    nx, nz = h.shape
    out = bytearray(b"GBT1")
    out += struct.pack("<HiiHH", 1, x0, z0, nx, nz)
    ys = np.clip(np.round((h - PARAMS.h0) / PARAMS.metres_per_stud * 8), 0, 65535).astype("<u2")
    ws = np.where(np.isnan(water), 0, np.clip(np.round((water - PARAMS.h0) / PARAMS.metres_per_stud * 8), 1, 65535)).astype("<u2")
    rec = np.zeros(nx * nz, dtype=[("h", "<u2"), ("m", "u1"), ("w", "<u2")])
    rec["h"], rec["m"], rec["w"] = ys.ravel(), mat.ravel(), ws.ravel()
    out += rec.tobytes()
    return bytes(out)


def pack(blob: bytes) -> str:
    comp = zstandard.ZstdCompressor(level=19).compress(blob)
    return base64.b64encode(comp).decode("ascii")


def write_module(path: Path, payload: str, comment: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Long-bracket string; base64 never contains ']'.
    path.write_text(f"-- {comment}\n-- Generated by pipeline/geoblox (do not edit). zstd + base64.\nreturn [==[{payload}]==]\n")


def lua_value(v, indent: str = "") -> str:
    if isinstance(v, dict):
        inner = indent + "\t"
        items = []
        for k, val in v.items():
            key = k if isinstance(k, str) and k.isidentifier() else f"[{lua_value(k)}]"
            items.append(f"{inner}{key} = {lua_value(val, inner)},")
        return "{\n" + "\n".join(items) + f"\n{indent}}}"
    if isinstance(v, (list, tuple)):
        if all(isinstance(x, (int, float)) for x in v) and len(v) <= 8:
            return "{ " + ", ".join(lua_value(x) for x in v) + " }"
        inner = indent + "\t"
        return "{\n" + "\n".join(f"{inner}{lua_value(x, inner)}," for x in v) + f"\n{indent}}}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return repr(round(float(v), 4))
    if v is None:
        return "nil"
    return json.dumps(str(v), ensure_ascii=False)
