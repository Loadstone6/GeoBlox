"""Drivability check on the compiled world: can a car run along every road?

Each road centreline is sampled every 0.5 m. At each sample we look at what
a car would meet there, using the exact solids of the baked parts:

  * the driving surface: the terrain road (ground roads) or the top of the
    bridge-deck parts (elevated stretches). We record the largest height step
    between samples;
  * obstacles: any collidable part whose solid crosses the band 0.15-2.0 m
    above the surface, such as a wall across a bridge end, a building across
    a passage, or furniture in the carriageway.

The numbers go into build/report.json. They are what a car would hit, before
anyone opens Studio.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np

from .config import PARAMS
from .ground import Ground
from .partmap import SHAPE_WEDGE

MPS = PARAMS.metres_per_stud
CELL = 24.0  # studs, spatial hash cell


class Solids:
    """Collidable parts as oriented solids with a vertical-line query (studs)."""

    def __init__(self, entries: list[tuple]):
        # entries: (shape, center, R, size, label)
        self.items = entries
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (shape, c, R, size, _) in enumerate(entries):
            half = np.abs(R) @ (np.asarray(size) / 2)
            x0, x1 = c[0] - half[0], c[0] + half[0]
            z0, z1 = c[2] - half[2], c[2] + half[2]
            for gx in range(int(np.floor(x0 / CELL)), int(np.floor(x1 / CELL)) + 1):
                for gz in range(int(np.floor(z0 / CELL)), int(np.floor(z1 / CELL)) + 1):
                    self.grid[(gx, gz)].append(i)

    def column(self, x: float, z: float) -> list[tuple[float, float, str]]:
        """Solid intervals (y0, y1, label) along the vertical line at (x, z)."""
        out = []
        for i in self.grid.get((int(np.floor(x / CELL)), int(np.floor(z / CELL))), ()):
            shape, c, R, size, label = self.items[i]
            a = R.T @ (np.array([x, 0.0, z]) - c)  # local position at y = 0
            b = R.T @ np.array([0.0, 1.0, 0.0])  # local change per unit y
            lo, hi = -np.inf, np.inf
            ok = True
            h = np.asarray(size) / 2
            for k in range(3):
                if abs(b[k]) < 1e-9:
                    if abs(a[k]) > h[k]:
                        ok = False
                        break
                    continue
                t0, t1 = (-h[k] - a[k]) / b[k], (h[k] - a[k]) / b[k]
                if t0 > t1:
                    t0, t1 = t1, t0
                lo, hi = max(lo, t0), min(hi, t1)
            if not ok or lo > hi:
                continue
            if shape == SHAPE_WEDGE:
                # Solid side of the slope: local y <= (sy / sz) * local z.
                g0 = a[1] - size[1] / size[2] * a[2]
                g1 = b[1] - size[1] / size[2] * b[2]
                if abs(g1) < 1e-12:
                    if g0 > 0:
                        continue
                elif g1 > 0:
                    hi = min(hi, -g0 / g1)
                else:
                    lo = max(lo, -g0 / g1)
                if lo > hi:
                    continue
            out.append((lo, hi, label))
        return out


def water_sampler(cols: dict):
    w = cols["water"]
    x0, z0 = cols["x0"], cols["z0"]

    def f(x: float, z: float) -> bool:
        i = int(np.clip((x - x0) // 4, 0, w.shape[0] - 1))
        j = int(np.clip((z - z0) // 4, 0, w.shape[1] - 1))
        return bool(np.isfinite(w[i, j]))

    return f


def terrain_sampler(cols: dict):
    h = cols["height"]
    x0, z0 = cols["x0"], cols["z0"]

    def f(x: float, z: float) -> float:
        fx = (x - x0) / 4 - 0.5
        fz = (z - z0) / 4 - 0.5
        i = int(np.clip(np.floor(fx), 0, h.shape[0] - 2))
        j = int(np.clip(np.floor(fz), 0, h.shape[1] - 2))
        u, v = np.clip(fx - i, 0, 1), np.clip(fz - j, 0, 1)
        m = h[i, j] * (1 - u) * (1 - v) + h[i + 1, j] * u * (1 - v) + h[i, j + 1] * (1 - u) * v + h[i + 1, j + 1] * u * v
        return (m - PARAMS.h0) / MPS

    return f


def run(world, ground: Ground, cols: dict, kinds=("trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "busway")) -> dict:
    entries = []
    for m in world.models:
        for shape, c, R, size, look, extra in m.parts.rows:
            if look.collide:
                entries.append((shape, c, R, size, f"{m.kind}:{m.name}"))
    solids = Solids(entries)
    terrain = terrain_sampler(cols)
    wet = water_sampler(cols)
    e0, n0 = world.region.origin
    band_lo, band_hi = 0.15 / MPS, 2.0 / MPS
    obstacles: Counter = Counter()
    obstacle_points: list = []
    worst_step = 0.0
    worst_at = None
    steps: list = []
    samples = 0
    deck_ids = {id(r) for r in (ground.deck_roads or [])}
    clip = world.region.bbox
    for r in list(ground.roads) + list(ground.deck_roads or []):
        if r.kind not in kinds:
            continue
        seg = np.linalg.norm(np.diff(r.stations, axis=0), axis=1)
        s = np.concatenate([[0], np.cumsum(seg)])
        if s[-1] < 1:
            continue
        ss = np.arange(0, s[-1], 0.5)
        xs = np.interp(ss, s, r.stations[:, 0])
        ys = np.interp(ss, s, r.stations[:, 1])
        inside = (xs > clip[0] + 5) & (xs < clip[2] - 5) & (ys > clip[1] + 5) & (ys < clip[3] - 5)
        prev = None
        deck = id(r) in deck_ids
        for E, N, ok in zip(xs, ys, inside):
            if not ok:
                prev = None
                continue
            X, Z = (E - e0) / MPS, -(N - n0) / MPS
            ivs = solids.column(X, Z)
            g = terrain(X, Z)
            # The car rolls on whatever surface lies closest to where it was a
            # moment ago: terrain, a deck plate or a kerb-strip top.
            if prev is not None:
                expect = prev
            elif deck and r.deck_z is not None:
                expect = (float(np.interp(np.hypot(E - r.stations[0, 0], N - r.stations[0, 1]), s, r.deck_z)) - PARAMS.h0) / MPS
            elif wet(X, Z) and ivs:
                expect = max(hi for lo, hi, _ in ivs)  # a road starting on a bridge deck
            else:
                expect = g
            cands = [g] + [hi for lo, hi, _ in ivs]
            near = [c for c in cands if abs(c - expect) < 1.5 / MPS]
            if near:
                best = min(abs(c - expect) for c in near)
                surface = max(c for c in near if abs(c - expect) < best + 0.1 / MPS)
            else:
                surface = g
            samples += 1
            for lo, hi, label in ivs:
                if lo < surface + band_hi and hi > surface + band_lo:
                    obstacles[label] += 1
                    if len(obstacle_points) < 400:
                        obstacle_points.append((round(E, 1), round(N, 1), label, r.feature.tags.get("name", "")))
            if prev is not None:
                step = abs(surface - prev) * MPS
                if step > 0.2:
                    steps.append((round(step, 2), round(E, 1), round(N, 1), r.feature.tags.get("name", ""), r.kind))
                if step > worst_step:
                    worst_step, worst_at = step, (round(E, 1), round(N, 1), r.feature.tags.get("name", ""))
            prev = surface
    by_kind = Counter()
    for label, n in obstacles.items():
        by_kind[label.split(":")[0]] += n
    return {
        "samples": samples,
        "obstacle_samples": int(sum(obstacles.values())),
        "obstacle_share": round(sum(obstacles.values()) / max(samples, 1), 5),
        "obstacles_by_kind": dict(by_kind),
        "top_obstacles": obstacles.most_common(12),
        "worst_step_m": round(worst_step, 3),
        "worst_step_at": worst_at,
        "steps_over_20cm": len(steps),
        "largest_steps": sorted(steps, reverse=True)[:15],
        "examples": obstacle_points[:40],
    }
