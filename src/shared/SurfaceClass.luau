--!strict
-- Surface classes: what a terrain column *is*. Materials.luau maps them to
-- Roblox terrain materials and colours per season.

local SurfaceClass = {
	Pasture = 1,
	Maize = 2,
	Wheat = 3,
	Potato = 4,
	Tulip = 5,
	Daffodil = 6,
	Hyacinth = 7,
	Plough = 8,

	Road = 10,
	Verge = 11,
	DikeSlope = 12,
	Bank = 13,
	BankWet = 14,
	Yard = 15,
	Drive = 16,
	Lawn = 17,
	Track = 18,
	Mound = 19,
	Headland = 20,
	Subsoil = 30,
}

-- Crops that are planted in visible rows (alternating with bare soil).
SurfaceClass.ROW_CROPS = {
	[SurfaceClass.Potato] = true,
	[SurfaceClass.Tulip] = true,
	[SurfaceClass.Daffodil] = true,
	[SurfaceClass.Hyacinth] = true,
}

-- Crop frequencies for synthesis (roughly the Dutch peat/clay polder mix).
SurfaceClass.CROPS = {
	SurfaceClass.Pasture,
	SurfaceClass.Maize,
	SurfaceClass.Wheat,
	SurfaceClass.Potato,
	SurfaceClass.Tulip,
	SurfaceClass.Daffodil,
	SurfaceClass.Hyacinth,
	SurfaceClass.Plough,
}
SurfaceClass.CROP_WEIGHTS = { 52, 12, 9, 7, 7, 4, 3, 6 }

return SurfaceClass
