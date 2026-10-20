--!nocheck
-- Lune test runner for the pure world-generation modules.
-- Usage: lune run tests/run

local Harness = require("./harness")
local Enum = require("@lune/roblox").Enum

local SRC = "src/shared"
local Config = Harness.require(SRC .. "/Config")
local Rng = Harness.require(SRC .. "/Rng")
local Layout = Harness.require(SRC .. "/Layout")
local Sampler = Harness.require(SRC .. "/Sampler")
local SurfaceClass = Harness.require(SRC .. "/SurfaceClass")

local failures, passed = 0, 0
local function check(name, cond, detail)
	if cond then
		passed += 1
	else
		failures += 1
		print("FAIL  " .. name .. (if detail then ("  (" .. tostring(detail) .. ")") else ""))
	end
end

-- Rng: determinism and range
do
	local a, b = Rng.new(42), Rng.new(42)
	local same, inRange = true, true
	local sum = 0
	for _ = 1, 10000 do
		local x, y = a:next(), b:next()
		same = same and x == y
		inRange = inRange and x >= 0 and x < 1
		sum += x
	end
	check("rng deterministic", same)
	check("rng in [0,1)", inRange)
	check("rng mean ~0.5", math.abs(sum / 10000 - 0.5) < 0.02, sum / 10000)
	check("hash stable", Rng.hash(1, 2, 3) == Rng.hash(1, 2, 3) and Rng.hash(1, 2, 3) ~= Rng.hash(3, 2, 1))
	check("hash negative ok", Rng.hash(-5, -7) >= 0)
end

-- Layout: deterministic and sane
local L = Layout.build(Config.SEED)
do
	local L2 = Layout.build(Config.SEED)
	check("layout deterministic: trees", #L.trees == #L2.trees and L.trees[#L.trees].x == L2.trees[#L2.trees].x)
	check("layout deterministic: farms", #L.farmList == #L2.farmList)
	check("has farms", #L.farmList >= 5, #L.farmList)
	check("has trees", #L.trees > 300, #L.trees)
	check("has herds", #L.herds > 5, #L.herds)
	for side = 1, 2 do
		local list = L.parcels[side]
		local contiguous = true
		for i = 2, #list do
			contiguous = contiguous and list[i].s0 == list[i - 1].s1
		end
		check("parcels contiguous side " .. side, contiguous)
		check("parcels cover region side " .. side, list[1].s0 <= Config.X_MIN and list[#list].s1 >= Config.X_MAX)
	end
	for _, f in L.farmList do
		for _, b in f.buildings do
			local inside = math.abs(b.ls) + b.w / 2 <= f.halfW
				and b.d - b.dep / 2 > Config.PARCEL_START
				and b.d + b.dep / 2 < f.depth
			check("building inside farm " .. f.id .. " " .. b.kind, inside)
		end
		check("farm clear of canal " .. f.id, math.abs(f.s - L.canal.s) > 200)
	end
end

-- Sampler: every column in the region
local smp = Sampler.new(L)
do
	local t0 = os.clock()
	local columns, bad, waterOk, roads = 0, 0, true, 0
	local minH, maxH = math.huge, -math.huge
	for x = Config.X_MIN + 2, Config.X_MAX - 2, 4 do
		for z = Config.Z_MIN + 2, Config.Z_MAX - 2, 4 do
			local h, cls, water = smp:sample(x, z)
			columns += 1
			minH, maxH = math.min(minH, h), math.max(maxH, h)
			if h < Config.Y_MIN + 4 or h > Config.Y_MAX - 4 or h ~= h then
				bad += 1
			end
			if cls == SurfaceClass.Road then
				roads += 1
			end
			if water > 0 and h > water + 2.5 then
				waterOk = false
			end
		end
	end
	local dt = os.clock() - t0
	check("heights within voxel band", bad == 0, string.format("%d bad, range %.1f..%.1f", bad, minH, maxH))
	check("water only over low ground", waterOk)
	check("road present", roads > 2000, roads)
	print(
		string.format(
			"sampled %d columns in %.2fs (%.2f us/column), height %.1f..%.1f",
			columns,
			dt,
			dt / columns * 1e6,
			minH,
			maxH
		)
	)
end

-- Road centre is road, spawn is on the road
do
	local x, z = Layout.toWorld(Config.SPAWN_S, 1, 0)
	local h, cls = smp:sample(x, z)
	check("spawn on road", cls == SurfaceClass.Road and h == Config.ROAD_Y, cls)
	local hx, hz = Layout.toWorld(0, 2, 25)
	local _, ditchCls, water = smp:sample(hx, hz)
	check("roadside ditch has water", water > 0 or ditchCls == SurfaceClass.Track or ditchCls == SurfaceClass.Drive)
end

-- Terrain chunk voxel arrays (uses Lune's Roblox datatypes for Enum/Region3)
do
	local TerrainChunk = Harness.require(SRC .. "/TerrainChunk")
	local NY = (Config.Y_MAX - Config.Y_MIN) // Config.VOXEL
	local cx, cz = 4, -1 -- contains the canal, road, ditches and the mill
	local _, mats, occs = TerrainChunk.compute(cx, cz)
	check("chunk x size", #mats == 64 and #occs == 64)
	check("chunk y size", #mats[1] == NY, #mats[1])
	check("chunk z size", #mats[1][1] == 64)
	local okOcc, water, air, solid = true, 0, 0, 0
	for i = 1, 64 do
		for j = 1, NY do
			for k = 1, 64 do
				local o, m = occs[i][j][k], mats[i][j][k]
				okOcc = okOcc and o >= 0 and o <= 1
				if m == Enum.Material.Water then
					water += 1
				elseif m == Enum.Material.Air then
					air += 1
				else
					solid += 1
				end
			end
		end
	end
	check("occupancy in [0,1]", okOcc)
	check("chunk has water", water > 500, water)
	check("chunk has solid ground", solid > 64 * 64 * 5, solid)
	local t0 = os.clock()
	local n = 0
	for x = Config.CHUNK_X0, Config.CHUNK_X0 + 3 do
		for z = Config.CHUNK_Z0, Config.CHUNK_Z1 do
			TerrainChunk.compute(x, z)
			n += 1
		end
	end
	local per = (os.clock() - t0) / n
	local total = (Config.CHUNK_X1 - Config.CHUNK_X0 + 1) * (Config.CHUNK_Z1 - Config.CHUNK_Z0 + 1)
	print(
		string.format(
			"terrain compute: %.1f ms/chunk (Lune, no native codegen) -> ~%.1fs serial for %d chunks",
			per * 1000,
			per * total,
			total
		)
	)
end

print(string.format("%d passed, %d failed", passed, failures))
if failures > 0 then
	error("tests failed")
end
