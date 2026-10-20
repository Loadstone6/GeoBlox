# GeoBlox — Technical Design

A lifelike, data-driven rural world in Roblox, in the spirit of GeoCraftNL
(Geodan/GeoFort): real open geodata turned into a walkable 1:1 world, with a
procedural layer that adds artistic variation and life.

Status: design draft v1. Anything marked **[verify]** is an engine assumption
that must be confirmed in the P0 engine spike before code depends on it.

---

## 1. Goals and non-goals

**Goals**
- Near 1:1 replica of a real rural region (default: Dutch polder landscape),
  built from open data: terrain, ditches, fields, roads, trees and buildings.
- An artistic layer that keeps the world believable rather than a raw data
  dump: regional farmhouse types, seasonal crops, weather, animals, sound.
- One world model, two sources: real data where it exists, and a
  **reality-inspired synthesis** source that generates plausible land
  (beyond the region edge, or anywhere) with the same downstream code.
- Best-in-class performance on PC with a real mobile tier.

**Non-goals**
- Google Street View / Google 3D tiles. Their terms forbid embedding or
  redistributing that imagery in a game. Open data only.
- Whole-country coverage in one place. One place = one region (≤ ~9 km
  square, see §3). Larger worlds = several places linked by TeleportService.
- Editable / destructible terrain (not ruled out later, but no design
  decisions are made to support it now).

---

## 2. System overview

```
             OFFLINE (tools/pipeline, Python)                     ROBLOX
 ┌────────────────────────────────────────────────┐   ┌─────────────────────────────────┐
 │ fetch → reproject → rasterise → derive → pack  │   │ Server                          │
 │  AHN DTM/DSM     RD New grid   classes  chunk  │──▶│  WorldSource (Real | Synthesis)  │
 │  BGT/BAG/BRP/NWB               trees    blobs  │   │  TerrainWriter (bake or runtime)│
 │  3DBAG, OSM                    buildings       │   │  ChunkService (serves blobs)    │
 └────────────────────────────────────────────────┘   │ Client                          │
                                                      │  DecorBuilder (trees, fences…)  │
         Studio "Baker" plugin bakes terrain +        │  BuildingBuilder, LOD, Horizon  │
         static objects into the place file           │  Ambient (life, weather, audio) │
                                                      └─────────────────────────────────┘
```

Core principle: **everything downstream of `WorldSource` consumes the same
chunk layers** (§5). Real data and synthesis are interchangeable producers.

Split of responsibility:
| Concern | Where | Why |
|---|---|---|
| Terrain voxels | Server (or baked into the place) | Terrain replicates; must be authoritative |
| Buildings (collidable shells) | Server / baked | Shared collision for all players and NPCs |
| Trees, fences, crops, props, animals, birds | Client only, deterministic | Zero network cost, per-client LOD; the local character is client-simulated so client-only anchored parts still collide with it |
| Weather / time of day | Server decides, clients render | Everyone sees the same sky |

---

## 3. Coordinates and scale

**Scale: `METRES_PER_STUD = 0.3`** (recommended).
- An R15 avatar (~5.5 studs) is then ~1.65 m: people, doors and cars feel right.
- A terrain voxel (4 studs) is 1.2 m: about GeoCraft's 1 m block, and matches
  the width of Dutch ditches and crop rows.
- At 1 stud = 1 m, avatars would be 5 m tall and the world would feel like a model railway.

**Projection.** For NL use RD New (EPSG:28992); it is already a metric grid, so no
lat/long maths at runtime. Elsewhere use the local UTM zone.

```
x_studs =  (E - E0) / MPS
z_studs = -(N - N0) / MPS        -- north = -Z
y_studs =  (H - H0) / MPS        -- H in NAP metres
```
- `(E0, N0)` = region centre, so the world is centred on the origin.
- `H0 = regionMinHeight - 2 m`. Polders sit up to ~7 m below NAP, so a fixed
  offset keeps Y positive. Sea level is then `y = -H0 / MPS`.

**Float precision.** Keep all geometry within ±16,000 studs of the origin, which
is a ~9.6 km square region at 0.3 m/stud. Larger areas are split into separate
places.

All conversions live in one module, `shared/Coords.luau`. Nothing else may
multiply by `MPS`.

---

## 4. Data sources and licensing (NL default)

| Layer | Dataset | Licence | Use |
|---|---|---|---|
| Ground height | AHN4 DTM 0.5 m (AHN5 where available) via PDOK | CC0 | Terrain heightfield |
| Surface height | AHN4 DSM 0.5 m | CC0 | nDSM = DSM − DTM → trees, hedges |
| Large-scale topography | BGT (PDOK OGC API) | CC0 | Water, roads, verges, yards, dikes |
| Crop parcels | BRP Gewaspercelen (yearly) | CC0 | Per-field crop type → seasonal look |
| Buildings 2D + year | BAG | CC0 | Footprint, construction year, use |
| Buildings 3D | 3DBAG (LoD1.2/1.3/2.2 + roof attrs) | CC BY 4.0 | Eave/ridge heights, roof type |
| Roads | NWB Wegen | CC0 | Road graph for traffic and cyclists |
| Landmarks | OSM (windmills, churches, turbines) | ODbL | Signature landmarks and skyline |
| Colour reference | PDOK aerial photo (Luchtfoto) | CC BY 4.0 | Per-parcel tint, far-field textures |

Outside NL: Copernicus GLO-30 or national LiDAR, plus OSM for everything else.

An in-game **Credits** screen must list 3DBAG, PDOK and "© OpenStreetMap
contributors". The build fails if `data/attribution.json` is missing.

---

## 5. World model — chunks and layers

### 5.1 Grid
- Voxel = 4 studs (1.2 m). One heightfield **column** per voxel.
- **Chunk = 64 × 64 columns** = 256 studs = 76.8 m. A 5 km region is
  66 × 66 ≈ 4,300 chunks.
- Chunk key = `(cx, cz)` packed as `cx * 65536 + cz` (both offset to be positive).

### 5.2 Layers (per chunk)
| Id | Layer | Encoding | Notes |
|---|---|---|---|
| 1 | `HEIGHT` | u16 per column, units of 1/16 stud (0.019 m), offset from H0 | Range 4,096 studs ≈ 1.2 km |
| 2 | `SURFACE` | u8 class id per column (§6) | After priority resolution |
| 3 | `WATER` | u16 water-surface height (0 = dry) | Ditches, canals, ponds |
| 4 | `PARCEL` | u8 index into the chunk parcel table | ≤ 255 parcels/chunk |
| 5 | `PARCELS` | table: crop, row angle (u8), tint (rgb565), farm id | Drives seasons and stripes |
| 6 | `TREES` | list: x,z (u16, 1/64-column units), height u8, crown u8, species u8 | From nDSM (§7.3) |
| 7 | `LINEAR` | polylines: hedge, fence, tree row, reed edge, dike | Placed with spacing rules |
| 8 | `BUILDINGS` | list of building records (§8) | Footprint in chunk-local u16 |
| 9 | `ROADS` | polylines with width class and surface | For traffic paths |
| 10 | `LANDMARKS` | typed points: windmill, church, turbine, silo… | Uses hand-made assets |

### 5.3 Binary format `GBC1`
```
header:  magic "GBC1" | version u8 | cx i16 | cz i16 | layerCount u8
         layerCount × { id u8, offset u32, length u32 }
payload: per layer
  - grids:   predictor (left/up, PNG-style) → zigzag → LEB128 varint
  - classes: run-length (value u8, run varint)
  - lists:   count varint, then fixed-width records
whole blob: zstd   (EncodingService:DecompressBuffer if available [verify],
                    else zstd is skipped and a pure-Luau LZ4 decoder is used)
```
Decoding uses `buffer` reads only, in `--!native` code. Expected size is
2–6 KB per chunk, so ~10–25 MB for a 5 km region.

**Storage and delivery.** The blobs are sliced into ≤100 KB strings in
ModuleScripts under `ServerStorage/WorldData` [verify the size limits].
Clients fetch a chunk's decor layers through `ChunkService` (RemoteFunction
returning a `buffer`) only when that chunk enters their decor radius. Nothing
large lives in ReplicatedStorage, which would bloat join time.

---

## 6. Surface classes and the terrain material budget

Roblox terrain has a fixed set of ~22 base materials. `MaterialService` lets
each one take **one** global MaterialVariant override, and
`Terrain:SetMaterialColor` sets a tint per material. That is the hard budget
for distinct terrain looks, so the base materials are repurposed:

| Base material | Override (MaterialVariant) | Classes mapped to it |
|---|---|---|
| Grass | Pasture grass | Grassland, verges, dike slopes |
| LeafyGrass | Tall crop / maize | Maize, cereals (summer) |
| Ground | Ploughed clay | Bare arable, winter fields |
| Mud | Wet peat / clay | Ditch banks, trampled yards |
| Sand | Dune sand | Dunes, beaches |
| Salt | Stubble / straw | Harvested cereal (late summer) |
| CrackedLava | Bulb-field flowers | Tulips/hyacinths (spring only) |
| Asphalt | Asphalt | Main roads |
| Brick | Dutch clinker (klinkers) | Village streets, farm yards |
| Pavement | Concrete slab | Cycle paths, farm tracks |
| Cobblestone | Gravel / shell path | Unpaved paths |
| Limestone | Sugar beet / potato leaf | Root crops |
| Slate | Greenhouse ground | Under greenhouses |
| Snow, Glacier | (kept) | Winter weather |
| Rock, Basalt, … | spare | Reserved for future classes |

The mapping lives in `shared/SurfaceClasses.luau`. **Seasons** change the class
→ material map plus `SetMaterialColor`, so no voxels are rewritten when the
season changes. Crops are always the same `PARCEL` crop; only their look changes.

**Crop rows.** Terrain textures are world-aligned and can't follow a parcel's
row direction. Rows are instead encoded at voxel level: within row-crop
parcels, alternate columns along the parcel's row axis between the crop
material and `Ground`. That gives ~1.2 m stripes oriented to the real field.

---

## 7. Offline pipeline (`tools/pipeline`, Python)

The stack is Python 3.12 with numpy, rasterio, geopandas/shapely, pyproj,
scipy and laspy/PDAL (optional), managed with `uv`. A `geoblox` CLI drives it:

```
geoblox fetch    --region regions/nl_groene_hart.toml
geoblox build    --region …        # all stages, cached per stage
geoblox pack     --region … --out data/nl_groene_hart/
geoblox preview  --region …        # PNG previews of every layer (CI artefact)
```

### 7.1 Height
1. Mosaic the AHN DTM tiles, then fill no-data. Water and buildings are holes
   in the DTM: fill them by inverse-distance from the hole edge, using the
   bank minimum for water holes.
2. Resample 0.5 m → 1.2 m with an area-average. A 3×3 median removes LiDAR
   speckle without rounding off dike crests.
3. Flatten roads: sample the DTM along the NWB/BGT road centreline, smooth it
   with a 1D Savitzky–Golay filter, and write it back across the road width,
   blending into a 2-column shoulder.
4. Carve ditches: water surface = the minimum bank height along the BGT water
   polygon − 0.3 m, and the bed = surface − depth (0.6 m for ditches, 2 m for
   canals). Ditches narrower than 2 voxels are widened to 2 (an artistic
   minimum, so terrain water renders).

### 7.2 Surface classification (priority, high → low)
`water > road/path > building plot (flatten) > BGT class > BRP crop > default grass`.
BGT polygons are rasterised at 0.6 m (2× supersampled), then the majority
class is taken per column.

### 7.3 Trees from nDSM
1. Compute `nDSM = DSM − DTM`, apply a Gaussian with σ = 0.5 m, and mask out
   buildings (BAG footprints dilated by 1 m).
2. Find local maxima with a **height-adaptive window** `r = 1 + 0.08·h` m,
   keeping peaks with `h ≥ 3 m`.
3. Get the crown radius from a marker-controlled watershed, clamped to the
   allometric range for height h.
4. Assign species by context (an artistic heuristic, not ground truth):
   - Points on a straight line along a road, with spacing variance < 20 % → **poplar / oak row**.
   - Within 3 m of water with `h < 8 m` → **pollard willow (knotwilg)**.
   - Clusters inside BGT forest → mixed deciduous, plus ~15 % conifer.
5. Treat continuous low nDSM (1–3 m) along parcel edges as **hedgerow**, and
   emit it as `LINEAR` features, not trees.

### 7.4 Buildings
For each BAG pand in the region, joined to 3DBAG:
- Footprint: simplify (Douglas–Peucker, 0.3 m), square near-right angles
  (±8°), then **decompose into ≤ 6 oriented rectangles**.
- Heights: ground = `b3_h_maaiveld`, eave ≈ `b3_h_dak_min`, ridge = `b3_h_dak_max`.
- Roof type: flat / gable / hip / half-hip / shed / mansard. Classify it from
  the 3DBAG roof type, the ridge-to-eave ratio, and the LoD2.2 plane count
  and orientation.
- Typology: an **artistic variation** from province × footprint shape ×
  BAG year × use:
  - Friesland/Groningen: kop-hals-romp. Noord-Holland: stolp (pyramid roof).
    Brabant/Limburg: langgevel. Gelderland/Overijssel: hallehuis.
  - BAG year < 1940: red brick, and thatch (riet) for farmhouses. 1945–1975:
    yellow/brown brick and concrete tiles. 1975 and later: modern brick and
    metal barns. A large rectangle with use "overig" means steel-clad barn,
    silos or a manure store.
- Output: a building record (§8.1) with a deterministic `facadeSeed`.

### 7.5 Synthesis statistics
The pipeline also writes `stats.json`, which the synthesis source consumes:
the parcel width/length distribution, ditch spacing, crop frequencies by
soil, tree-row spacing, farm spacing along roads, and building typology
frequencies. This is what makes procedural land "heavily inspired by reality".

### 7.6 Tests
- pytest on small fixture tiles in the repo (`tools/pipeline/tests/fixtures`).
- Golden checksums of packed chunks, so any format change is deliberate.
- `preview` PNGs are uploaded as CI artefacts for visual review.

---

## 8. Runtime systems (Luau)

All code is `--!strict`. Hot paths are `--!native`. Shared modules are pure
(no Instance access), so Lune can test them.

### 8.1 Module map
```
src/shared/
  Config.luau            MPS, chunk size, radii, quality tiers
  Coords.luau            world ↔ RD/UTM ↔ chunk/column
  Hash.luau              PCG32 / xxhash32 on bit32; seed(world, cx, cz, layer)
  Noise.luau             seeded simplex/value noise (not math.noise, for portability in tests)
  ChunkFormat.luau       GBC1 decode/encode (encode only for tests and synthesis)
  SurfaceClasses.luau    class enum, material map, season tables
  Synthesis/             Polder.luau, Parcels.luau, Farms.luau, Trees.luau
  WorldSource.luau       interface: getChunk(cx, cz) -> ChunkLayers
src/server/
  WorldService.server.luau   boot, region load, player tracking
  ChunkScheduler.luau        priority queue, time-sliced job runner
  TerrainWriter.luau         layers -> voxel arrays (parallel) -> WriteVoxels (serial)
  BuildingBuilder.luau       records -> collidable shells
  ChunkService.luau          remote: serve decor layers to clients
  Environment.luau           authoritative time, season, weather state
src/client/
  DecorController.client.luau   decor rings, pooling
  Decor/ Trees.luau, Linear.luau, Props.luau, Crops.luau
  LOD.luau                     per-object LOD switching, impostors
  Horizon.luau                 far-field meshes beyond the streaming radius
  Ambient/ Animals.luau, Birds.luau, Traffic.luau, Windmills.luau, Audio.luau
  Sky.luau                     interpolates lighting keyframes
  Quality.luau                 adaptive quality tiers
src/plugin/
  Baker.server.luau            Studio plugin: bake region into the place
```

### 8.2 WorldSource
```lua
export type ChunkLayers = {
    cx: number, cz: number,
    height: buffer,     -- u16 × 4096
    surface: buffer,    -- u8 × 4096
    water: buffer,      -- u16 × 4096
    parcel: buffer,     -- u8 × 4096
    parcels: { Parcel },
    trees: { Tree }, linear: { Linear }, buildings: { Building },
    roads: { Road }, landmarks: { Landmark },
}
export type WorldSource = { getChunk: (self: WorldSource, cx: number, cz: number) -> ChunkLayers? }
```
- `RealSource` decodes `GBC1` blobs from `ServerStorage/WorldData`.
- `SynthesisSource` generates layers from a seed and `stats.json` (§9).
- `CompositeSource` uses real data inside the region and synthesis outside it,
  with a 2-chunk **blend band**. Heights lerp, and synthesis parcels are
  clipped against the real ditch network, so the edge isn't a visible seam.

### 8.3 Terrain generation
**Mode A — Bake (default for the real region).** The Studio plugin runs
`TerrainWriter` over every chunk in the region and saves the place. Runtime
cost is zero: terrain ships in the place file and StreamingEnabled delivers it.
This is the right choice because a 5 km region is ~4,300 chunks, well beyond a
comfortable runtime budget.

**Mode B — Runtime (synthesis area and dev iteration).** The same
`TerrainWriter` runs, driven by `ChunkScheduler`.

Column → voxel algorithm (2.5D heightfield). Write only the active vertical band:
```
for each column (x, z):
    h = height[x,z] / 16           -- studs
    yTop = h / 4                   -- voxel units, fractional
    for vy in bandMin..bandMax:
        occ = clamp(yTop - vy, 0, 1)            -- smooth top surface
        mat = occ > 0 and materialFor(surface, depthBelowTop = yTop - vy) or Air
        if water[x,z] > 0 and vy*4 < waterSurface and occ < 1:
            water occupancy = clamp(waterSurface/4 - vy, 0, 1)   -- WriteVoxelChannels [verify]
```
- `bandMin = floor(minH/4) - 3` (3 voxels of soil under the lowest point, with
  mud/clay beneath grass). `bandMax = ceil(maxH/4) + 1`. Dutch land is flat,
  so a chunk band is typically **6–10 voxels**, i.e. ~25–40 k voxels per chunk.
- Material by depth: the surface class for the top voxel, then a soil class
  (clay/peat/sand from region soil) below it.
- Slope override: if the max neighbour height difference is > 1.5 voxels,
  use `Mud` or `Ground` so dike slopes don't show bright pasture on the steep face.

**Parallelism.**
- An actor pool (`N = 4`, or 8 on server) computes the material/occupancy
  arrays in `task.desynchronize()`. Inputs are passed as `buffer`, so there is
  no table copying.
- `WriteVoxels` / `WriteVoxelChannels` run in the serial phase, treated as
  not parallel-safe [verify].
- The serial phase gets a per-Heartbeat budget of 4 ms server / 2 ms client,
  measured with `os.clock()`.
- Preallocated 3D tables are reused per worker to avoid GC churn.

### 8.4 ChunkScheduler
- Priority = `distance(chunkCentre, playerPos + playerVel * 2s)`, × 0.6 if the
  chunk is inside a 120° cone of the camera look direction (client), or
  across all players (server).
- Hysteresis: load at radius R, unload at 1.25 R. Job dedupe uses the chunk key.
- Job states: `queued → computing (actor) → ready → committing (serial) → live`.
  Cancel if the chunk falls out of range before commit.

### 8.5 Buildings
Shells are built from the rectangle decomposition:
- **Walls**: one Part per wall run (anchored, `CanTouch=false`). Material comes
  from the typology via MaterialVariant (brick colours as variants of `Brick`).
  Window/door rhythm is a `Texture` with stud-based tiling, picked by `facadeSeed`.
- **Roofs**: WedgeParts for gables, CornerWedgeParts for hips, sized from the
  eave and ridge heights. Thatch, tile and corrugated variants are MaterialVariants.
- Each building is a `Model` with `ModelStreamingMode = Atomic` and
  `LevelOfDetail = StreamingMesh`, so distant buildings render as engine
  impostors when streamed out.
- Landmarks (windmills, churches, turbines) are hand-made MeshParts with
  SurfaceAppearance (PBR). They are placed from `LANDMARKS` and are **never
  streamed out** (`Persistent`), because they define the Dutch skyline.
- v2 option: the pipeline emits merged OBJ meshes per chunk for LoD2.2
  accuracy, uploaded through the Open Cloud Assets API. This is deferred
  because of upload volume and moderation.

### 8.6 Decor (client)
| Ring | Radius (studs) | Content |
|---|---|---|
| Near | 0–300 | Full trees (trunk + leaf mesh), fences, posts, reeds, crop props, animals |
| Mid | 300–900 | Low-poly trees, hedges as merged boxes, no small props |
| Far | 900–streaming radius | Tree clusters as instanced billboard-ish cards, no hedges |
| Horizon | beyond streaming | `Horizon` meshes only (§8.8) |

- Trees come from a kit of ~12 species × 3 LOD meshes, with per-instance
  scale (from height and crown) and a yaw derived from `Hash`. Identical
  MeshId + texture means automatic GPU instancing.
- Pooling: one pool per asset/LOD. Set properties before parenting, and move
  objects with `workspace:BulkMoveTo`.
- Defaults for decor: `CanQuery=false`, `CanTouch=false`, `CastShadow=false`
  (except near trees and buildings), `CollisionFidelity=Box`, and trunks only
  collide.
- Budgets come from §11, enforced by `Quality`. When over budget, the far ring
  shrinks first.

### 8.7 Ambient life
- **Livestock**: cows and sheep spawn in BRP grassland parcels near farms,
  with count ∝ parcel area. A simple state machine (graze / walk / lie down)
  is constrained to the parcel polygon, and ditches act as natural fences.
  They are animated with AnimationController and only exist within 300 studs.
- **Birds**: boids flocks (starling murmurations at dusk), and lapwings and
  geese over open grassland. Max 60 birds, updated with `BulkMoveTo`.
- **Windmills / turbines**: blade speed comes from `workspace.GlobalWind`.
  CFrame updates are batched in a single `RenderStepped` handler.
- **Traffic**: tractors, cars and cyclists follow the NWB road graph with A*
  between random farm nodes. They are client-side and cosmetic, and despawn
  out of sight.
- **Water**: terrain water. Ducks and moorhens appear on ditches, and reed
  sways via the terrain decoration and wind.
- **Audio**: there is an ambience bed per surface-class histogram around the
  listener (pasture birdsong, reed rustle, road hum), crossfaded over 2 s.
  Positional one-shots cover cows, church bells and windmill creaks. It uses
  the new Audio API (AudioPlayer/AudioEmitter/AudioListener) [verify].

### 8.8 Horizon (far field)
Dutch landscapes are flat and open, so a short view distance would ruin
them. Beyond the terrain streaming radius:
- The pipeline emits, per 1 km tile, a **low-res heightmap mesh** (65×65
  verts) plus a **512² texture** derived from the aerial photo and colour
  graded to match the terrain palette. These are uploaded as assets.
- Client `Horizon` shows the tiles in a ring from the streaming radius out to
  ~6 km, dropped 0.5 studs below the terrain to avoid z-fighting, and hidden
  per tile once the real terrain for that area has streamed in.
- Skyline silhouettes (church towers, turbines, tree rows) come from the
  `LANDMARKS` layer as low-poly persistent meshes.
- Atmosphere haze is tuned so that the horizon hides the transitions.

---

## 9. Synthesis source (reality-inspired procedural)

This generates a polder that is statistically like the real region.
1. **Macro**: a low-frequency height (±1 m) and a soil field (clay / peat /
   sand) from noise. Peat areas sit lower.
2. **Polder grid**: the dominant axis angle comes from region stats. Roads
   and main canals sit on a jittered grid, with spacing drawn from the stats
   distributions.
3. **Parcels**: blocks between roads and canals are split by recursive
   subdivision along the axis. Widths come from the parcel-width
   distribution, and a ditch runs along every parcel boundary.
4. **Farms**: ribbon settlement (lintbebouwing) along roads, with spacing from
   the stats. Each farm has a yard, a farmhouse typology for the province, and
   barns.
5. **Crops**: drawn from crop-frequency-by-soil. Neighbouring parcels of the
   same farm are correlated.
6. **Trees**: rows along roads (spacing from stats), willows on ditch corners,
   and farmyard clusters.

The output is the same `ChunkLayers`, so everything else is unchanged.
Synthesis is seeded per chunk, but parcels span chunks. Parcels are therefore
generated per **super-cell** (8×8 chunks) and cached, keyed by
`hash(seed, superCell)`.

---

## 10. Graphics

- `Lighting.Technology = Future`.
- `Lighting.GeographicLatitude` is set to the region latitude (52° for NL).
  Time of day is real-time-scaled (1 day = 48 min by default).
- **Sky keyframes** (`Sky.luau`): a table of `ClockTime → { Ambient,
  OutdoorAmbient, ColorShift_Top, Brightness, ExposureCompensation,
  Atmosphere{Density, Offset, Color, Decay, Glare, Haze},
  Clouds{Cover, Density, Color}, ColorCorrection{…}, Bloom{…}, SunRays{…} }`,
  interpolated each frame (cheap). Dutch preset: high cloud cover, soft
  diffuse light, and long golden hours.
- **Weather** (server `Environment`) is a Markov chain over
  {clear, broken clouds, overcast, drizzle, rain, fog, frost, snow}, with
  season-weighted transitions. It drives Clouds, Atmosphere density,
  `GlobalWind`, rain/snow ParticleEmitters attached around the camera, and a
  wet-look MaterialVariant swap at a ~30 s fade.
- **Seasons**: material/colour map swaps (§6), deciduous trees swap to
  bare-branch meshes in winter, bulb fields only flower in April–May, and
  stubble after harvest.
- **Materials**: PBR MaterialVariants (2K source, 1K on mobile) and
  SurfaceAppearance on hero meshes.
- Post-processing is subtle: ColorCorrection, Bloom (low), SunRays, and
  DepthOfField only in photo mode.

---

## 11. Performance budgets and quality tiers

| Metric | PC High | PC Medium | Mobile |
|---|---|---|---|
| Frame time p95 | ≤ 16.6 ms | ≤ 16.6 ms | ≤ 33 ms |
| Client Luau per frame | ≤ 2 ms | ≤ 2 ms | ≤ 1.5 ms |
| Decor instances live | ≤ 25 k | ≤ 15 k | ≤ 6 k |
| Near / mid / far radius (studs) | 300 / 900 / 2000 | 250 / 700 / 1400 | 150 / 400 / 800 |
| Client memory | ≤ 2 GB | ≤ 1.5 GB | ≤ 900 MB |
| Shadows on decor | near trees | none | none |
| Horizon distance | 6 km | 4 km | 2 km |

- **Server**: Heartbeat ≥ 55 Hz, chunk work ≤ 4 ms/frame, memory ≤ 3 GB.
- **Adaptive quality**: `Quality.luau` samples frame time. If p95 exceeds the
  budget for 5 s it drops a tier, and it raises one after 30 s of headroom.
  The starting tier comes from the platform and `UserGameSettings().SavedQualityLevel`.
- **Streaming**: `StreamingEnabled = true`,
  `StreamingIntegrityMode = PauseOutsideLoadedArea`. Target radius by tier.
- Avoid in hot paths: `Touched`, `:GetDescendants()`, per-frame
  `Instance.new`, closures per object, and `math.random` (use seeded `Random`).
- **Profiling**: MicroProfiler labels on every job type
  (`debug.profilebegin("GB:Terrain:Compute")`) and a dev overlay that shows
  queue depths, budgets and instance counts.

---

## 12. Repository layout and tooling

```
GeoBlox/
  default.project.json      Rojo (game)   plugin.project.json (Baker)
  rokit.toml                rojo, wally, selene, stylua, lune, luau-lsp
  wally.toml                minimal deps (e.g. testing only)
  selene.toml  stylua.toml  .luaurc (strict)
  src/{shared,server,client,plugin}/
  tests/                    Lune specs for shared/*
  tools/pipeline/           Python package + pytest
  regions/*.toml            bbox (RD), MPS, H0, season default, typology overrides
  data/<region>/            packed chunks (Git LFS), attribution.json, stats.json
  assets/manifest.json      logical asset name → rbxassetid
  docs/DESIGN.md
  .github/workflows/ci.yml  stylua --check, selene, luau-lsp analyze, lune tests, pytest
```

**Integration tests (optional).** The Roblox Open Cloud Luau Execution API
can run server-side scripts against the published place from CI, which tests
Roblox-API code (TerrainWriter round-trip via `ReadVoxels`) headlessly
[verify availability for this account].

---

## 13. Delivery plan

| Phase | Scope | Done when |
|---|---|---|
| **P0 Foundation** | Repo scaffold, CI, `Coords`, `Hash`, `ChunkFormat`, and an **engine spike** that checks every [verify] item in Studio | CI green; spike notes committed in `docs/engine-spike.md` |
| **P1 Terrain** | Pipeline height + surface for a 2×2 km test region; TerrainWriter; Baker; runtime mode | Region walkable in Studio; dikes and ditches recognisable vs aerial photo |
| **P2 Land** | Water carving, parcels, crop rows, seasons, roads | Side-by-side with the aerial photo is convincing from 50 m altitude |
| **P3 Vegetation** | nDSM trees, hedges, decor rings, pooling, LOD | Budgets met at PC High and Mobile |
| **P4 Buildings** | 3DBAG join, rectangle decomposition, typology shells, landmarks | ≥ 90 % of buildings classified; no floating or buried buildings |
| **P5 Life & sky** | Sky keyframes, weather, animals, birds, traffic, audio, horizon | 10-minute walk feels alive; horizon has no visible popping |
| **P6 Synthesis** | stats.json, SynthesisSource, CompositeSource blend band | Edge of the real region is not obvious to testers |
| **P7 Scale-out** | 5 km region, perf pass, mobile tier, credits screen, publish | All §11 budgets pass on a mid-range phone |

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Engine limits differ from assumptions (region size per WriteVoxels, string property size, parallel safety) | Rework | P0 spike before dependent code |
| Place file too large with baked terrain + data | Slow publish/join | Bake band-only terrain; data ≤ 25 MB; split regions across places |
| Too many unique building parts | Draw calls / memory | Merge per building, streaming impostors, mesh path in v2 |
| Flat horizon looks empty or pops | Breaks realism | Horizon meshes + skyline landmarks + haze tuning |
| Data accuracy vs. fun (ditches everywhere block movement) | Frustration | Player-only "hop" across ≤ 2-voxel ditches; bridges at farm entrances from BGT |
| Licence compliance (CC BY, ODbL) | Takedown | Attribution file enforced by CI, credits screen |
