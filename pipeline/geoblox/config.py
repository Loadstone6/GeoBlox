"""Region and reconstruction parameters.

All geometry is computed in British National Grid metres (EPSG:27700) and only
converted to Roblox studs at export time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
CACHE = PIPELINE / "cache"
GENERATED = ROOT / "src" / "generated"
BUILD = ROOT / "build"


@dataclass(frozen=True)
class Region:
    name: str
    # Bounding box in BNG metres: (min_e, min_n, max_e, max_n)
    bbox: tuple[float, float, float, float]
    # Extra LiDAR margin so edge statistics (AO horizons, building rings) are valid
    margin: float = 120.0

    @property
    def origin(self) -> tuple[float, float]:
        e0, n0, e1, n1 = self.bbox
        return ((e0 + e1) / 2.0, (n0 + n1) / 2.0)

    @property
    def fetch_bbox(self) -> tuple[float, float, float, float]:
        e0, n0, e1, n1 = self.bbox
        m = self.margin
        return (e0 - m, n0 - m, e1 + m, n1 + m)


WESTMINSTER = Region(
    name="westminster",
    # Trafalgar Square → Westminster Abbey, Whitehall → the London Eye.
    bbox=(529500.0, 179100.0, 530900.0, 180600.0),
)


@dataclass(frozen=True)
class Params:
    metres_per_stud: float = 0.3
    # Vertical datum: Y_studs = (H_odn - h0) / metres_per_stud
    h0: float = -12.0
    chunk_m: float = 100.0  # mesh chunk edge (metres)
    voxel_studs: int = 4  # Roblox terrain voxel
    # Roof TIN tolerances (metres)
    roof_eps_lod0: float = 0.30
    roof_eps_lod1: float = 1.20
    roof_max_vertices: int = 260
    ransac_threshold: float = 0.18
    ransac_min_support: int = 18
    # Street geometry
    kerb_height: float = 0.12
    pavement_lift: float = 0.06
    marking_lift: float = 0.03
    # Water
    thames_level: float = 1.2  # metres ODN, roughly mid-tide
    # Trees
    tree_min_height: float = 4.0
    # Geometry budgets per EditableMesh (Roblox hard caps: 60k verts, 20k tris)
    max_tris_per_mesh: int = 19000
    max_verts_per_mesh: int = 55000
    floor_height: float = 3.4
    extra: dict = field(default_factory=dict)


PARAMS = Params()


def studs(metres: float) -> float:
    return metres / PARAMS.metres_per_stud
