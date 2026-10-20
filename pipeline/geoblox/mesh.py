"""Mesh accumulation: grouped, chunked triangle soups with normals, colours, UVs.

Geometry is accumulated in BNG metres (x = easting, y = northing, z = ODN
height) and only converted to Roblox studs on export.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .config import PARAMS, Region


@dataclass
class Piece:
    v: np.ndarray  # (n, 3) float64 BNG metres
    t: np.ndarray  # (m, 3) int
    n: np.ndarray  # (n, 3) unit normals
    c: np.ndarray  # (n, 3) float colour 0..1
    uv: np.ndarray  # (n, 2)


@dataclass
class GroupBuffer:
    pieces: list[Piece] = field(default_factory=list)

    def merged(self) -> Piece:
        vs, ts, ns, cs, uvs = [], [], [], [], []
        off = 0
        for p in self.pieces:
            vs.append(p.v)
            ts.append(p.t + off)
            ns.append(p.n)
            cs.append(p.c)
            uvs.append(p.uv)
            off += len(p.v)
        if not vs:
            z3 = np.zeros((0, 3))
            return Piece(z3, np.zeros((0, 3), dtype=np.int64), z3, z3, np.zeros((0, 2)))
        return Piece(np.vstack(vs), np.vstack(ts), np.vstack(ns), np.vstack(cs), np.vstack(uvs))

    @property
    def triangles(self) -> int:
        return sum(len(p.t) for p in self.pieces)


def face_normals(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(ln, 1e-12)


def orient_up(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Flip triangles so their normals point up (+z)."""
    n = face_normals(v, t)
    t = t.copy()
    flip = n[:, 2] < 0
    t[flip] = t[flip][:, [0, 2, 1]]
    return t


def crease_split(v: np.ndarray, t: np.ndarray, crease_deg: float = 25.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Duplicate vertices along creases; smooth normals within smoothing groups.

    Returns (vertices, triangles, normals, source_index) where source_index maps
    each new vertex to the original vertex.
    """
    fn = face_normals(v, t)
    cos_c = np.cos(np.radians(crease_deg))
    incident: dict[int, list[int]] = defaultdict(list)
    for fi, tri in enumerate(t):
        for k in range(3):
            incident[int(tri[k])].append(fi)
    new_v, new_n, src = [], [], []
    corner_map = np.zeros_like(t)
    for vi, faces in incident.items():
        clusters: list[list[int]] = []
        for f in faces:
            for cl in clusters:
                if np.dot(fn[cl[0]], fn[f]) >= cos_c:
                    cl.append(f)
                    break
            else:
                clusters.append([f])
        for cl in clusters:
            nn = fn[cl].sum(axis=0)
            nn /= max(np.linalg.norm(nn), 1e-12)
            idx = len(new_v)
            new_v.append(v[vi])
            new_n.append(nn)
            src.append(vi)
            for f in cl:
                tri = t[f]
                k = int(np.where(tri == vi)[0][0])
                corner_map[f, k] = idx
    return np.array(new_v), corner_map, np.array(new_n), np.array(src)


class Collector:
    """Accumulates pieces keyed by (chunk, lod, group)."""

    def __init__(self, region: Region):
        self.region = region
        self.groups: dict[tuple[tuple[int, int], int, str], GroupBuffer] = defaultdict(GroupBuffer)

    def chunk_of(self, x: float, y: float) -> tuple[int, int]:
        e0, n0, _, _ = self.region.bbox
        cm = PARAMS.chunk_m
        return (int(np.floor((x - e0) / cm)), int(np.floor((y - n0) / cm)))

    def add(self, chunk: tuple[int, int], lod: int, group: str, piece: Piece) -> None:
        if len(piece.t) == 0:
            return
        self.groups[(chunk, lod, group)].pieces.append(piece)

    def add_by_centroid(self, lods: tuple[int, ...], group: str, piece: Piece) -> None:
        """Split a piece into chunks by triangle centroid (for ground meshes)."""
        if len(piece.t) == 0:
            return
        cent = piece.v[piece.t].mean(axis=1)
        keys = np.array([self.chunk_of(x, y) for x, y in cent[:, :2]])
        uniq = {tuple(k) for k in keys}
        for k in uniq:
            m = np.all(keys == np.array(k), axis=1)
            sub_t = piece.t[m]
            used = np.unique(sub_t)
            remap = -np.ones(len(piece.v), dtype=np.int64)
            remap[used] = np.arange(len(used))
            sub = Piece(piece.v[used], remap[sub_t], piece.n[used], piece.c[used], piece.uv[used])
            for lod in lods:
                self.add(k, lod, group, sub)

    def stats(self) -> dict:
        out: dict = defaultdict(int)
        for (chunk, lod, group), buf in self.groups.items():
            out[(lod, group)] += buf.triangles
        return dict(out)


def quad(p0, p1, p2, p3, normal, color, uv) -> Piece:
    """Planar quad p0-p1-p2-p3 (counter-clockwise seen from `normal`)."""
    v = np.array([p0, p1, p2, p3], dtype=np.float64)
    t = np.array([[0, 1, 2], [0, 2, 3]])
    nrm = np.tile(np.asarray(normal, dtype=np.float64), (4, 1))
    c = np.tile(np.asarray(color, dtype=np.float64), (4, 1)) if np.ndim(color) == 1 else np.asarray(color, dtype=np.float64)
    return Piece(v, t, nrm, c, np.asarray(uv, dtype=np.float64))


def merge(pieces: list[Piece]) -> Piece:
    buf = GroupBuffer(pieces)
    return buf.merged()
