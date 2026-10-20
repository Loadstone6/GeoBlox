--!strict
--!native
-- Turns one 64x64-column chunk of sampled layers into WriteVoxels arrays.
-- Safe to call from a parallel (desynchronized) Actor: it only reads the
-- module-local layout and allocates plain tables.

local Config = require(script.Parent.Config)
local Layout = require(script.Parent.Layout)
local Sampler = require(script.Parent.Sampler)
local Materials = require(script.Parent.Materials)
local SurfaceClass = require(script.Parent.SurfaceClass)

local VOX = Config.VOXEL
local N = Config.CHUNK_VOXELS
local NY = (Config.Y_MAX - Config.Y_MIN) // VOX
local Y_MIN = Config.Y_MIN

local AIR = Enum.Material.Air
local WATER = Enum.Material.Water
local CLASS_TO_MATERIAL = Materials.CLASS_TO_MATERIAL
local SUBSOIL = CLASS_TO_MATERIAL[SurfaceClass.Subsoil]

local TerrainChunk = {}

local sampler: Sampler.Sampler? = nil
local function getSampler(): Sampler.Sampler
	if not sampler then
		sampler = Sampler.new(Layout.build(Config.SEED))
	end
	return sampler :: Sampler.Sampler
end

function TerrainChunk.region(cx: number, cz: number): Region3
	local x0, z0 = cx * Config.CHUNK_STUDS, cz * Config.CHUNK_STUDS
	return Region3.new(
		Vector3.new(x0, Config.Y_MIN, z0),
		Vector3.new(x0 + Config.CHUNK_STUDS, Config.Y_MAX, z0 + Config.CHUNK_STUDS)
	)
end

function TerrainChunk.compute(cx: number, cz: number): (Region3, { any }, { any })
	local smp = getSampler()
	local x0, z0 = cx * Config.CHUNK_STUDS, cz * Config.CHUNK_STUDS
	local mats: { { { Enum.Material } } } = table.create(N)
	local occs: { { { number } } } = table.create(N)

	for i = 1, N do
		local mi: { { Enum.Material } } = table.create(NY)
		local oi: { { number } } = table.create(NY)
		for j = 1, NY do
			mi[j] = table.create(N)
			oi[j] = table.create(N)
		end
		mats[i] = mi
		occs[i] = oi

		local x = x0 + (i - 0.5) * VOX
		for k = 1, N do
			local z = z0 + (k - 0.5) * VOX
			local h, cls, water = smp:sample(x, z)
			local surface = CLASS_TO_MATERIAL[cls]
			local yTop = (h - Y_MIN) / VOX
			local wTop = (water - Y_MIN) / VOX
			for j = 1, NY do
				local vy = j - 1
				local depth = yTop - vy
				local occ = math.clamp(depth, 0, 1)
				local mat = AIR
				if occ > 0 then
					mat = if depth < 2 then surface else SUBSOIL
					if water > 0 and occ < 0.5 and vy < wTop then
						mat = WATER
						occ = math.clamp(wTop - vy, 0, 1)
					end
				elseif water > 0 and vy < wTop then
					mat = WATER
					occ = math.clamp(wTop - vy, 0, 1)
				end
				mi[j][k] = mat
				oi[j][k] = occ
			end
		end
	end

	return TerrainChunk.region(cx, cz), mats, occs
end

return TerrainChunk
