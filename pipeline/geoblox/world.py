"""The compiled world: Roblox Models made of parts, grouped into 100 m chunks.

Every reconstructed element (a building, a chunk's street furniture, a bridge,
a landmark) becomes one Model. The kind decides how the place baker sets
streaming on it:

  building / bridge  Atomic, with a StreamingMesh impostor when out of range
  streets            Atomic per chunk (kerbs, pavement edges, road markings)
  trees              non-atomic, StreamingMesh impostor
  prop               one Atomic model per placed prefab
  landmark           Persistent (animated on every client)

Props and trees are prefab placements, built by the baker from the shared
Prefabs module, so their look can be changed without rerunning the pipeline.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import zstandard

from .config import PARAMS, Region
from .partmap import MATERIALS, PART_DTYPE, TAGS, Parts

KINDS = ("building", "streets", "bridge", "trees", "prop", "landmark", "water")


@dataclass
class Model:
    name: str
    kind: str
    e: float  # BNG anchor (decides the chunk)
    n: float
    parts: Parts
    attrs: dict = field(default_factory=dict)
    prefabs: list = field(default_factory=list)  # [name, x, y, z, yaw, scale..., params]


@dataclass
class World:
    region: Region
    models: list[Model] = field(default_factory=list)

    def model(self, name: str, kind: str, e: float, n: float, **attrs) -> Model:
        assert kind in KINDS, kind
        m = Model(name, kind, float(e), float(n), Parts(self.region), attrs)
        self.models.append(m)
        return m

    def chunk_of(self, e: float, n: float) -> tuple[int, int]:
        e0, n0, _, _ = self.region.bbox
        cm = PARAMS.chunk_m
        return int(np.floor((e - e0) / cm)), int(np.floor((n - n0) / cm))

    def stats(self) -> dict:
        out: dict = {}
        for m in self.models:
            s = out.setdefault(m.kind, {"models": 0, "parts": 0, "prefabs": 0})
            s["models"] += 1
            s["parts"] += len(m.parts)
            s["prefabs"] += len(m.prefabs)
        out["total_parts"] = sum(len(m.parts) for m in self.models)
        out["total_prefabs"] = sum(len(m.prefabs) for m in self.models)
        return out


def write(world: World, out_dir: Path) -> list[dict]:
    """One zstd file per chunk: magic "GBW1", u32 json length, json header
    (anchor in studs, models with their part ranges, prefab placements),
    u32 part count, then 52-byte part records (see partmap.PART_DTYPE)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.gbw"):
        old.unlink()
    by_chunk: dict[tuple[int, int], list[Model]] = {}
    for m in world.models:
        by_chunk.setdefault(world.chunk_of(m.e, m.n), []).append(m)
    e0, n0, _, _ = world.region.bbox
    cm = PARAMS.chunk_m
    probe = Parts(world.region)
    index = []
    cctx = zstandard.ZstdCompressor(level=19)
    for (ci, cj), models in sorted(by_chunk.items()):
        ce, cn = e0 + (ci + 0.5) * cm, n0 + (cj + 0.5) * cm
        anchor = probe.S(np.array([ce, cn, 0.0]))
        anchor[1] = 0.0
        recs = []
        header_models = []
        first = 0
        for m in models:
            r = m.parts.records(anchor)
            recs.append(r)
            header_models.append(
                {
                    "name": m.name,
                    "kind": m.kind,
                    "first": first,
                    "count": len(r),
                    "attrs": m.attrs,
                    "prefabs": m.prefabs,
                }
            )
            first += len(r)
        allrec = np.concatenate(recs) if recs else np.zeros(0, dtype=PART_DTYPE)
        name = f"c{ci:02d}_{cj:02d}"
        header = json.dumps({"name": name, "anchor": [float(v) for v in anchor], "models": header_models}, separators=(",", ":"), ensure_ascii=False).encode()
        blob = b"GBW1" + struct.pack("<I", len(header)) + header + struct.pack("<I", len(allrec)) + allrec.tobytes()
        comp = cctx.compress(blob)
        (out_dir / f"{name}.gbw").write_bytes(comp)
        index.append({"name": name, "parts": int(len(allrec)), "models": len(models), "bytes": len(comp)})
    (out_dir / "index.json").write_text(
        json.dumps({"materials": MATERIALS, "tags": TAGS, "chunks": index}, indent=1)
    )
    return index


def read(out_dir: Path, region: Region) -> World:
    """Load compiled chunks back (previews, checks). Looks carry what the
    records store: material, colour, flags, tag, transparency, reflectance."""
    from .partmap import FLAG_COLLIDE, FLAG_SHADOW, Look, matrix_from_quat

    world = World(region)
    dctx = zstandard.ZstdDecompressor()
    e0, n0 = region.origin
    for path in sorted(out_dir.glob("*.gbw")):
        raw = dctx.decompress(path.read_bytes(), max_output_size=1 << 31)
        jl = struct.unpack("<I", raw[4:8])[0]
        header = json.loads(raw[8 : 8 + jl])
        o = 8 + jl
        n = struct.unpack("<I", raw[o : o + 4])[0]
        rec = np.frombuffer(raw[o + 4 : o + 4 + n * PART_DTYPE.itemsize], dtype=PART_DTYPE)
        anchor = np.array(header["anchor"], dtype=np.float64)
        looks: dict = {}
        for m in header["models"]:
            cx = anchor[0] * PARAMS.metres_per_stud + e0
            cz = n0 - anchor[2] * PARAMS.metres_per_stud
            model = world.model(m["name"], m["kind"], cx, cz, **m["attrs"])
            model.prefabs = m["prefabs"]
            for r in rec[m["first"] : m["first"] + m["count"]]:
                key = (int(r["material"]), tuple(int(v) for v in r["color"]), int(r["flags"]), int(r["tag"]), int(r["transparency"]), int(r["reflectance"]))
                look = looks.get(key)
                if look is None:
                    look = Look(MATERIALS[key[0]], key[1], bool(key[2] & FLAG_COLLIDE), bool(key[2] & FLAG_SHADOW), key[4] / 255, key[5] / 255, TAGS[key[3]])
                    looks[key] = look
                model.parts.rows.append((int(r["shape"]), r["pos"].astype(np.float64) + anchor, matrix_from_quat(r["quat"].astype(np.float64)), r["size"].astype(np.float64), look, int(r["extra"])))
    return world
