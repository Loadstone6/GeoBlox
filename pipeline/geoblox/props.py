"""Street furniture records (instanced by the Roblox client from parametric models)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Point

from .ground import Ground, drape
from .scene import Scene

TYPES = {
    "lamp": 0, "bench": 1, "bin": 2, "bollard": 3, "post_box": 4, "phone_k6": 5, "bus_stop": 6, "shelter": 7,
    "signal": 8, "belisha": 9, "cycle_stand": 10, "cycle_hire": 11, "statue": 12, "flagpole": 13, "info": 14,
    "phone_modern": 15, "lamp_embankment": 16,
}


@dataclass
class Prop:
    kind: int
    x: float
    y: float
    z: float
    yaw: float  # radians, rotation about up; 0 = facing +y (north)
    variant: int


def _facing(ground: Ground, x: float, y: float) -> float:
    """Yaw that turns an object's front towards the nearest carriageway."""
    car = ground.carriageway
    if car.is_empty:
        return 0.0
    p = Point(x, y)
    q = shapely.ops.nearest_points(p, car.boundary)[1] if not car.contains(p) else p
    d = np.array([q.x - x, q.y - y])
    if np.linalg.norm(d) < 1e-6:
        return 0.0
    return float(np.arctan2(d[0], d[1]))


def collect(scene: Scene, ground: Ground, crossings: list[dict]) -> list[Prop]:
    import shapely.ops  # noqa: F401

    props: list[Prop] = []
    clip = scene.clip

    def z_at(x, y):
        return float(drape(scene, ground, np.array([[x, y]]), 0.0)[0] + (0.18 if ground.hardscape.contains(Point(x, y)) else 0.0))

    def add(kind: str, x: float, y: float, yaw: float | None = None, variant: int = 0):
        if not clip.contains(Point(x, y)):
            return
        props.append(Prop(TYPES[kind], x, y, z_at(x, y), _facing(ground, x, y) if yaw is None else yaw, variant))

    lamps_xy = []
    for p in scene.osm.points:
        t = p.tags
        x, y = p.geom.x, p.geom.y
        a, hw, b = t.get("amenity"), t.get("highway"), t.get("barrier")
        if hw == "street_lamp":
            add("lamp", x, y)
            lamps_xy.append((x, y))
        elif a == "bench":
            add("bench", x, y)
        elif a == "waste_basket":
            add("bin", x, y)
        elif b == "bollard":
            add("bollard", x, y)
        elif a == "post_box":
            add("post_box", x, y)
        elif a == "telephone":
            booth = t.get("booth", "")
            add("phone_k6" if booth in ("K6", "K2", "K8", "") and t.get("covered") != "no" else "phone_modern", x, y)
        elif hw == "bus_stop":
            add("shelter" if t.get("shelter") == "yes" else "bus_stop", x, y)
        elif a == "bicycle_parking":
            add("cycle_stand", x, y)
        elif a == "bicycle_rental":
            add("cycle_hire", x, y)
        elif t.get("man_made") == "flagpole":
            add("flagpole", x, y, 0.0)
        elif t.get("historic") in ("memorial", "monument") or t.get("tourism") == "artwork":
            if t.get("memorial") in ("plaque", "blue_plaque", "stolperstein") or t.get("artwork_type") in ("mural", "graffiti"):
                continue
            add("statue", x, y, None, int(abs(hash(t.get("name", ""))) % 4))
        elif t.get("tourism") == "information" and t.get("information") in ("board", "map"):
            add("info", x, y)
        elif hw == "traffic_signals":
            pass  # handled from crossings below (poles at the kerbs)

    # Signal heads / Belisha beacons at both kerbs of each crossing.
    for c in crossings:
        for sgn in (1, -1):
            pos = c["c"] + c["across"] * sgn * (c["half"] + 0.7)
            yaw = float(np.arctan2(-sgn * c["across"][0], -sgn * c["across"][1]))
            add("belisha" if c["kind"] == "zebra" else "signal", float(pos[0]), float(pos[1]), yaw)

    # Procedural lamp columns along kerbed roads where OSM has none nearby.
    tree = shapely.STRtree([Point(xy) for xy in lamps_xy]) if lamps_xy else None
    for r in ground.roads:
        if r.kind in ("service", "cycleway"):
            continue
        seg = np.linalg.norm(np.diff(r.stations, axis=0), axis=1)
        s = np.concatenate([[0], np.cumsum(seg)])
        k = 0
        for d in np.arange(12.0, s[-1], 30.0):
            k += 1
            i = int(np.searchsorted(s, d))
            i = min(i, len(r.stations) - 1)
            side = 1 if k % 2 else -1
            n = np.array([-r.tangents[i, 1], r.tangents[i, 0]]) * side
            half = r.left[i] if side == 1 else r.right[i]
            pos = r.stations[i] + n * (half + 0.55)
            pt = Point(pos)
            if not ground.hardscape.contains(pt):
                continue
            if tree is not None and len(tree.query(pt.buffer(16.0))):
                continue
            yaw = float(np.arctan2(-n[0], -n[1]))
            add("lamp", float(pos[0]), float(pos[1]), yaw, 1)
    return props
