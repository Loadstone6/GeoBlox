--!strict
-- Reality-inspired synthesis of a Dutch polder road ("slagenlandschap"):
-- a dike road with red cycle strips, roadside ditches, long narrow parcels
-- perpendicular to the road separated by ditches, ribbon farms along the road,
-- a canal crossing with a bridge and a polder mill, tree rows and willows.
--
-- Pure Luau: no Instance access, so the server, client and Lune tests all
-- build the identical layout from the same seed.
--
-- Coordinates: s = position along the road (== world x), side = 1 (+z) or 2 (-z),
-- d = distance from the road centreline (>= 0). Layout.toWorld converts.

local Config = require(script.Parent.Config)
local Rng = require(script.Parent.Rng)
local SurfaceClass = require(script.Parent.SurfaceClass)

export type Building = {
	kind: string,
	ls: number, -- centre along the road, relative to the farm
	d: number, -- centre distance from the road
	w: number, -- size along s
	dep: number, -- size along d
	axis: string, -- ridge direction: "s" or "d"
	seed: number,
}

export type Rect = { ls0: number, ls1: number, d0: number, d1: number }

export type Farm = {
	id: number,
	s: number,
	side: number,
	halfW: number,
	depth: number,
	style: string,
	driveS: number,
	buildings: { Building },
	yard: Rect,
	seed: number,
}

export type Herd = {
	kind: string, -- "cow" | "sheep"
	breed: string,
	count: number,
	side: number,
	s0: number,
	s1: number,
	d0: number,
	d1: number,
	seed: number,
}

export type Parcel = {
	id: number,
	side: number,
	s0: number,
	s1: number,
	crop: number,
	dam: number?,
}

export type Tree = { x: number, z: number, kind: string, h: number, seed: number }
export type Prop = { kind: string, x: number, z: number, yaw: number, len: number, seed: number, variant: number }

export type Layout = {
	seed: number,
	canal: { s: number, half: number, bank: number, bed: number, water: number },
	wetering: { d: number, half: number, bank: number, bed: number, water: number },
	windmill: { s: number, side: number, d: number, radius: number, height: number },
	parcels: { { Parcel } },
	farms: { { Farm } },
	farmList: { Farm },
	trees: { Tree },
	props: { Prop },
	herds: { Herd },
}

local Layout = {}

local function sideSign(side: number): number
	return if side == 1 then 1 else -1
end
Layout.sideSign = sideSign

-- Road centreline z(x) and its slope dz/dx: two gentle sines, like an old polder road.
function Layout.road(x: number): (number, number)
	local a, b = x * 0.00085 + 0.4, x * 0.0021 + 1.3
	return 55 * math.sin(a) + 18 * math.sin(b), 55 * 0.00085 * math.cos(a) + 18 * 0.0021 * math.cos(b)
end

-- Inverse of the sampler's (s, d) mapping: s == x, d is scaled perpendicular offset.
function Layout.toWorld(s: number, side: number, d: number): (number, number)
	local zc, slope = Layout.road(s)
	local norm = 1 / math.sqrt(1 + slope * slope)
	return s, zc + sideSign(side) * d / norm
end

function Layout.findParcel(list: { Parcel }, s: number): Parcel?
	local lo, hi = 1, #list
	while lo <= hi do
		local mid = (lo + hi) // 2
		local p = list[mid]
		if s < p.s0 then
			hi = mid - 1
		elseif s >= p.s1 then
			lo = mid + 1
		else
			return p
		end
	end
	return nil
end

function Layout.findFarm(list: { Farm }, s: number): Farm?
	for _, f in list do
		if math.abs(s - f.s) < f.halfW then
			return f
		end
	end
	return nil
end

local TREE_HEIGHT = {
	poplar = { 78, 100 },
	oak = { 44, 60 },
	ash = { 50, 64 },
	willow = { 15, 21 },
	linden = { 48, 62 },
	alder = { 36, 50 },
	apple = { 12, 16 },
	chestnut = { 46, 60 },
}

local function addTree(L: Layout, rng: Rng.Rng, kind: string, x: number, z: number)
	local range = TREE_HEIGHT[kind]
	table.insert(L.trees, {
		x = x + rng:range(-1, 1),
		z = z + rng:range(-1, 1),
		kind = kind,
		h = rng:range(range[1], range[2]),
		seed = rng:int(0, 2 ^ 31),
	})
end

local function addProp(
	L: Layout,
	rng: Rng.Rng,
	kind: string,
	x: number,
	z: number,
	yaw: number,
	len: number,
	variant: number
)
	table.insert(L.props, {
		kind = kind,
		x = x,
		z = z,
		yaw = yaw,
		len = len,
		seed = rng:int(0, 2 ^ 31),
		variant = variant,
	})
end

local function inRect(r: Rect, ls: number, d: number, margin: number): boolean
	return ls > r.ls0 - margin and ls < r.ls1 + margin and d > r.d0 - margin and d < r.d1 + margin
end

local function buildingRect(b: Building): Rect
	return { ls0 = b.ls - b.w / 2, ls1 = b.ls + b.w / 2, d0 = b.d - b.dep / 2, d1 = b.d + b.dep / 2 }
end

local FARM_STYLES = { "hallehuis", "langgevel", "stolp", "modern" }
local FARM_WEIGHTS = { 30, 20, 25, 25 }

local function farmBuildings(style: string, rng: Rng.Rng): { Building }
	local function b(kind: string, ls: number, d: number, w: number, dep: number, axis: string): Building
		return { kind = kind, ls = ls, d = d, w = w, dep = dep, axis = axis, seed = rng:int(0, 2 ^ 31) }
	end
	if style == "hallehuis" then
		return {
			b("hallehuis", 0, 90, 36, 86, "d"),
			b("oldbarn", rng:range(-12, 12), 182, 50, 40, "s"),
		}
	elseif style == "langgevel" then
		return {
			b("langgevel", -10, 64, 92, 32, "s"),
			b("steelshed", 8, 165, 104, 60, "s"),
		}
	elseif style == "stolp" then
		return {
			b("stolp", 0, 82, 58, 58, "d"),
			b("oldbarn", 46, 168, 36, 46, "d"),
		}
	else
		return {
			b("modern", -40, 60, 34, 30, "s"),
			b("steelshed", 14, 170, 110, 70, "s"),
			b("silo", -62, 128, 14, 14, "d"),
			b("silo", -62, 148, 14, 14, "d"),
			b("mestsilo", 52, 98, 40, 40, "d"),
		}
	end
end

local function makeFarm(L: Layout, id: number, s: number, side: number, rng: Rng.Rng): Farm
	local style = FARM_STYLES[rng:weighted(FARM_WEIGHTS)]
	local buildings = farmBuildings(style, rng)
	local house = buildings[1]

	-- Yard = bounding box of all buildings + margin, with lawn in front of the house.
	local yard: Rect = { ls0 = math.huge, ls1 = -math.huge, d0 = math.huge, d1 = -math.huge }
	for _, b in buildings do
		local r = buildingRect(b)
		yard.ls0 = math.min(yard.ls0, r.ls0 - 12)
		yard.ls1 = math.max(yard.ls1, r.ls1 + 12)
		yard.d0 = math.min(yard.d0, r.d0 - 12)
		yard.d1 = math.max(yard.d1, r.d1 + 14)
	end
	yard.d0 = math.max(yard.d0, house.d - house.dep / 2 + 4)

	local driveLs = house.ls + house.w / 2 + 12
	local farm: Farm = {
		id = id,
		s = s,
		side = side,
		halfW = 86,
		depth = 242,
		style = style,
		driveS = s + driveLs,
		buildings = buildings,
		yard = yard,
		seed = rng:int(0, 2 ^ 31),
	}

	local function world(ls: number, d: number): (number, number)
		return Layout.toWorld(s + ls, side, d)
	end
	local function free(ls: number, d: number, margin: number): boolean
		if math.abs(ls - driveLs) < 10 or inRect(yard, ls, d, margin) then
			return false
		end
		for _, b in buildings do
			if inRect(buildingRect(b), ls, d, margin + 4) then
				return false
			end
		end
		return true
	end

	-- Big trees on the front lawn, by the road.
	for _, ls in { -70, -48, 48, 70 } do
		if rng:chance(0.7) and free(ls, 41, 2) then
			local x, z = world(ls + rng:range(-4, 4), 41 + rng:range(-2, 3))
			addTree(L, rng, if rng:chance(0.5) then "linden" else "chestnut", x, z)
		end
	end

	-- Windbreak behind the yard, sometimes a side row.
	local wbKind = if rng:chance(0.5) then "alder" else "ash"
	for ls = -78, 78, 17 do
		local x, z = world(ls, farm.depth - 10)
		addTree(L, rng, wbKind, x, z)
	end
	if rng:chance(0.5) then
		local sideLs = if rng:chance(0.5) then -80 else 80
		for d = 70, 220, 19 do
			if free(sideLs, d, 0) then
				local x, z = world(sideLs, d)
				addTree(L, rng, wbKind, x, z)
			end
		end
	end

	-- Small orchard on free lawn.
	if rng:chance(0.4) then
		for ls = -74, 74, 14 do
			for d = 52, 150, 14 do
				if free(ls, d, 6) then
					local x, z = world(ls, d)
					addTree(L, rng, "apple", x, z)
				end
			end
		end
	end

	-- Front hedge with a gap for the drive.
	if rng:chance(0.55) then
		for ls = -80, 72, 16 do
			if math.abs(ls + 8 - driveLs) > 14 then
				local x, z = world(ls + 8, 36)
				addProp(L, rng, "hedge", x, z, 0, 16, 0)
			end
		end
	end

	-- Wrapped silage bales next to the main barn.
	local barn = buildings[2]
	local balesLs = barn.ls - barn.w / 2 - 9
	local n = rng:int(4, 9)
	local variant = rng:weighted({ 5, 3, 2 }) -- white, black, green wrap
	for i = 0, n - 1 do
		local x, z = world(balesLs, barn.d - barn.dep / 2 + 4 + i * 4.4)
		addProp(L, rng, "bale", x, z, 0, 2, variant)
		if i < n - 1 and rng:chance(0.6) then
			addProp(L, rng, "bale", x, z, 0, 2, variant + 10) -- +10 = stacked on top
		end
	end

	-- Mailbox by the drive.
	local mx, mz = world(driveLs + 9, 34)
	addProp(L, rng, "mailbox", mx, mz, 0, 0, 0)

	return farm
end

function Layout.build(seed: number?): Layout
	local seedValue = seed or Config.SEED
	local rng = Rng.new(seedValue)
	local L: Layout = {
		seed = seedValue,
		canal = { s = 1100, half = 10, bank = 4, bed = 16, water = Config.WATER_Y },
		wetering = { d = 760, half = 8, bank = 3, bed = 20, water = Config.WATER_Y },
		windmill = { s = 1195, side = 1, d = 190, radius = 44, height = 6 },
		parcels = { {}, {} },
		farms = { {}, {} },
		farmList = {},
		trees = {},
		props = {},
		herds = {},
	}

	-- Ribbon farms along the road.
	local s = Config.X_MIN + 260
	local farmId = 0
	while true do
		s += rng:range(430, 760)
		if s > Config.X_MAX - 260 then
			break
		end
		if math.abs(s - L.canal.s) < 280 or math.abs(s - Config.SPAWN_S) < 110 then
			continue
		end
		farmId += 1
		local side = if rng:chance(0.5) then 1 else 2
		local farm = makeFarm(L, farmId, s, side, Rng.new(Rng.hash(seedValue, 11, farmId)))
		table.insert(L.farms[side], farm)
		table.insert(L.farmList, farm)
	end

	-- Parcels: long strips perpendicular to the road on both sides.
	local function boundaryAllowed(b: number, side: number): boolean
		if math.abs(b - L.canal.s) < 20 and b ~= L.canal.s then
			return false
		end
		if side == L.windmill.side and math.abs(b - L.windmill.s) < L.windmill.radius + 8 then
			return false
		end
		return true
	end

	local parcelId = 0
	for side = 1, 2 do
		local list = L.parcels[side]
		local b = Config.X_MIN - rng:range(0, 150)
		while b < Config.X_MAX do
			local nb = b + rng:range(110, 220)
			while not boundaryAllowed(nb, side) do
				nb += 9
			end
			if b < L.canal.s and nb > L.canal.s then
				nb = L.canal.s -- the canal itself is the parcel boundary
			end
			parcelId += 1
			table.insert(list, {
				id = parcelId,
				side = side,
				s0 = b,
				s1 = nb,
				crop = SurfaceClass.CROPS[rng:weighted(SurfaceClass.CROP_WEIGHTS)],
				dam = nil,
			})
			b = nb
		end
	end

	-- Per-parcel details: field entrances (dam + gate), herds and willow rows.
	for side = 1, 2 do
		for i, p in L.parcels[side] do
			local prng = Rng.new(Rng.hash(seedValue, 23, p.id))
			local mid = (p.s0 + p.s1) / 2
			local width = p.s1 - p.s0
			local overlapFarm: Farm? = nil
			for _, f in L.farms[side] do
				if p.s1 > f.s - f.halfW - 12 and p.s0 < f.s + f.halfW + 12 then
					overlapFarm = f
				end
			end

			local dam = math.clamp(mid + prng:range(-0.3, 0.3) * width, p.s0 + 14, p.s1 - 14)
			local nearFarm = false
			for _, f in L.farms[side] do
				if math.abs(dam - f.s) < f.halfW + 10 then
					nearFarm = true
				end
			end
			if not nearFarm and math.abs(dam - L.canal.s) > 40 then
				p.dam = dam
				local x, z = Layout.toWorld(dam, side, 33)
				addProp(L, prng, "gate", x, z, 0, 12, 0)
			end

			if p.crop == SurfaceClass.Pasture then
				local d0 = if overlapFarm then overlapFarm.depth + 18 else 48
				local roll = prng:next()
				if roll < 0.45 then
					local breeds = { "holstein", "mrij", "blaarkop", "lakenvelder" }
					table.insert(L.herds, {
						kind = "cow",
						breed = breeds[prng:weighted({ 60, 20, 14, 6 })],
						count = prng:int(4, 11),
						side = side,
						s0 = p.s0 + 8,
						s1 = p.s1 - 8,
						d0 = d0,
						d1 = d0 + prng:range(180, 420),
						seed = prng:int(0, 2 ^ 31),
					})
				elseif roll < 0.6 then
					table.insert(L.herds, {
						kind = "sheep",
						breed = "texel",
						count = prng:int(8, 18),
						side = side,
						s0 = p.s0 + 8,
						s1 = p.s1 - 8,
						d0 = d0,
						d1 = d0 + prng:range(150, 380),
						seed = prng:int(0, 2 ^ 31),
					})
				end
			end

			-- Pollard willows along the parcel's leading ditch.
			if i > 1 and prng:chance(0.22) and math.abs(p.s0 - L.canal.s) > 40 then
				local off = if prng:chance(0.5) then 4.5 else -4.5
				local dStart = if overlapFarm and math.abs(p.s0 - overlapFarm.s) < overlapFarm.halfW + 10
					then overlapFarm.depth + 10
					else 46
				local spacing = prng:range(26, 34)
				local d = dStart
				while d < 740 do
					if not (side == L.windmill.side and math.abs(p.s0 - L.windmill.s) < 60 and d < 260) then
						local x, z = Layout.toWorld(p.s0 + off, side, d)
						addTree(L, prng, "willow", x, z)
					end
					d += spacing + prng:range(-3, 3)
				end
			end
		end
	end

	-- Road tree rows in segments: poplars, oaks, ash, or open stretches.
	local trng = Rng.new(Rng.hash(seedValue, 31))
	local segS = Config.X_MIN
	while segS < Config.X_MAX do
		local segLen = trng:range(480, 1000)
		local kindIdx = trng:weighted({ 22, 18, 20, 10, 30 })
		local kinds = { "poplar", "poplar", "oak", "ash", "" }
		local kind = kinds[kindIdx]
		local sides: { number } = if kindIdx == 2 or kindIdx == 4
			then { if trng:chance(0.5) then 1 else 2 }
			else { 1, 2 }
		local spacing = if kind == "poplar" then 25 elseif kind == "oak" then 32 else 27
		if kind ~= "" then
			local ts = segS
			while ts < math.min(segS + segLen, Config.X_MAX) do
				for _, side in sides do
					local blocked = math.abs(ts - L.canal.s) < 32 or math.abs(ts - Config.SPAWN_S) < 16
					for _, f in L.farms[side] do
						if math.abs(ts - f.driveS) < 13 then
							blocked = true
						end
					end
					local p = Layout.findParcel(L.parcels[side], ts)
					if p and p.dam and math.abs(ts - p.dam) < 10 then
						blocked = true
					end
					if not blocked then
						local x, z = Layout.toWorld(ts, side, 14.5 + trng:range(-0.5, 0.5))
						addTree(L, trng, kind, x, z)
					end
				end
				ts += spacing + trng:range(-2, 2)
			end
		end
		segS += segLen
	end

	return L
end

return Layout
