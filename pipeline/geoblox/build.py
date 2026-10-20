"""End-to-end build: data -> reconstruction -> chunked Roblox modules + report.

    python -m geoblox.build            # full build
    python -m geoblox.build --preview  # also render preview images
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import Point
from shapely.ops import unary_union

from . import ao as ao_mod
from . import bridges as bridges_mod
from . import buildings as bl
from . import export, ground as ground_mod, landmarks, props as props_mod, streets, trees as trees_mod
from .config import BUILD, GENERATED, PARAMS, ROOT, WESTMINSTER, Region
from .mesh import Collector
from .scene import load

# Order defines the group ids written into the binary chunks.
GROUPS = [
    "road", "pavement", "kerb", "embankment", "bridge",
    "marking_white", "marking_yellow", "marking_red",
    "roof_pitched", "roof_flat", "roof_metal", "roof_glass", "gilt",
    "wall_brick", "wall_stone", "wall_concrete", "wall_plaster", "wall_glass", "wall_metal",
    "window_frame", "window_glass", "curtain_glass", "window_lit", "shopfront",
]

ALL_LODS = 2  # lod value meaning "shown at every level of detail"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def building_collisions(b: bl.Building) -> list[dict]:
    """Thin wall boxes along the outline (walk-around collision near the player)."""
    out = []
    ring = np.asarray(b.outline.exterior.coords)
    top = b.ground + max(3.0, b.height)
    for i in range(len(ring) - 1):
        a, c = ring[i], ring[i + 1]
        seg = c - a
        L = float(np.linalg.norm(seg))
        if L < 0.6:
            continue
        u = seg / L
        n = np.array([u[1], -u[0]])
        center = (a + c) / 2 - n * 0.3
        out.append({"c": [center[0], center[1], (b.ground + top) / 2], "u": u.tolist(), "size": [L + 0.3, 0.6, top - b.ground]})
    return out


def run(region: Region, preview: bool) -> dict:
    t0 = time.time()
    report: dict = {"region": region.name, "bbox_bng": region.bbox}
    scene = load(region)
    log(f"scene loaded; transform: {scene.proj.description}")
    eye = landmarks.london_eye(scene)
    masked = landmarks.mask_eye(scene)
    log(f"London Eye: wheel plane from {eye['rim_returns'] if eye else 0} LiDAR returns; {masked} cells masked")

    # ------------------------------------------------------------ buildings
    blds = bl.collect(scene)
    log(f"buildings: {len(blds)} ({sum(b.mode == 's3db' for b in blds)} mapper-modelled landmarks)")
    for i, b in enumerate(blds):
        bl.reconstruct(scene, b, PARAMS.roof_eps_lod0, PARAMS.roof_max_vertices, 0)
        b.style = bl.infer_style(b.tags, b.height, b.key)
        if i % 200 == 0:
            log(f"  reconstructed {i}/{len(blds)}")
    blds = [b for b in blds if b.mode == "s3db" or b.lidar_ok]
    bl.unify_seams(blds)
    lod1 = []
    for b in blds:
        c = bl.Building(b.key, b.tags, b.outline, [], b.retail)
        c.style = b.style
        bl.reconstruct(scene, c, PARAMS.roof_eps_lod1, 24, 1)
        lod1.append(c)
    log(f"  kept {len(blds)} buildings with LiDAR evidence; courtyards cut: {sum(b.courtyards for b in blds)}")
    report["buildings"] = {
        "total": len(blds),
        "s3db": sum(b.mode == "s3db" for b in blds),
        "lidar": sum(b.mode == "lidar" for b in blds),
        "courtyards_detected": sum(b.courtyards for b in blds),
        "roof_planes": sum(f.tin.planes for b in blds for f in b.faces if f.tin is not None),
    }

    # ------------------------------------------------------------ ground
    building_polys = [b.outline for b in blds]
    gnd = ground_mod.build(scene, building_polys)
    brs = bridges_mod.find(scene, gnd)
    bridges_mod.register_decks(gnd, brs)
    log(f"ground: {len(gnd.roads)} road ways, {len(gnd.kerb_lines)} kerb runs, {len(gnd.water)} water bodies, {len(brs)} bridges; Thames level {scene.water_level:.2f} m ODN")
    report["ground"] = {
        "roads": len(gnd.roads),
        "carriageway_m2": round(gnd.carriageway.intersection(scene.clip).area),
        "hardscape_m2": round(gnd.hardscape.intersection(scene.clip).area),
        "green_m2": round(gnd.green.intersection(scene.clip).area),
        "thames_level_m_odn": round(scene.water_level, 2),
        "bridges": [b.name or "unnamed" for b in brs],
        "width_from_kerbs_fraction": round(float(np.mean([r.measured for r in gnd.roads])) if gnd.roads else 0.0, 3),
    }

    col = Collector(region)
    idx0 = bl.FaceIndex(blds)
    idx1 = bl.FaceIndex(lod1)
    rng = np.random.default_rng(7)
    for b in blds:
        chunk = col.chunk_of(*b.outline.representative_point().coords[0])
        bl.emit_building(col, chunk, 0, b, idx0, ao_mod.no_ao, rng)
    for b in lod1:
        chunk = col.chunk_of(*b.outline.representative_point().coords[0])
        bl.emit_building(col, chunk, 1, b, idx1, ao_mod.no_ao, rng)
    log("buildings emitted")

    ground_mod.emit(col, scene, gnd)
    bridge_colls = bridges_mod.emit(col, scene, gnd, brs)
    crossings = streets.emit(col, scene, gnd)
    log(f"streets: {len(crossings)} crossings marked")

    # Shared-LOD groups are stored once.
    merged: dict = {}
    for (chunk, lod, group), buf in list(col.groups.items()):
        if group in ("road", "pavement", "embankment", "bridge") and lod == 0:
            merged[(chunk, ALL_LODS, group)] = buf
    for (chunk, lod, group) in list(col.groups.keys()):
        if group in ("road", "pavement", "embankment", "bridge"):
            del col.groups[(chunk, lod, group)]
    col.groups.update(merged)

    ao = ao_mod.AmbientOcclusion(scene.dsm)
    ao_mod.bake(col, ao, skip=("window_lit", "marking_white", "marking_yellow", "marking_red"))
    log("ambient occlusion baked")

    no_trees = unary_union([w.poly for w in gnd.water] + [b.poly for b in brs] + [gnd.carriageway.buffer(-1.5)])
    trees = trees_mod.detect(scene, unary_union(building_polys), no_trees)
    if eye is not None:
        e0, n0 = region.origin
        hx, hz = eye["hub"][0] * PARAMS.metres_per_stud + e0, n0 - eye["hub"][2] * PARAMS.metres_per_stud
        trees = [t for t in trees if not (np.hypot(t.x - hx, t.y - hz) < 75 and t.height > 20)]
    props = props_mod.collect(scene, gnd, crossings)
    dials = landmarks.big_ben_dials(scene)
    log(f"trees: {len(trees)}, props: {len(props)}, dials: {len(dials)}, eye: {eye is not None}")
    report["trees"] = len(trees)
    report["props"] = len(props)

    # ------------------------------------------------------------ chunk assembly
    per_chunk: dict = defaultdict(lambda: {"meshes": [], "trees": [], "props": [], "coll": []})
    gid = {g: i for i, g in enumerate(GROUPS)}
    tri_stats: dict = defaultdict(int)
    for (chunk, lod, group), buf in col.groups.items():
        if group not in gid:
            raise KeyError(f"unregistered group {group}")
        piece = buf.merged()
        for sub in export.split_piece(piece):
            per_chunk[chunk]["meshes"].append((gid[group], lod, sub))
            tri_stats[(lod, group)] += len(sub.t)
    for t in trees:
        per_chunk[col.chunk_of(t.x, t.y)]["trees"].append(t)
    for p in props:
        per_chunk[col.chunk_of(p.x, p.y)]["props"].append(p)
    for b in blds:
        for c in building_collisions(b):
            per_chunk[col.chunk_of(c["c"][0], c["c"][1])]["coll"].append(c)
    for c in bridge_colls:
        per_chunk[col.chunk_of(c["c"][0], c["c"][1])]["coll"].append(c)

    out_dir = GENERATED
    if out_dir.exists():
        shutil.rmtree(out_dir)
    chunk_dir = out_dir / "Chunks"
    total_bytes = 0
    manifest_chunks = []
    e0, n0, _, _ = region.bbox
    cm = PARAMS.chunk_m
    for (ci, cj), data in sorted(per_chunk.items()):
        ce = e0 + (ci + 0.5) * cm
        cn = n0 + (cj + 0.5) * cm
        anchor = export.bng_to_studs(region, np.array([[ce, cn, 0.0]]))[0]
        anchor[1] = 100.0
        blob = export.encode_chunk(region, anchor, data["meshes"], data["trees"], data["props"], data["coll"])
        payload = export.pack(blob)
        name = f"c{ci:02d}_{cj:02d}"
        export.write_module(chunk_dir / f"{name}.luau", payload, f"GeoBlox mesh chunk {name}")
        total_bytes += len(payload)
        manifest_chunks.append({"name": name, "i": ci, "j": cj, "center": [float(anchor[0]), float(anchor[1]), float(anchor[2])], "bytes": len(blob), "tris": int(sum(len(m[2].t) for m in data["meshes"]))})
    log(f"mesh chunks: {len(manifest_chunks)}, {total_bytes / 1e6:.1f} MB encoded")

    # Terrain columns, chunk-aligned (64 columns = 256 studs).
    ext = (-2432.0, -2560.0, 2432.0, 2560.0)
    cols = ground_mod.terrain_columns(scene, gnd, ext)
    terr_dir = ROOT / "src" / "generated_server" / "Terrain"
    if terr_dir.exists():
        shutil.rmtree(terr_dir)
    tchunks = []
    tbytes = 0
    nx, nz = cols["nx"], cols["nz"]
    for ix in range(0, nx, 64):
        for iz in range(0, nz, 64):
            h = cols["height"][ix : ix + 64, iz : iz + 64]
            m = cols["material"][ix : ix + 64, iz : iz + 64]
            w = cols["water"][ix : ix + 64, iz : iz + 64]
            x0 = int(cols["x0"] + ix * 4)
            z0 = int(cols["z0"] + iz * 4)
            payload = export.pack(export.encode_terrain(x0, z0, h, m, w))
            name = f"t{ix // 64:02d}_{iz // 64:02d}"
            export.write_module(terr_dir / f"{name}.luau", payload, f"GeoBlox terrain chunk {name}")
            tbytes += len(payload)
            tchunks.append({"name": name, "x0": x0, "z0": z0})
    log(f"terrain chunks: {len(tchunks)}, {tbytes / 1e6:.1f} MB encoded")

    # Spawn points / viewpoints (studs).
    def sp(e, n, h_above=1.0):
        z = float(gnd_drape(scene, gnd, e, n)) + h_above
        return export.bng_to_studs(region, np.array([[e, n, z]]))[0].tolist()

    def gnd_drape(scene, gnd, e, n):
        return ground_mod.drape(scene, gnd, np.array([[e, n]]), PARAMS.pavement_lift + PARAMS.kerb_height)[0]

    viewpoints = {
        "Westminster Bridge": sp(530420, 179668, 1.2),
        "Parliament Square": sp(530075, 179585, 1.2),
        "Whitehall": sp(530160, 180020, 1.2),
        "Trafalgar Square": sp(530000, 180390, 1.2),
        "South Bank (London Eye)": sp(530700, 179900, 1.2),
        "Victoria Embankment": sp(530430, 180050, 1.2),
    }
    manifest = {
        "version": 2,
        "region": region.name,
        "metresPerStud": PARAMS.metres_per_stud,
        "h0": PARAMS.h0,
        "chunkStuds": PARAMS.chunk_m / PARAMS.metres_per_stud,
        "waterLevelY": (scene.water_level - PARAMS.h0) / PARAMS.metres_per_stud,
        "groups": GROUPS,
        "chunks": manifest_chunks,
        "terrain": tchunks,
        "viewpoints": viewpoints,
        # On land at the east end of Westminster Bridge, looking west at Big Ben:
        # terrain gives collision immediately (bridge collision is client-side).
        "spawn": sp(530555, 179672, 1.2),
        "londonEye": eye or {},
        "bigBenDials": dials,
        "attribution": "Contains Environment Agency LiDAR data © Environment Agency copyright and/or database right 2022 (OGL v3). Map data © OpenStreetMap contributors (ODbL).",
    }
    (out_dir).mkdir(parents=True, exist_ok=True)
    (out_dir / "Manifest.luau").write_text(
        "-- Generated by pipeline/geoblox (do not edit).\n--!strict\nreturn " + export.lua_value(manifest) + "\n"
    )
    report["chunks"] = len(manifest_chunks)
    report["encoded_mb"] = round((total_bytes + tbytes) / 1e6, 2)
    report["triangles"] = {f"{k[1]}@lod{k[0]}": v for k, v in sorted(tri_stats.items(), key=lambda kv: -kv[1])}
    report["seconds"] = round(time.time() - t0, 1)
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "report.json").write_text(json.dumps(report, indent=2))
    log(f"done in {report['seconds']} s")

    if preview:
        from . import preview as preview_mod

        preview_mod.render_all(scene, col, gnd, trees, cols, region)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    run(WESTMINSTER, args.preview)


if __name__ == "__main__":
    main()
