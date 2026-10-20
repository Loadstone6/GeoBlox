# GeoBlox — Westminster as a drivable Roblox city

GeoBlox rebuilds a real 1.4 × 1.5 km piece of central London (Trafalgar Square →
Westminster Abbey, Whitehall → the London Eye) in Roblox from open
measurement data, for a driving game. It works like the Minecraft converters:
GeoCraft and Arnis map measured or mapped geometry onto blocks, and GeoBlox
maps it onto **Roblox parts**. Every building, kerb, road marking, bridge,
tree and lamp column is a box, wedge, cylinder or ball baked into the place
file. The roads are Roblox terrain. The whole city is replicated, collidable,
streamed by the engine and editable in Studio.

```
 Environment Agency LiDAR (1 m DTM + first-return DSM)     OpenStreetMap (footprints, 3D parts,
              │                                             roads, kerbs, crossings, trees, furniture)
              └──────────────┬───────────────────────────────────────┘
                 OSTN15 transform (cm-level), FFT registration check
                             │
   ┌───────────────┬─────────┴─────────┬────────────────┬──────────────┬────────────┐
 LoD2 roofs      footprints          road surface      bridges        trees        landmarks
 RANSAC planes   regularisation      robust heights    deck profile   CHM maxima   Eye: PCA
 line arrange-   facade modules      approach fusion   landfall ramp  watershed    Big Ben dials
 ment + Potts    party walls,        kerbs + strips    piers, arches  OSM fusion
 labelling       passages            TSRGD markings
   └───────────────┴──────────────── part mapper ─────┴────────────────┴──────────────┘
          exact boxes / wedges (partmap.py) · prefab placements · terrain columns
                             │
      world/*.gbw chunks ──► Lune baker (tools/bake.luau) ──► Workspace.City in the place
                             │
     Roblox: StreamingEnabled (atomic models, StreamingMesh impostors) · terrain roads
     written by parallel Actors · raycast-suspension test car · night lights, signals,
     beacons, rotating Eye and live Big Ben from CollectionService tags
```

Code: `pipeline/geoblox/*` (Python), `tools/bake.luau` (Lune), `src/*` (Luau).

---

## Why parts (and not meshes)

The first version rendered reconstructed meshes on the client with
`EditableMesh`. That approach doesn't suit a car game:

- **It is client-only and gated.** EditableMesh content does not replicate, so
  the server had no collision geometry for cars. Published games also need
  *Allow Mesh & Image APIs* and an ID-verified owner.
- **Uploading meshes is no better.** Uploaded MeshParts would replicate, but
  Open Cloud asset uploads are rate-limited and Roblox recommends the Studio
  3D Importer for meshes ([assets guide](https://create.roblox.com/docs/cloud/guides/usage-assets)).
  That doesn't scale to thousands of building meshes rebuilt on every
  pipeline run.

Parts avoid all of this. They replicate, collide, work without permissions,
stream with the engine, batch well when static, and are what Roblox city maps
are made of ([parts vs meshes](https://devforum.roblox.com/t/parts-vs-meshes-for-performance/649907),
[mega city maps](https://devforum.roblox.com/t/how-to-make-a-mega-city-map/1527626)).
Existing Roblox real-world importers also generate parts from OSM, but stop at
extruded footprints and flat road strips: see
[WorldLoader](https://github.com/Klingaac/WorldLoader-Roblox-Plugin)
([DevForum](https://devforum.roblox.com/t/worldloader-plugin-import-real-life-places-into-studio/3190878)),
its fork [GeoGenerator](https://github.com/illegalgames099/GeoGenerator) (kerbs
and lane markings), and [RoadGenerator](https://devforum.roblox.com/t/roadgenerator-a-real-world-road-generator/496241).
GeoBlox keeps the measurement-driven reconstruction and changes only the output
primitive, the same way [Arnis](https://wiki.openstreetmap.org/wiki/Arnis)
maps OSM elements onto Minecraft blocks with per-tag element processors.

## 1. Data and coordinate reference

| Source | Product | Use | Licence |
|---|---|---|---|
| Environment Agency | LiDAR Composite **DTM 1 m (2022)** via OGC WCS 2.0.1 | ground, roads, water level | OGL v3 |
| Environment Agency | LiDAR Composite **First-Return DSM 1 m** via WCS | roofs, trees, bridge decks, the Eye | OGL v3 |
| OpenStreetMap | Overpass extract (~66k elements) | footprints, `building:part` 3D, roads, lanes, kerbs, crossings, trees, furniture | ODbL |

All geometry is computed in **British National Grid** (EPSG:27700, metres).
OSM coordinates are transformed with the **OSTN15** grid (PROJ `hgridshift`,
centimetre-level). FFT phase correlation between rasterised OSM footprints and
the LiDAR building mask measures the residual offset at (0.05 m, −0.2 m), so no
correction is needed. Roblox coordinates: `X = east`, `Y = up`, `Z = south`,
1 stud = 0.3 m (true scale for an R15 avatar and a 4.3 m car), heights relative
to −12 m ODN. The map is orientation-preserving, so plane frames computed in BNG
stay right-handed in studs.

## 2. Mapping geometry onto parts (`partmap.py`)

The part mapper is exact: what the reconstruction computes is what Roblox
draws.

- **Planar polygons** (roof faces, gable ends, deck slabs, wall infill) are
  expressed in their plane's own 2D frame. They are swept into trapezoids
  along a dominant edge direction, and collinear trapezoids are merged across
  sweep lines. Each trapezoid is one box plus at most two right-triangle
  wedges, and adjacent boxes merge again. A rectilinear L-shape becomes two
  boxes, and a wall with a gable becomes one box and two wedges. A constrained
  Delaunay triangulation is the fallback whenever it needs fewer parts.
- **Any triangle** is two wedges split at the altitude from its longest edge.
  This is EgoMoose's WedgePart technique
  ([DevForum](https://devforum.roblox.com/t/3d-triangle-creation/65274)); a
  right triangle is one wedge. The wedge convention (square corner at
  bottom-back, slope facing −Z) is fixed in code and tested.
- Parts are **plates**: the visible face lies exactly on the input plane and
  the thickness extends behind it, so surfaces stay where the data puts them.
- Records are 52 bytes: shape, material, flags, CollectionService tag, colour,
  position, size and quaternion. The Lune tests check that `CFrame.new(x, y, z,
  qx, qy, qz, qw)` reproduces the pipeline's rotation matrices.

Tests cover the wedge convention, triangle coverage and area, quaternion round
trips, partition exactness and minimality on rectilinear shapes, and plates on
sloped planes.

## 3. Buildings

**Footprints** (`buildparts.py`). OSM outlines are regularised. Each edge
within 0.25 m end-deviation of the building's dominant axes is snapped to
them, parallel neighbours merge, and corners are re-intersected. The result
is rejected if it moves the footprint by more than 4% of its area or more than
0.6 m. Courtyards and light wells are cut where the DSM is at ground level
inside the outline.

**LoD2 roofs by footprint partitioning** (`roofer.py`). The method follows
3DBAG's roofer ([Peters et al. 2022](https://arxiv.org/pdf/2201.01191),
[roofer](https://github.com/3DBAG/roofer),
[TU Delft course notes](https://3d.bk.tudelft.nl/courses/geo1004/data/lod2recon.pdf)):
1. Sequential RANSAC planes are fitted on the DSM inside the footprint and
   split into connected facets. Near-flat planes snap to horizontal, pitched
   planes turn their slope onto the footprint axes, and near-duplicates merge.
2. Candidate lines come from three sources:
   - plane intersections of touching facets (ridges, hips, valleys), kept only
     if the shared boundary lies on them;
   - step lines fitted to boundary pixels between facets at different heights,
     snapped to the footprint directions;
   - mapped `building:part` outlines.
3. The lines partition the footprint into cells. Each cell is labelled with a
   plane by minimising a Potts energy: area × mean truncated residual, plus
   λ × the length of shared boundary where labels differ, solved with ICM.
   Zebedin et al. 2008 and roofer solve the same energy with graph cuts.
   Unexplained clusters, such as plant rooms, get their own flat levels.
4. Cells merge into planar faces. Height jumps between faces become step
   walls, and steps of a storey or more get windows.

On real data, 844 LiDAR buildings rebuild into 4,122 planar faces (about 5
each). Tests check that gable, hip and stepped roofs with plant rooms are
recovered exactly, with watertight ridges.

**Walls and facade modules.** Each outline edge is a wall run from the ground
(or, for party walls, from the neighbour's measured roof) up to the roof
profile along the edge. Flat roofs get a 0.8 m parapet. Below the eave a
facade module draws:
- spandrel bands at the wall line;
- glass recessed 13 cm, in bands of up to three floors, each tagged to light
  up independently at night;
- piers 3 cm proud at every bay boundary, which gives real window reveals and
  shadows from B + F + ⌈F/3⌉ + 2 parts.

Bay width, window size and floor height come from an inferred style: London
stock brick, red brick, Portland stone, gothic, curtain wall (mullions and
transoms), concrete or stucco. Variants add shopfronts with fascias where OSM
shows shops, and cornices on classical facades. Anything above the eave
(gables, mansards, steps) is decomposed exactly. Walls fully hidden by a
taller neighbour are skipped.

**Passages.** Roads mapped through buildings (`tunnel=building_passage`, such
as King Charles Street and Cockspur Court) and streets under railway viaducts
open the walls to a 5 m clearance, with a ceiling and side linings.

**Mapped landmarks** (83 buildings, Simple 3D Buildings). Every
`building:part` goes through the same wall runs. Roofs follow `roof:shape`:
gables and hips as exact planes, pyramids and cones as triangle fans, domes and
onions as ellipsoids, barrel vaults as box strips. Gilded parts use Foil. This
gives the Palace of Westminster, the Abbey, Nelson's Column and Admiralty
Arch.

## 4. The drivable ground

**Roblox terrain is the road surface** (`surface.py`), as in
[Heisters](https://devforum.roblox.com/t/heisters-using-terrain-for-roads/3820006).
It has zero part cost, junctions of any shape come free, and it collides and
streams like terrain. Heights (1/32-stud precision, ~9 mm) come from:

- **Road**: a robust normalised convolution of the DTM over carriageway cells
  at least 0.8 m from the kerb. Two re-weighting passes drop cells more than
  0.5 m off the local surface, such as DTM pits next to abutments and basement
  wells. A gradient limiter relaxes anything steeper than 20% inside the
  carriageway.
- **Bridge approaches.** Bare-earth filtering classifies raised approaches
  (vaults, ramps) as structures, which put Bridge Street 6 m below the real
  road. Within 90 m of a river bridge, a vehicle-free DSM surface replaces the
  DTM where it is smooth and higher. That surface is a 5×5 minimum over street
  cells only, which erases cars, buses and lamp columns.
- **Pavement**: kerb height above the road at the kerb, blending over 3 m into
  the measured pavement surface.

**Kerbs.** A 4-stud voxel cannot hold a 12 cm kerb, so kerbs are granite parts
with a 2.4 m pavement-edge strip behind them. Terrain within 1 m of the kerb on
the pavement side stays at road height, and the strip covers the ramp, so no
terrain lump ever sits on the road.

**Carriageway widths** are measured by ray casting from centreline stations to
mapped kerbs and building fronts (see `ground.py`). **Markings**
(`streets.py`) follow UK TSRGD: centre line 4/5 m, lane lines 2/7 m, double
yellow or red lines, zebras with zig-zags, and signal crossings with stud lines
and stop lines. They are 8 cm non-colliding parts at the road surface.

**Bridges** (`bridges.py`).
- **Deck profile.** The DSM is binned along each deck (30th percentile, then a
  moving median) and simplified into straight runs. Each run is a tilted
  planar slab: asphalt over the bridge carriageway, footways raised a kerb
  height, and granite kerbs between.
- **Landfall.** Each deck end walks outward until the road terrain agrees with
  the deck, then ramps onto it. Terrain under a deck is kept below it.
- **Viaducts.** Elevated stretches with no mapped deck are built station by
  station from their own DSM profile, cut against the decks so nothing
  overlaps.
- **Structure.** River crossings get piers with cutwaters and segmental arches
  with exact spandrels: seven spans for Westminster Bridge, in Commons green.
- **Embankment.** Granite river walls stop below the decks.

**Drivability check** (`drivecheck.py`). Every road centreline is sampled each
0.5 m (51,868 samples). The check follows the surface a car would roll on and
looks for collidable solids in the band 0.15–2.0 m above it, and for height
steps. It found the faults fixed above: walls across building passages,
embankment walls at bridge ends, a deck gap at Waterloo Bridge, and the
DTM-lowered approaches. In the current build it reports **0 blocked
samples**. The remaining steps over 20 cm are on Adelphi Terrace, a cul-de-sac
on the 18th-century Adelphi vaults where the DTM itself is inconsistent.

## 5. Trees and street furniture (prefabs)

**Trees** (`trees.py`) are detected in the canopy height model and fused with
OSM trees:
- Tops are local maxima with a height-adaptive window.
- Crowns come from a marker-controlled watershed.
- OSM trees snap to tops within 4 m.

This yields 4.2k trees, each with a measured height and crown radius.

**Furniture** (2.0k items) comes from OSM, plus signal heads and Belisha
beacons at the 241 mapped crossings, plus lamp columns wherever OSM has none.
Examples: Westminster lamp columns, K6 kiosks, pillar boxes, bus stops and
shelters, cycle-hire docks, memorials and flagpoles.

Both are **prefab placements**, built by the shared `src/shared/Prefabs.luau`.
It is like a Minecraft schematic library: the baker calls it in Lune, a copy
of every prefab is stored in `ServerStorage.GeoBloxPrefabs`, and the game can
reuse it at runtime. Restyling a lamp column changes every lamp without
rerunning the pipeline.

## 6. Landmarks

The **London Eye's** wheel plane comes from PCA of the 1,081 LiDAR returns
above 45 m. Its returns are masked from the DSM before building and tree
reconstruction. The wheel is about 280 parts: rims, braces, spokes, spindle,
A-frame and 32 capsule ellipsoids. **Big Ben's** four dials sit at the white,
round mapped parts; their hands show London time, BST-aware.

## 7. Baking and streaming

`world.py` writes one zstd chunk per 100 m: a JSON header (models, part
ranges, attributes, prefab placements) followed by part records.
`tools/bake.luau` (Lune 0.10) reads them and builds `Workspace.City/<chunk>/<model>`
in the place that `rojo build` produced. It takes about 9 s and produces an
8.3 MB place. Streaming is configured per model kind:

| Kind | Streaming |
|---|---|
| building, bridge | `Atomic`, `LevelOfDetail = StreamingMesh` (engine impostor beyond the radius) |
| streets (kerbs, strips, markings per chunk) | `Atomic` |
| tree, prop (one model each) | `Atomic` |
| landmark (Eye, dials) | `Persistent` (animated on every client) |

The workspace settings are:
- StreamingTargetRadius 1600 studs (480 m) and StreamingMinRadius 420;
- `StreamingIntegrityMode = PauseOutsideLoadedArea`, which pauses rather than
  dropping a car through unloaded ground
  ([enum](https://create.roblox.com/docs/en-us/reference/engine/enums/StreamingIntegrityMode.md));
- opportunistic stream-out.

The client also calls `RequestStreamAroundAsync` 2.5 s ahead of a moving car.

| | |
|---|---|
| Buildings | 927 (83 mapped, 844 LiDAR) → 180k parts; 4,122 roof faces from 1,560 detected planes |
| Streets | 1,456 road stretches (64 through passages), 24 elevated; 17k kerb + strip parts, 18k marking parts |
| Bridges | 18 decks + viaducts → 8.8k parts |
| Trees / furniture | 4.2k / 2.0k prefabs → 39k parts |
| Total | 264k parts, 8.3 MB place, 3.9 MB world chunks + 1.5 MB terrain |
| Build | ≈ 4 min pipeline + 9 s bake |

## 8. Roblox runtime

- **Server.**
  - Four Actors decode terrain chunks and voxelise them in parallel, nearest
    the spawn first; `WriteVoxels` runs in the serial phase.
  - Each player spawns into a test car on Westminster Bridge facing Big Ben.
  - The car is anchored while parked. When someone sits, it is unanchored and
    handed to the driver's client (network ownership).
- **Car** (`src/shared/Car.luau`, `src/client/Drive.luau`). A true-scale
  hatchback with raycast suspension, driven from VehicleSeat inputs (keyboard,
  gamepad or touch):
  - one invisible chassis box carries mass and collisions;
  - each wheel's ray gives a spring-damper force (stiffness from static sag,
    damping ratio 0.42);
  - tyre forces cancel side slip and apply drive, brake and rolling
    resistance, capped by a friction circle;
  - forces go through VectorForces at the mounts, as DevForum advice prefers
    over impulses ([raycast suspensions](https://devforum.roblox.com/t/raycast-suspensions/3105353),
    [scripted car physics](https://devforum.roblox.com/t/in-depth-scripted-car-physics/3915628)).

  Gravity at 1 stud = 0.3 m is about 6 g, so grip and acceleration are tuned
  in real-world terms (about 1 g cornering and braking, 0.4 g acceleration,
  70 mph top speed). Raycast wheels ride over part seams and terrain without
  snagging.
- **Effects.** CollectionService tags drive everything that changes, and are
  tracked as parts stream in and out:
  - about 38% of window bands and every shopfront light up at night;
  - lanterns get real PointLights near the camera;
  - Belisha beacons flash and signals cycle;
  - the Eye turns once every 30 minutes on server time;
  - Big Ben's hands follow London time.
- **Sky.** London keyframes (latitude 51.5°) and five weather states.

## 9. Validation

- `pipeline/tests`: 25 tests covering the part mapper (exactness, wedge
  convention, quaternions, vertical solid queries), LoD2 roofs, facade
  budgets, landmark roofs, footprint regularisation, terrain encoding,
  RANSAC, ray casting, triangulation and parsers.
- `lune run tests/run [place]`: about 1,740 checks.
  - It decodes every terrain chunk and voxelises samples.
  - It validates every world part record (shapes, materials, tags, sizes,
    unit quaternions).
  - It checks the quaternion convention against Lune's CFrame and builds
    every prefab.
  - Given a place, it checks streaming settings, model streaming modes,
    persistent landmarks and the prefab library.
- `build/report.json`: reconstruction statistics and the drivability report.
- `build/preview_*.png`: offline renders of the parts exactly as placed.

## 10. Known limitations and next steps

- **Not yet run in Roblox from here.** The place has not been opened in Studio
  from this environment. The car is a first-pass test rig and its feel needs
  tuning in Studio.
- **Facades are generic.** Styles are inferred, not observed. Open
  building-age and material data (Colouring London) could drive them.
- **1 m LiDAR misses fine detail.** It cannot resolve chimney pots or
  pinnacles unless they are mapped in 3D. National LiDAR Programme point
  clouds would allow point-based roof segmentation.
- **The area is small for driving.** 1.4 × 1.5 km fits comfortably inside
  Roblox's precise coordinate range, and the pipeline is region-generic, so a
  larger London box is a configuration change (`config.py`) plus a download.
  The part count scales with building density, roughly 120k per km² in
  Westminster.

## References
- Peters, Dukai, Vitalis, van Liempt & Stoter (2022), *Automated 3D reconstruction of LoD2 and LoD1 models for all 10 million buildings of the Netherlands*, PE&RS ([arXiv](https://arxiv.org/pdf/2201.01191)).
- Zebedin, Bauer, Karner & Bischof (2008), *Fusion of feature- and area-based information for urban buildings modeling from aerial imagery*, ECCV.
- Fischler & Bolles (1981), *Random Sample Consensus*.
- Shewchuk (1996), *Triangle: Engineering a 2D Quality Mesh Generator and Delaunay Triangulator*.
- Vincent & Soille (1991), *Watersheds in Digital Spaces*.
- Ordnance Survey (2016), *OSTN15 / OSGM15 transformation*.
- Department for Transport, *Traffic Signs Regulations and General Directions 2016*.
- Roblox Creator Docs: [instance streaming](https://create.roblox.com/docs/workspace/streaming), [StreamingIntegrityMode](https://create.roblox.com/docs/en-us/reference/engine/enums/StreamingIntegrityMode.md), [WedgePart](https://create.roblox.com/docs/en-us/reference/engine/classes/WedgePart.md), [Open Cloud assets](https://create.roblox.com/docs/cloud/guides/usage-assets).
- Arnis ([OSM wiki](https://wiki.openstreetmap.org/wiki/Arnis)) and GeoCraft: real-world data → Minecraft.
