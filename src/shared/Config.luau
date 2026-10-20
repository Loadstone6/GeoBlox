--!strict
-- Global constants for the practice road. All distances are in studs unless the
-- name says otherwise. Convert real-world metres with Config.m().

local Config = {}

Config.SEED = 20261020
Config.MPS = 0.3 -- metres per stud (avatar ~1.65 m, terrain voxel 1.2 m)

-- Terrain grid
Config.VOXEL = 4
Config.CHUNK_VOXELS = 64
Config.CHUNK_STUDS = 256
Config.CHUNK_X0 = -10 -- inclusive chunk index range along x (road direction)
Config.CHUNK_X1 = 9
Config.CHUNK_Z0 = -4
Config.CHUNK_Z1 = 3
Config.Y_MIN = 0 -- bottom of the written voxel band
Config.Y_MAX = 48 -- top of the written voxel band (12 voxels)

-- Derived region bounds
Config.X_MIN = Config.CHUNK_X0 * Config.CHUNK_STUDS
Config.X_MAX = (Config.CHUNK_X1 + 1) * Config.CHUNK_STUDS
Config.Z_MIN = Config.CHUNK_Z0 * Config.CHUNK_STUDS
Config.Z_MAX = (Config.CHUNK_Z1 + 1) * Config.CHUNK_STUDS

-- Vertical profile (studs). Heights sit on voxel boundaries where flatness matters.
Config.FIELD_Y = 28 -- polder field level
Config.ROAD_Y = 32 -- road on a low dike (+1.2 m)
Config.WATER_Y = 26 -- ditch / canal water level (0.6 m below the fields)
Config.DITCH_BED = 24

-- Road cross-section (distance from the centreline)
Config.ROAD_HALF = 10 -- asphalt incl. red cycle strips (~6 m total)
Config.STRIP_IN = 4.5 -- red "suggestiestrook" from STRIP_IN to STRIP_OUT
Config.STRIP_OUT = 9.5
Config.VERGE = 12
Config.DIKE_FOOT = 19
Config.ROAD_DITCH = 25 -- centre of the roadside ditch
Config.ROAD_DITCH_HALF = 6
Config.PARCEL_START = 31

-- Runtime
Config.PARALLEL = true -- compute terrain in Actors; set false to debug serially
Config.SPAWN_S = -2300 -- spawn position along the road

-- Client decor rings (studs from the camera)
Config.DECOR_NEAR = 700
Config.DECOR_FAR = 1800
Config.DECOR_BUDGET_MS = 3
Config.ANIMAL_RADIUS = 450

function Config.m(metres: number): number
	return metres / Config.MPS
end

return Config
