--!strict
-- Runtime settings for the reconstructed city. Geometry itself comes from the
-- generated chunks (see pipeline/); these only control streaming and looks.

local Config = {}

-- Level-of-detail rings (studs from the camera). LOD0 = full reconstruction
-- (windows, markings, kerbs); LOD1 = simplified roofs, no facade detail.
Config.LOD0_RADIUS = 1150
Config.LOD1_RADIUS = 4000
Config.HYSTERESIS = 120

-- Per-frame work budget for building meshes and decor (milliseconds).
Config.BUILD_BUDGET_MS = 6
Config.MAX_CONCURRENT_MESHPARTS = 6

-- Collision proxies (thin invisible walls) are only created near the player.
Config.COLLISION_RADIUS = 420

-- Trees and street furniture.
Config.TREE_LOD0_RADIUS = 900
Config.PROP_RADIUS = 700
Config.LAMP_LIGHT_RADIUS = 260
Config.MAX_LAMP_LIGHTS = 48

-- If faces render inside-out on some future engine version, flip this.
Config.FLIP_WINDING = false

return Config
