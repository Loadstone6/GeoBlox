--!strict
--!native
-- Column sampler: for any world (x, z) returns the ground height (studs),
-- the surface class, and the water surface height (or -1 when dry).
-- Pure Luau and deterministic; terrain, decor and animals all ask it.

local Config = require(script.Parent.Config)
local Layout = require(script.Parent.Layout)
local SurfaceClass = require(script.Parent.SurfaceClass)

local F = Config.FIELD_Y
local ROAD_Y = Config.ROAD_Y
local WATER = Config.WATER_Y
local BED = Config.DITCH_BED

local C_ROAD = SurfaceClass.Road
local C_VERGE = SurfaceClass.Verge
local C_DIKE = SurfaceClass.DikeSlope
local C_BANK = SurfaceClass.Bank
local C_WET = SurfaceClass.BankWet
local C_PASTURE = SurfaceClass.Pasture
local C_PLOUGH = SurfaceClass.Plough
local C_HEADLAND = SurfaceClass.Headland
local ROW_CROPS = SurfaceClass.ROW_CROPS

export type Sampler = {
	layout: Layout.Layout,
	lastX: number,
	zc: number,
	norm: number,
	parcel: { Layout.Parcel? },
	farm: { Layout.Farm? },
	canalDist: number,
	sample: (self: Sampler, x: number, z: number) -> (number, number, number),
	height: (self: Sampler, x: number, z: number) -> number,
}

local Sampler = {}
Sampler.__index = Sampler

local function smooth(t: number): number
	t = math.clamp(t, 0, 1)
	return t * t * (3 - 2 * t)
end

local function lerp(a: number, b: number, t: number): number
	return a + (b - a) * t
end

function Sampler.new(layout: Layout.Layout): Sampler
	local self = setmetatable({
		layout = layout,
		lastX = math.huge,
		zc = 0,
		norm = 1,
		parcel = {},
		farm = {},
		canalDist = 0,
	}, Sampler)
	return (self :: any) :: Sampler
end

-- Everything that depends only on x is cached: terrain chunks are filled in
-- x-major order, so this runs once per 64 columns.
local function column(self: Sampler, x: number)
	if x == self.lastX then
		return
	end
	self.lastX = x
	local zc, slope = Layout.road(x)
	self.zc = zc
	self.norm = 1 / math.sqrt(1 + slope * slope)
	local L = self.layout
	self.parcel[1] = Layout.findParcel(L.parcels[1], x)
	self.parcel[2] = Layout.findParcel(L.parcels[2], x)
	self.farm[1] = Layout.findFarm(L.farms[1], x)
	self.farm[2] = Layout.findFarm(L.farms[2], x)
	self.canalDist = math.abs(x - L.canal.s)
end

-- Water body cross-section: flat bed, smooth banks up to field level.
local function channel(dist: number, half: number, bank: number, bed: number, top: number): (number, number)
	if dist <= half - bank then
		return bed, C_WET
	end
	local h = lerp(bed, top, smooth((dist - (half - bank)) / bank))
	return h, if h < WATER + 0.5 then C_WET else C_BANK
end

local function farmSample(farm: Layout.Farm, s: number, ad: number): (number, number, number)
	local ls = s - farm.s
	local edge = math.min(farm.halfW - math.abs(ls), farm.depth - ad, ad - Config.PARCEL_START)
	local h = F + 1.2 * smooth(edge / 14) -- low terp the farm sits on
	if math.abs(s - farm.driveS) < 6 and ad < farm.yard.d0 + 4 then
		return h, SurfaceClass.Drive, -1
	end
	local y = farm.yard
	if ls > y.ls0 and ls < y.ls1 and ad > y.d0 and ad < y.d1 then
		return h, SurfaceClass.Yard, -1
	end
	return h, SurfaceClass.Lawn, -1
end

function Sampler.sample(self: Sampler, x: number, z: number): (number, number, number)
	column(self, x)
	local L = self.layout
	local d = (z - self.zc) * self.norm
	local ad = math.abs(d)
	local side = if d >= 0 then 1 else 2
	local s = x

	-- 1. Canal crossing the road (passes under the bridge).
	local C = L.canal
	if self.canalDist < C.half + C.bank then
		local h, cls = channel(self.canalDist, C.half + C.bank, C.bank, C.bed, F)
		return h, cls, C.water
	end

	-- 2. Wetering: wide water course parallel to the road.
	local W = L.wetering
	local wd = math.abs(ad - W.d)
	if wd < W.half + W.bank then
		local h, cls = channel(wd, W.half + W.bank, W.bank, W.bed, F)
		return h, cls, W.water
	end

	-- 3. Road on its dike.
	if ad < Config.ROAD_HALF then
		return ROAD_Y, C_ROAD, -1
	end
	if ad < Config.VERGE then
		return ROAD_Y, C_VERGE, -1
	end
	if ad < Config.DIKE_FOOT then
		return lerp(ROAD_Y, F, smooth((ad - Config.VERGE) / (Config.DIKE_FOOT - Config.VERGE))), C_DIKE, -1
	end

	local farm = self.farm[side]
	local parcel = self.parcel[side]

	-- 4. Roadside ditch, crossed by dams at farm drives and field entrances.
	if ad < Config.PARCEL_START then
		if farm and math.abs(s - farm.driveS) < 6 then
			return F, SurfaceClass.Drive, -1
		end
		if parcel and parcel.dam and math.abs(s - parcel.dam) < 6 then
			return F, SurfaceClass.Track, -1
		end
		local t = math.abs(ad - Config.ROAD_DITCH)
		if t < 2 then
			return BED, C_WET, WATER
		end
		local h = lerp(BED, F, smooth((t - 2) / (Config.ROAD_DITCH_HALF - 2)))
		return h, if h < WATER + 0.5 then C_WET else C_BANK, WATER
	end

	-- 5. Farm yard.
	if farm and ad < farm.depth then
		return farmSample(farm, s, ad)
	end

	-- 6. Polder mill on its mound.
	local M = L.windmill
	if side == M.side then
		local dx, dd = s - M.s, ad - M.d
		local r = math.sqrt(dx * dx + dd * dd)
		if r < M.radius then
			local h = F + M.height * smooth((M.radius - r) / (M.radius * 0.75))
			return h, if r < 15 then SurfaceClass.Track else SurfaceClass.Mound, -1
		end
	end

	-- 7. Parcels with ditches on their boundaries.
	if not parcel then
		return F, C_PASTURE, -1
	end
	local e = math.min(s - parcel.s0, parcel.s1 - s)
	if e < 3 then
		local h = if e < 1 then BED else lerp(BED, F, smooth((e - 1) / 2))
		return h, if h < WATER + 0.5 then C_WET else C_BANK, WATER
	end

	local half = (parcel.s1 - parcel.s0) / 2
	local u = (s - (parcel.s0 + half)) / half
	local h = F + 0.9 * (1 - u * u) -- "bolle akker": crowned field for drainage
	local crop = parcel.crop
	if crop == C_PASTURE then
		h += math.noise(x * 0.021, z * 0.021, 1.7) * 0.7 + math.noise(x * 0.08, z * 0.08, 4.2) * 0.25
		return h, C_PASTURE, -1
	end
	if ad < Config.PARCEL_START + 7 then
		return h, C_HEADLAND, -1 -- grass headland along the road side
	end
	if ROW_CROPS[crop] and (x // 4) % 2 == 1 then
		return h, C_PLOUGH, -1
	end
	return h, crop, -1
end

function Sampler.height(self: Sampler, x: number, z: number): number
	local h = self:sample(x, z)
	return h
end

return Sampler
