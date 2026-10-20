--!strict
-- Surface class -> terrain material, and per-season terrain/foliage palettes.
-- Terrain has one colour per material, so seasons recolour materials instead
-- of rewriting voxels.

local SurfaceClass = require(script.Parent.SurfaceClass)

local M = Enum.Material
local Materials = {}

Materials.CLASS_TO_MATERIAL = {
	[SurfaceClass.Pasture] = M.Grass,
	[SurfaceClass.Maize] = M.LeafyGrass,
	[SurfaceClass.Wheat] = M.Sand,
	[SurfaceClass.Potato] = M.Limestone,
	[SurfaceClass.Tulip] = M.Salt,
	[SurfaceClass.Daffodil] = M.Sandstone,
	[SurfaceClass.Hyacinth] = M.Snow,
	[SurfaceClass.Plough] = M.Ground,
	[SurfaceClass.Road] = M.Asphalt,
	[SurfaceClass.Verge] = M.Grass,
	[SurfaceClass.DikeSlope] = M.Grass,
	[SurfaceClass.Bank] = M.Grass,
	[SurfaceClass.BankWet] = M.Mud,
	[SurfaceClass.Yard] = M.Concrete,
	[SurfaceClass.Drive] = M.Brick,
	[SurfaceClass.Lawn] = M.Grass,
	[SurfaceClass.Track] = M.Pavement,
	[SurfaceClass.Mound] = M.Grass,
	[SurfaceClass.Headland] = M.Grass,
	[SurfaceClass.Subsoil] = M.Ground,
} :: { [number]: Enum.Material }

local function rgb(r: number, g: number, b: number): Color3
	return Color3.fromRGB(r, g, b)
end

export type Palette = {
	terrain: { [Enum.Material]: Color3 },
	leaves: { Color3 },
	leafTransparency: number,
	willow: Color3,
	conifer: Color3,
	horizon: { Color3 },
}

local soil = rgb(112, 86, 60)

Materials.SEASONS = {
	"Spring",
	"Summer",
	"Autumn",
	"Winter",
}

Materials.PALETTES = {
	Spring = {
		terrain = {
			[M.Grass] = rgb(92, 130, 52),
			[M.LeafyGrass] = rgb(118, 104, 72), -- maize just sown
			[M.Sand] = rgb(112, 138, 62), -- young wheat
			[M.Limestone] = rgb(120, 96, 68), -- potato ridges
			[M.Salt] = rgb(206, 38, 52), -- tulips
			[M.Sandstone] = rgb(236, 198, 44), -- daffodils
			[M.Snow] = rgb(118, 92, 196), -- hyacinths
			[M.Ground] = soil,
			[M.Mud] = rgb(74, 64, 48),
			[M.Asphalt] = rgb(64, 64, 66),
			[M.Concrete] = rgb(156, 154, 146),
			[M.Brick] = rgb(146, 82, 62),
			[M.Pavement] = rgb(136, 130, 118),
		},
		leaves = { rgb(118, 158, 62), rgb(104, 146, 56), rgb(130, 166, 72) },
		leafTransparency = 0,
		willow = rgb(150, 170, 110),
		conifer = rgb(48, 74, 44),
		horizon = { rgb(92, 128, 52), rgb(100, 136, 58), rgb(112, 138, 62), rgb(116, 92, 66) },
	},
	Summer = {
		terrain = {
			[M.Grass] = rgb(88, 122, 48),
			[M.LeafyGrass] = rgb(58, 98, 36), -- tall maize
			[M.Sand] = rgb(206, 174, 96), -- ripe wheat
			[M.Limestone] = rgb(76, 112, 50), -- potato leaf
			[M.Salt] = soil,
			[M.Sandstone] = soil,
			[M.Snow] = soil,
			[M.Ground] = soil,
			[M.Mud] = rgb(72, 62, 46),
			[M.Asphalt] = rgb(66, 66, 68),
			[M.Concrete] = rgb(160, 158, 150),
			[M.Brick] = rgb(146, 82, 62),
			[M.Pavement] = rgb(140, 134, 122),
		},
		leaves = { rgb(70, 108, 44), rgb(62, 98, 40), rgb(80, 116, 50) },
		leafTransparency = 0,
		willow = rgb(132, 152, 100),
		conifer = rgb(44, 70, 40),
		horizon = { rgb(86, 120, 46), rgb(60, 98, 38), rgb(204, 172, 96), rgb(80, 114, 50) },
	},
	Autumn = {
		terrain = {
			[M.Grass] = rgb(104, 118, 58),
			[M.LeafyGrass] = rgb(152, 132, 86), -- maize stubble
			[M.Sand] = rgb(176, 156, 108), -- straw stubble
			[M.Limestone] = rgb(110, 88, 64),
			[M.Salt] = soil,
			[M.Sandstone] = soil,
			[M.Snow] = soil,
			[M.Ground] = rgb(100, 78, 56),
			[M.Mud] = rgb(66, 56, 42),
			[M.Asphalt] = rgb(60, 60, 62),
			[M.Concrete] = rgb(150, 148, 140),
			[M.Brick] = rgb(140, 78, 60),
			[M.Pavement] = rgb(128, 122, 110),
		},
		leaves = { rgb(176, 120, 44), rgb(150, 132, 52), rgb(120, 112, 48), rgb(168, 84, 40) },
		leafTransparency = 0,
		willow = rgb(150, 150, 96),
		conifer = rgb(44, 66, 40),
		horizon = { rgb(104, 118, 58), rgb(150, 130, 86), rgb(100, 78, 56), rgb(176, 156, 108) },
	},
	Winter = {
		terrain = {
			[M.Grass] = rgb(100, 112, 70),
			[M.LeafyGrass] = rgb(96, 80, 58),
			[M.Sand] = rgb(104, 120, 70), -- winter wheat
			[M.Limestone] = rgb(98, 78, 58),
			[M.Salt] = rgb(98, 78, 58),
			[M.Sandstone] = rgb(98, 78, 58),
			[M.Snow] = rgb(98, 78, 58),
			[M.Ground] = rgb(96, 76, 56),
			[M.Mud] = rgb(62, 54, 42),
			[M.Asphalt] = rgb(70, 70, 72),
			[M.Concrete] = rgb(146, 146, 142),
			[M.Brick] = rgb(132, 76, 60),
			[M.Pavement] = rgb(124, 120, 112),
		},
		leaves = { rgb(96, 86, 74), rgb(86, 78, 70) },
		leafTransparency = 0.72,
		willow = rgb(110, 100, 84),
		conifer = rgb(42, 62, 42),
		horizon = { rgb(100, 112, 70), rgb(96, 78, 58), rgb(104, 120, 70), rgb(92, 76, 56) },
	},
} :: { [string]: Palette }

function Materials.applyTerrain(terrain: Terrain, season: string)
	local palette = Materials.PALETTES[season]
	for material, color in palette.terrain do
		terrain:SetMaterialColor(material, color)
	end
	terrain.WaterColor = if season == "Summer" then rgb(52, 70, 56) else rgb(46, 62, 58)
	terrain.WaterTransparency = 0.35
	terrain.WaterReflectance = 0.6
	terrain.WaterWaveSize = 0.08
	terrain.WaterWaveSpeed = 6
end

return Materials
