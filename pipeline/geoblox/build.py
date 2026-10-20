"""End-to-end build: data -> reconstruction -> Roblox parts, terrain and the
manifest.

    python -m geoblox.build            # full build
    python -m geoblox.build --preview  # also render preview images

Outputs:
  world/*.gbw                    part chunks (baked into the place by tools/bake.luau)
  src/generated/Manifest.luau    viewpoints, spawn, landmark parameters
  src/generated_server/Terrain   terrain columns (written by the server at start)
  build/report.json              statistics
"""

from __future__ import annotations

import argparse
import json
import shutil
import time

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from . import bridges as bridges_mod
from . import buildings as bl
from . import buildparts as bp
from . import export, ground as ground_mod, landmarks, props as props_mod, streets, trees as trees_mod
from .config import BUILD, GENERATED, PARAMS, ROOT, WESTMINSTER, Region
from .partmap import Parts
from .scene import load
from .surface import Surface
from .world import World, write

WORLD_DIR = ROOT / "world"
PREFAB_NAMES = {v: k for k, v in props_mod.TYPES.items()}
TREE_KINDS = {0: "plane", 1: "broadleaf", 2: "conifer", 3: "ornamental"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def stud_to_bng(region: Region, p: np.ndarray) -> tuple[float, float]:
    e0, n0 = region.origin
    return float(p[0] * PARAMS.metres_per_stud + e0), float(n0 - p[2] * PARAMS.metres_per_stud)


def distribute(world: World, P: Parts, kind: str, name: str) -> None:
    """Split loose parts (kerbs, markings, walls) into one model per chunk."""
    by_chunk: dict = {}
    for row in P.rows:
        e, n = stud_to_bng(world.region, row[1])
        by_chunk.setdefault(world.chunk_of(e, n), []).append((row, e, n))
    for (ci, cj), rows in by_chunk.items():
        e = float(np.mean([r[1] for r in rows]))
        n = float(np.mean([r[2] for r in rows]))
        m = world.model(name, kind, e, n)
        # Keep the model in the chunk the parts were binned into.
        e0, n0, _, _ = world.region.bbox
        cm = PARAMS.chunk_m
        m.e, m.n = e0 + (ci + 0.5) * cm, n0 + (cj + 0.5) * cm
        m.parts.rows.extend(r[0] for r in rows)


def run(region: Region, preview: bool) -> dict:
    t0 = time.time()
    report: dict = {"region": region.name, "bbox_bng": region.bbox}
    scene = load(region)
    log(f"scene loaded; transform: {scene.proj.description}")
    eye = landmarks.london_eye(scene)
    masked = landmarks.mask_eye(scene)
    log(f"London Eye: wheel plane from {eye['rim_returns'] if eye else 0} LiDAR returns; {masked} cells masked")
    world = World(region)

    # ------------------------------------------------------------ buildings
    blds = bl.collect(scene)
    for i, b in enumerate(blds):
        if b.mode == "s3db":
            b.footprint = bp.clean_outline(b.outline)
            b.ground = bp.ground_level(scene, b.footprint)
            hs = [h for h in (bl.part_heights(t) for _, t in b.parts) if h]
            b.height = max((h[1] for h in hs), default=10.0)
        else:
            bp.prepare(scene, b)
        b.style = bl.infer_style(b.tags, b.height, b.key)
        if i % 200 == 0:
            log(f"  reconstructed {i}/{len(blds)}")
    blds = [b for b in blds if b.mode == "s3db" or b.lidar_ok]

    # ------------------------------------------------------------ ground (needed by the buildings for passages)
    footprints = [b.footprint for b in blds]
    gnd = ground_mod.build(scene, footprints)
    river = unary_union([w.poly for w in gnd.water if w.kind == "river"]) if gnd.water else None
    deck_polys = [f.geom for f in scene.osm.areas if f.tags.get("man_made") == "bridge" and river is not None and f.geom.intersects(river)]
    surface = Surface(scene, gnd, unary_union(footprints), deck_polys)
    brs = bridges_mod.find(scene, gnd, surface)
    deck_fn = bridges_mod.deck_height_fn(brs)
    passages = unary_union([ground_mod.road_polygon(r) for r in gnd.roads if r.passage]).buffer(0.3)
    log(f"ground: {len(gnd.roads)} road stretches ({sum(r.passage for r in gnd.roads)} through passages), {len(gnd.deck_roads)} elevated, {len(gnd.kerb_lines)} kerb runs, {len(brs)} bridge decks; Thames {scene.water_level:.2f} m ODN")
    report["ground"] = {
        "roads": len(gnd.roads),
        "passages": sum(r.passage for r in gnd.roads),
        "elevated_stretches": len(gnd.deck_roads),
        "carriageway_m2": round(gnd.carriageway.intersection(scene.clip).area),
        "hardscape_m2": round(gnd.hardscape.intersection(scene.clip).area),
        "green_m2": round(gnd.green.intersection(scene.clip).area),
        "thames_level_m_odn": round(scene.water_level, 2),
        "bridges": [b.name or "unnamed" for b in brs],
        "width_from_kerbs_fraction": round(float(np.mean([r.measured for r in gnd.roads])) if gnd.roads else 0.0, 3),
    }

    # ------------------------------------------------------------ buildings as parts
    exposure = bp.Exposure(scene, blds)
    tower = next((f.geom for f in scene.osm.areas if f.tags.get("name") == "Elizabeth Tower" and "building" in f.tags), None)
    dial_zone = tower.buffer(3.0) if tower is not None else None
    for b in blds:
        rp = b.footprint.representative_point()
        m = world.model(b.tags.get("name") or "Building", "building", rp.x, rp.y, style=b.style, height=round(b.height, 1), osm=str(b.key))
        through = passages if b.footprint.intersects(passages) else None
        if b.mode == "s3db":
            bp.emit_s3db_building(m.parts, b, dial_zone, through)
        else:
            bp.emit_lidar_building(m.parts, b, exposure, through)
    log(f"buildings: {len(blds)} ({sum(b.mode == 's3db' for b in blds)} mapped landmarks), {sum(len(m.parts) for m in world.models)} parts")
    report["buildings"] = {
        "total": len(blds),
        "s3db": sum(b.mode == "s3db" for b in blds),
        "lidar": sum(b.mode == "lidar" for b in blds),
        "courtyards_detected": sum(b.courtyards for b in blds),
        "roof_faces": sum(len(b.roof.faces) for b in blds if b.roof is not None),
        "roof_planes_detected": sum(b.roof.planes_detected for b in blds if b.roof is not None),
    }

    street_parts = Parts(region)
    n_kerb = surface.kerb_parts(street_parts)

    def road_z(xy):
        return surface.road(np.atleast_2d(xy))

    marks = Parts(region)
    crossings = streets.emit(marks, scene, gnd, road_z, deck_fn)
    street_parts.extend(marks)
    walls = Parts(region)
    n_emb = bridges_mod.embankment(walls, scene, gnd, surface, brs)
    street_parts.extend(walls)
    distribute(world, street_parts, "streets", "Streets")
    for b in brs:
        m = world.model(b.name or "Bridge", "bridge", *b.axis_c)
        bridges_mod.emit_one(m.parts, scene, gnd, b)
    via = Parts(region)
    bridges_mod.viaducts(via, gnd, brs)
    distribute(world, via, "bridge", "Viaduct")
    log(f"streets: {n_kerb} kerb parts, {len(marks)} marking parts, {n_emb} embankment walls, {len(crossings)} crossings")

    # ------------------------------------------------------------ trees, furniture, landmarks
    no_trees = unary_union([w.poly for w in gnd.water] + [b.poly for b in brs] + [gnd.carriageway.buffer(-1.5)])
    trees = trees_mod.detect(scene, unary_union(footprints), no_trees)
    if eye is not None:
        e0, n0 = region.origin
        hx, hz = eye["hub"][0] * PARAMS.metres_per_stud + e0, n0 - eye["hub"][2] * PARAMS.metres_per_stud
        trees = [t for t in trees if not (np.hypot(t.x - hx, t.y - hz) < 75 and t.height > 20)]

    def deck_at(x, y):
        for b in brs:
            if b.poly.contains(Point(x, y)):
                return float(b.deck_z(np.array([[x, y]]))[0]) + 0.12
        return None

    props = props_mod.collect(scene, gnd, crossings, surface, deck_at)
    S = Parts(region)
    tree_models: dict = {}
    for t in trees:
        z = float(surface.top(np.array([[t.x, t.y]]))[0])
        p = S.S(np.array([t.x, t.y, z]))
        ck = world.chunk_of(t.x, t.y)
        if ck not in tree_models:
            e0, n0, _, _ = region.bbox
            tree_models[ck] = world.model("Trees", "trees", e0 + (ck[0] + 0.5) * PARAMS.chunk_m, n0 + (ck[1] + 0.5) * PARAMS.chunk_m)
        tree_models[ck].prefabs.append(["tree_" + TREE_KINDS[t.kind], round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3), 0.0, round(t.height, 2), round(t.crown, 2), int(t.seed)])
    prop_models: dict = {}
    for pr in props:
        p = S.S(np.array([pr.x, pr.y, pr.z]))
        ck = world.chunk_of(pr.x, pr.y)
        if ck not in prop_models:
            e0, n0, _, _ = region.bbox
            prop_models[ck] = world.model("Props", "prop", e0 + (ck[0] + 0.5) * PARAMS.chunk_m, n0 + (ck[1] + 0.5) * PARAMS.chunk_m)
        prop_models[ck].prefabs.append([PREFAB_NAMES[pr.kind], round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3), round(float(pr.yaw), 4), int(pr.variant)])
    dials = landmarks.big_ben_dials(scene)
    if eye is not None:
        e0, n0 = region.origin
        hx, hz = eye["hub"][0] * PARAMS.metres_per_stud + e0, n0 - eye["hub"][2] * PARAMS.metres_per_stud
        m = world.model("LondonEye", "landmark", hx, hz, hub=eye["hub"], plane=eye["plane"], period=1800)
        landmarks.eye_parts(m.parts, eye)
    for i, d in enumerate(dials):
        cx, cz = stud_to_bng(region, np.array(d["center"]))
        m = world.model(f"ClockDial{i + 1}", "landmark", cx, cz, center=d["center"], normal=d["normal"], tag="GeoBloxClockDial")
        landmarks.dial_parts(m.parts, d)
    log(f"trees: {len(trees)}, props: {len(props)}, dials: {len(dials)}, eye: {eye is not None}")
    report["trees"] = len(trees)
    report["props"] = len(props)

    # ------------------------------------------------------------ write world chunks
    index = write(world, WORLD_DIR)
    stats = world.stats()
    log(f"world: {len(index)} chunks, {stats['total_parts']} parts, {stats['total_prefabs']} prefab placements, {sum(c['bytes'] for c in index) / 1e6:.1f} MB")
    report["world"] = stats
    report["world_mb"] = round(sum(c["bytes"] for c in index) / 1e6, 2)

    # ------------------------------------------------------------ terrain
    ext = (-2432.0, -2560.0, 2432.0, 2560.0)
    cols = surface.terrain_columns(ext, [(b.poly, b.deck_z) for b in brs])
    terr_dir = ROOT / "src" / "generated_server" / "Terrain"
    if terr_dir.exists():
        shutil.rmtree(terr_dir)
    tchunks = []
    tbytes = 0
    nx, nz = cols["nx"], cols["nz"]
    for ix in range(0, nx, 64):
        for iz in range(0, nz, 64):
            h = cols["height"][ix : ix + 64, iz : iz + 64]
            mtl = cols["material"][ix : ix + 64, iz : iz + 64]
            w = cols["water"][ix : ix + 64, iz : iz + 64]
            x0 = int(cols["x0"] + ix * 4)
            z0 = int(cols["z0"] + iz * 4)
            payload = export.pack(export.encode_terrain(x0, z0, h, mtl, w))
            name = f"t{ix // 64:02d}_{iz // 64:02d}"
            export.write_module(terr_dir / f"{name}.luau", payload, f"GeoBlox terrain chunk {name}")
            tbytes += len(payload)
            tchunks.append({"name": name, "x0": x0, "z0": z0})
    log(f"terrain chunks: {len(tchunks)}, {tbytes / 1e6:.1f} MB encoded")
    from . import drivecheck

    drive = drivecheck.run(world, gnd, cols)
    report["drivability"] = drive
    log(f"drivability: {drive['samples']} road samples, {drive['obstacle_share'] * 100:.2f}% blocked {drive['obstacles_by_kind']}, worst step {drive['worst_step_m']} m at {drive['worst_step_at']}")

    # ------------------------------------------------------------ manifest
    def sp(e, n, h_above=1.0):
        z = deck_at(e, n)
        if z is None:
            z = float(surface.top(np.array([[e, n]]))[0])
        return export.bng_to_studs(region, np.array([[e, n, z + h_above]]))[0].tolist()

    viewpoints = {
        "Westminster Bridge": sp(530420, 179668, 1.2),
        "Parliament Square": sp(530075, 179585, 1.2),
        "Whitehall": sp(530160, 180020, 1.2),
        "Trafalgar Square": sp(530000, 180390, 1.2),
        "South Bank (London Eye)": sp(530700, 179900, 1.2),
        "Victoria Embankment": sp(530430, 180050, 1.2),
    }
    spawn_pos, spawn_dir = spawn_on_road(region, gnd, surface, deck_fn, (530540, 179672))
    manifest = {
        "version": 3,
        "region": region.name,
        "metresPerStud": PARAMS.metres_per_stud,
        "h0": PARAMS.h0,
        "chunkStuds": PARAMS.chunk_m / PARAMS.metres_per_stud,
        "waterLevelY": (scene.water_level - PARAMS.h0) / PARAMS.metres_per_stud,
        "terrain": tchunks,
        "viewpoints": viewpoints,
        "spawn": spawn_pos,
        "spawnLook": spawn_dir,
        "londonEye": eye or {},
        "attribution": "Contains Environment Agency LiDAR data © Environment Agency copyright and/or database right 2022 (OGL v3). Map data © OpenStreetMap contributors (ODbL).",
    }
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    GENERATED.mkdir(parents=True, exist_ok=True)
    (GENERATED / "Manifest.luau").write_text(
        "-- Generated by pipeline/geoblox (do not edit).\n--!strict\nreturn " + export.lua_value(manifest) + "\n"
    )
    report["seconds"] = round(time.time() - t0, 1)
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "report.json").write_text(json.dumps(report, indent=2))
    log(f"done in {report['seconds']} s")

    if preview:
        from . import preview as preview_mod

        rows = [row for m in world.models for row in m.parts.rows]
        meshes = [preview_mod.parts_mesh(region, rows), preview_mod.terrain_mesh(cols, region), preview_mod.tree_mesh(region, world)]
        preview_mod.render_views(meshes)
    return report


def spawn_on_road(region: Region, gnd, surface, deck_fn, near: tuple[float, float]):
    """A spawn on the carriageway nearest `near`, facing along the road."""
    best = None
    for r in list(gnd.roads) + list(gnd.deck_roads or []):
        if r.kind in ("service", "cycleway"):
            continue
        d = np.linalg.norm(r.stations - np.asarray(near), axis=1)
        i = int(np.argmin(d))
        if best is None or d[i] < best[0]:
            best = (float(d[i]), r, i)
    _, r, i = best
    p = r.stations[i]
    deck = r in (gnd.deck_roads or [])
    z = float(deck_fn(r, p[None])[0]) if deck else float(surface.road(p[None])[0])
    # Face west (towards Westminster from the bridge) and keep left, as in the UK.
    look = r.tangents[i] if r.tangents[i][0] < 0 else -r.tangents[i]
    left = np.array([-look[1], look[0]])
    pos = p + left * (0.5 * min(r.left[i], r.right[i]) if not r.oneway else 0.0)
    s = export.bng_to_studs(region, np.array([[pos[0], pos[1], z + 1.5]]))[0]
    return s.tolist(), [float(look[0]), 0.0, float(-look[1])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    run(WESTMINSTER, args.preview)


if __name__ == "__main__":
    main()
