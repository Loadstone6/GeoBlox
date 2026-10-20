# GeoBlox — Reconstructing Westminster from LiDAR and OpenStreetMap

GeoBlox rebuilds a real 1.4 × 1.5 km piece of central London (Trafalgar Square →
Westminster Abbey, Whitehall → the London Eye) inside Roblox from open data.
It follows the GeoCraft idea (measured national datasets → a walkable world) but
reconstructs **meshes**, not voxels: Roblox renders arbitrary geometry through
`EditableMesh`, so roofs, walls, kerbs and road markings keep their true shapes.

```
 Environment Agency LiDAR (1 m DTM + first-return DSM)     OpenStreetMap (buildings, 3D parts,
              │                                             roads, kerbs, crossings, trees, furniture)
              └──────────────┬───────────────────────────────────────┘
                     OSTN15 transform + FFT registration check
                             │
   ┌──────────────┬──────────┴────────┬──────────────┬─────────────┬──────────────┐
 buildings      ground/streets       bridges        trees         landmarks      sky-view AO
 RANSAC planes  measured widths      deck profile   CHM maxima    Eye: PCA       horizon scan
 breaklines     hardscape partition  arches/piers   watershed     Big Ben dials  on the DSM
 greedy TIN     kerbs, TSRGD marks                  OSM fusion
 S3DB parts
   └──────────────┴───────────────────┴──────────────┴─────────────┴──────────────┘
                   chunked binary (zstd + base64) → Luau modules
                             │
     Roblox: server terrain (parallel Actors) · client EditableMesh streaming with LOD,
     parametric trees/furniture, rotating Eye, live Big Ben hands, London sky
```

Code: `pipeline/geoblox/*` (Python), `src/*` (Luau). Build: `python -m geoblox.build`.

---

## 1. Data and coordinate reference

| Source | Product | Use | Licence |
|---|---|---|---|
| Environment Agency | LiDAR Composite **DTM 1 m (2022)** via OGC WCS 2.0.1 | ground, water level | OGL v3 |
| Environment Agency | LiDAR Composite **First-Return DSM 1 m** via WCS | roofs, trees, bridge decks, the Eye | OGL v3 |
| OpenStreetMap | Overpass extract (~66k elements) | footprints, `building:part` 3D, roads, lanes, kerbs, crossings, trees, furniture | ODbL |

All geometry is computed in **British National Grid** (EPSG:27700, metres).
OSM's WGS84 coordinates are transformed with the **OSTN15** NTv2 grid (PROJ
`hgridshift`), which is accurate to a few centimetres; a 7-parameter Helmert
transform would be off by several metres. Roblox coordinates: `X = east`,
`Y = up`, `Z = south`, 1 stud = 0.3 m (an R15 avatar ≈ 1.65 m), heights
relative to −12 m ODN.

**Registration check.** OSM footprints are rasterised and compared with a LiDAR
building mask (nDSM > 3 m) by FFT phase correlation with 1/20-pixel upsampling.
The estimated offset is **(0.05 m, −0.2 m)**, so no correction is applied; the
transform alone aligns vector and LiDAR data to within a fraction of a cell.

## 2. Buildings

Two modes per building (`buildings.py`, `s3db.py`):

**a) LiDAR reconstruction (831 buildings).**
1. *Faces.* The footprint and its `building:part` outlines are noded and
   polygonised (planar arrangement); parts act as breaklines where heights
   change. Slivers < 3 m² merge into their longest-shared-edge neighbour.
2. *Courtyards.* DSM cells inside a face that are within 1.6 m of ground form
   connected regions; regions ≥ 20 m² are contoured (marching squares) and cut
   out as holes, so light wells get inward-facing walls.
3. *Samples.* DSM cell centres inside the face eroded by 0.45 m (excludes
   wall/ground mixed pixels).
4. *Planes.* Sequential **RANSAC** (Fischler & Bolles 1981) fits `z = ax+by+c`
   with an orthogonal-distance threshold of 0.18 m, adaptive iteration count
   (99.5% confidence), slopes < 70°. Each consensus set is split into
   4-connected components on the raster, so coplanar but separate facets stay
   separate; each is refit by least squares. Samples get a *snapped* height
   (their plane) or a 3×3 median otherwise.
5. *Breaklines.* For adjacent facets the plane intersection
   `(aᵢ−aⱼ)x + (bᵢ−bⱼ)y + (cᵢ−cⱼ) = 0` is kept if the facets' shared raster
   boundary lies on it (median distance < 1 m, i.e. they meet rather than
   step). Points along it, at the exact plane height, seed the TIN, so ridges,
   hips and valleys are exact.
6. *Greedy-insertion TIN* (Garland & Heckbert 1995) over a constrained Delaunay
   triangulation (Shewchuk's Triangle) of the face: repeatedly insert, per
   triangle, the worst sample until every sample is within ε. ε is per-sample:
   0.30 m on detected planes and 0.8 m on clutter (sub-resolution pinnacles,
   plant), so the TIN models structure, not noise.
7. *Eaves* (wall tops) use the 30th percentile of snapped heights within 2.2 m
   of a point 0.9 m inside the wall, so chimneys do not lift the eave line.
8. *Seams.* Vertices shared by faces (and by neighbouring buildings) whose
   heights agree within 0.45 m are unified; otherwise a vertical step wall is
   emitted from the lower neighbour's TIN height.

**b) Mapper-modelled landmarks (83 buildings).** Where ≥ 85% of a building's
parts carry explicit heights (Simple 3D Buildings tagging: the Palace of
Westminster has 365 parts, Westminster Abbey 330, Elizabeth Tower 80, plus
Nelson's Column and Cleopatra's Needle), the parts are extruded from
`min_height` to `height`. Roofs are analytic height functions over the
footprint: gabled `top − rh·|v|/W`, hipped `base + rh·d/d_max` (distance to the
outline, a straight-skeleton approximation), pyramidal
`base + rh·(1 − max(|u|/L, |v|/W))`, dome `base + rh·√(1 − (1 − d/d_max)²)`,
barrel, skillion, onion. Wall tops follow the same function, so gable ends close
exactly. The ground level still comes from the LiDAR DTM.

**Facades.** An architectural style is inferred from tags, height and history:
London stock brick, red brick, Portland stone, gothic (Palace, Abbey,
churches), stucco, concrete, or curtain wall. Windows are placed from the
style's floor height and bay width, per exposed wall portion: sashes with
glazing bars, tall classical windows, pointed lancets, glazing grids with
mullions. Ground floors get shopfronts where OSM shows shops or restaurants
inside the footprint. About 30% of windows get a "lit" twin for night.

## 3. Ground, streets and lines

**Carriageway width is measured** (`ground.py`). From stations every 5 m along
each road centreline, perpendicular rays are cast to the 15 km of mapped kerb
lines (`barrier=kerb`) and to building fronts. A kerb hit within a plausible
band gives the half-width directly; otherwise the width follows `width`, lanes
× 3.25 m or the road class, capped to leave ≥ 2 m of pavement in front of
buildings. Half-widths are median-filtered along the road, swept into
polygons, unioned with round junction caps, and closed morphologically.

The surface is then a **partition**: hardscape = region − buildings −
carriageway − green − water. Park paths are cut from the green as surfaced
paths. **Kerbs** are where hardscape meets carriageway: a 120 mm granite upstand
facing the road plus a top strip, drawn as meshes on the merged and simplified
boundary lines. Pavement and carriageway meshes are constrained Delaunay
triangulations draped on the DTM, or on the bridge deck where applicable.

**Road markings** (`streets.py`) are offset curves of each centreline, shifted
to the centre of the measured carriageway, resampled at 0.5 m, then dashed by
arc length to UK TSRGD patterns: centre line 4 m mark / 5 m gap, lane lines
2 m / 7 m, and double no-waiting lines 250 mm from both kerbs (red on red
routes, yellow elsewhere). Zebra crossings get 600 mm bars, zig-zags and
give-way lines. Signal crossings (173 mapped "dots" crossings) get stud lines,
and stop lines on the approach side for left-hand traffic. Everything is
suppressed inside junction discs and crossing zones and clipped to the
carriageway.

**Water.** The Thames polygon is closed from its bank lines and the region
boundary; the river face is the large, flat face with the lowest LiDAR surface.
Its median gives the water level **measured at survey time (3.25 m ODN)**.
Embankment walls with parapets run along the river edge.

**Bridges** (`bridges.py`). The first-return DSM over each `man_made=bridge`
polygon is binned along the bridge's long axis. A 30th-percentile profile
rejects buses and people above the deck, and a moving median smooths it.
River crossings get piers and segmental arches springing just above the
measured water level: seven spans for Westminster Bridge (Commons green) and
five for Lambeth (Lords red) and Waterloo.

## 4. Trees

`trees.py`: canopy height model (CHM) = DSM − DTM. Vegetation is CHM > 2.5 m,
outside dilated footprints, water, bridges and carriageway cores, and either
rough (local RMS residual > 0.22 m, as foliage is noisy while roofs and vehicles
are smooth) or near a mapped tree. Tops are local maxima of the smoothed CHM
with a height-adaptive window (r = 2…5 m for 0…24+ m). A marker-controlled
**watershed** gives crowns, with radius √(area/π). The 2,302 OSM trees snap to
the nearest top within 4 m and take its height and crown; unmatched tops become
new trees. The result is **4.2k trees**, each rendered at its measured size.

## 5. Ambient occlusion from the city itself

`ao.py` bakes sky visibility into vertex colours. For each vertex, 16
azimuths are marched across the DSM with growing steps up to 70 m, recording
the horizon elevation θₖ. The visible-sky fraction is a weighted mean of
1 − sin θₖ, weighted by how much each direction faces the surface; this
restricts vertical walls to their front half-space. This is the heightfield
form of horizon-based AO (Bavoil et al. 2008), evaluated on **measured** city
geometry. Narrow streets, courtyards and wall bases darken where they really
are enclosed.

## 6. Export format

Per 100 m chunk, a binary blob (`export.py`; parser `src/shared/ChunkFormat.luau`):
- **Meshes:** `i16` positions (1/32 stud), `i8` normals, `u8` RGB, `i16` UVs and
  `u16` indices, split under the `EditableMesh` caps (≤ 55k vertices, ≤ 19k
  triangles).
- **Records:** trees, props and collision boxes.

Blobs are zstd-compressed (level 19), base64-encoded and wrapped in Luau
modules. The client decodes them with `EncodingService` (native base64 + zstd).
Terrain is shipped as 64 × 64 column chunks (height, material, water level) for
the server.

| | |
|---|---|
| Buildings | 914 (83 mapper-modelled, 831 LiDAR), 1,771 roof planes + breaklines, 13 courtyards |
| Streets | 1,407 road ways, 239 marked crossings, 427 kerb runs |
| Trees / props | 4.2k / 2.0k |
| Triangles | 0.79 M at LOD0, 73 k at LOD1, 79 k shared street/bridge geometry |
| Encoded size | ≈ 17 MB (325 mesh chunks + 380 terrain chunks) |
| Build time | ≈ 2.5 min on a laptop-class CPU |

## 7. Roblox runtime

- **Server** (`src/server`, `src/worker`). Four Actors decode terrain chunks
  and build voxel arrays in parallel, then call `WriteVoxels` in the serial
  phase, nearest to spawn first. Terrain is the collision ground and renders
  grass (with decoration), park paths and the Thames.
- **Client** (`src/client/World.luau`). Chunks within 1,150 studs get LOD0: full
  facades, roof TINs, kerbs and markings. Chunks out to 4,000 studs get LOD1:
  simplified roofs. Street surfaces are built once. Each mesh is decoded into an
  `EditableMesh` with explicit normals, colours and UVs, centred on its bounding
  box, and turned into a MeshPart with `CreateMeshPartAsync`. Work runs under a
  per-frame budget with at most six meshes in flight.
- **Collision.** Invisible thin wall boxes along footprints and bridge decks,
  created only within ~420 studs of the player. Feet snap to the visual street
  mesh by raycast, because the terrain sits a few centimetres below it.
- **Trees.** Batched meshes of noise-displaced icosphere clusters at the
  measured height and crown radius, with leaf texture through UVs and a single
  blob at distance.
- **Furniture.** Parametric London models from real dimensions: Westminster
  lamp columns, the K6 kiosk, pillar boxes, Belisha beacons (flashing), signal
  heads, bus stops and shelters, Sheffield stands, cycle-hire docks, memorials
  and flagpoles.
- **Landmarks.** The **London Eye**'s wheel plane comes from PCA of the 1,081
  LiDAR returns above 45 m. The wheel turns once every 30 minutes with 32 level
  capsules, and its returns are masked from the DSM before building and tree
  reconstruction. **Big Ben** shows live London time (BST-aware) on its four
  mapped dials, which glow at night.
- **Sky.** Keyframed London light (latitude 51.5°) with five weather states.
  At night lit windows, shopfronts, lanterns (real PointLights near the camera)
  and dials come on.

## 8. Known limitations and next steps

- 1 m LiDAR cannot resolve gothic pinnacles or chimney pots, which are
  represented coarsely unless mapped in 3D. Upgrading to the National LiDAR
  Programme point clouds (classified, multi-return) would allow point-based
  roof segmentation and better trees.
- Facade styles are inferred, not observed. Street-level imagery cannot be
  used (licensing); OS MasterMap / Verisk UKBuildings would give age and
  materials but are not open.
- Carriageway widths come from kerbs for about 12% of stations and are
  estimated elsewhere. Adding OSM `area:highway` polygons and sidewalk centre
  lines as constraints is the next accuracy step.
- Published games need **Game Settings → Security → Allow Mesh & Image APIs**
  (an ID-verified owner is required by Roblox). Studio testing works as is.

## References
- Fischler & Bolles (1981), *Random Sample Consensus*.
- Garland & Heckbert (1995), *Fast Polygonal Approximation of Terrains and Height Fields*.
- Shewchuk (1996), *Triangle: Engineering a 2D Quality Mesh Generator and Delaunay Triangulator*.
- Bavoil, Sainz & Dimitrov (2008), *Image-Space Horizon-Based Ambient Occlusion*.
- Vincent & Soille (1991), *Watersheds in Digital Spaces* (marker-controlled watershed).
- Ordnance Survey (2016), *OSTN15 / OSGM15 transformation*.
- Department for Transport, *Traffic Signs Regulations and General Directions 2016*.
