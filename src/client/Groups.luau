--!strict
-- How each exported geometry group renders. Colours come from per-vertex data
-- (baked ambient occlusion × measured/inferred surface colour); materials add
-- surface texture through the mesh UVs (1 UV unit = 2 m on walls and roofs).

local M = Enum.Material

export type GroupStyle = {
	material: Enum.Material,
	reflectance: number,
	shadow: boolean,
	night: boolean?, -- only visible at night (lit windows)
	glowAtNight: boolean?,
}

local Groups: { [string]: GroupStyle } = {
	road = { material = M.Asphalt, reflectance = 0, shadow = false },
	pavement = { material = M.Pavement, reflectance = 0, shadow = false },
	kerb = { material = M.Granite, reflectance = 0, shadow = false },
	embankment = { material = M.Granite, reflectance = 0, shadow = true },
	bridge = { material = M.Concrete, reflectance = 0, shadow = true },
	marking_white = { material = M.SmoothPlastic, reflectance = 0, shadow = false },
	marking_yellow = { material = M.SmoothPlastic, reflectance = 0, shadow = false },
	marking_red = { material = M.SmoothPlastic, reflectance = 0, shadow = false },
	roof_pitched = { material = M.Slate, reflectance = 0, shadow = true },
	roof_flat = { material = M.Concrete, reflectance = 0, shadow = true },
	roof_metal = { material = M.Metal, reflectance = 0.08, shadow = true },
	roof_glass = { material = M.Glass, reflectance = 0.3, shadow = true },
	gilt = { material = M.Foil, reflectance = 0.12, shadow = true },
	wall_brick = { material = M.Brick, reflectance = 0, shadow = true },
	wall_stone = { material = M.Limestone, reflectance = 0, shadow = true },
	wall_concrete = { material = M.Concrete, reflectance = 0, shadow = true },
	wall_plaster = { material = M.Plaster, reflectance = 0, shadow = true },
	wall_glass = { material = M.Glass, reflectance = 0.35, shadow = true },
	wall_metal = { material = M.Metal, reflectance = 0.08, shadow = true },
	window_frame = { material = M.SmoothPlastic, reflectance = 0, shadow = false },
	window_glass = { material = M.Glass, reflectance = 0.32, shadow = false },
	curtain_glass = { material = M.Glass, reflectance = 0.45, shadow = true },
	window_lit = { material = M.Neon, reflectance = 0, shadow = false, night = true },
	shopfront = { material = M.Glass, reflectance = 0.25, shadow = false, glowAtNight = true },
}

return Groups
