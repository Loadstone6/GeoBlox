--!strict
--!native
-- Terrain column data -> WriteVoxels arrays. Heights come from the LiDAR DTM
-- (raised by the kerb height under pavements), materials from the surface
-- classification, water from the measured Thames level / mapped water bodies.

local Codec = require(script.Parent.Codec)
local ChunkFormat = require(script.Parent.ChunkFormat)

local TerrainBuild = {}

local M = Enum.Material
local MATERIALS = {
	[0] = M.Concrete,
	[1] = M.Asphalt,
	[2] = M.Pavement,
	[3] = M.Grass,
	[4] = M.LeafyGrass,
	[5] = M.Ground,
	[6] = M.Concrete,
	[7] = M.Water,
	[8] = M.Mud,
	[9] = M.Slate,
	[10] = M.Sand,
	[11] = M.Cobblestone,
	[12] = M.Brick,
} :: { [number]: Enum.Material }

function TerrainBuild.decode(payload: string): ChunkFormat.TerrainChunk
	return ChunkFormat.parseTerrain(Codec.decode(payload))
end

function TerrainBuild.voxels(t: ChunkFormat.TerrainChunk): (Region3?, { any }, { any })
	local nx, nz = t.nx, t.nz
	local lo, hi = math.huge, -math.huge
	for ix = 0, nx - 1 do
		for iz = 0, nz - 1 do
			local h, _, w = ChunkFormat.column(t, ix, iz)
			lo = math.min(lo, h)
			hi = math.max(hi, h, w)
		end
	end
	if lo == math.huge then
		return nil, {}, {}
	end
	-- Two solid voxels under the lowest ground; nothing below (unreachable).
	local y0 = (math.floor(lo / 4) - 2) * 4
	local y1 = (math.ceil(hi / 4) + 1) * 4
	local ny = (y1 - y0) // 4
	local mats: { { { Enum.Material } } } = table.create(nx)
	local occs: { { { number } } } = table.create(nx)
	local AIR, WATER, SOIL = M.Air, M.Water, M.Ground
	for ix = 0, nx - 1 do
		local mi: { { Enum.Material } } = table.create(ny)
		local oi: { { number } } = table.create(ny)
		for j = 1, ny do
			mi[j] = table.create(nz)
			oi[j] = table.create(nz)
		end
		mats[ix + 1] = mi
		occs[ix + 1] = oi
		for iz = 0, nz - 1 do
			local h, code, w = ChunkFormat.column(t, ix, iz)
			local surface = MATERIALS[code] or M.Concrete
			local top = (h - y0) / 4
			local wtop = if w > 0 then (w - y0) / 4 else -1
			for j = 1, ny do
				local vy = j - 1
				local depth = top - vy
				local occ = math.clamp(depth, 0, 1)
				local mat = AIR
				if occ > 0 then
					mat = if depth < 2 then surface else SOIL
					if wtop > 0 and occ < 0.5 and vy < wtop then
						mat = WATER
						occ = math.clamp(wtop - vy, 0, 1)
					end
				elseif wtop > 0 and vy < wtop then
					mat = WATER
					occ = math.clamp(wtop - vy, 0, 1)
				end
				mi[j][iz + 1] = mat
				oi[j][iz + 1] = occ
			end
		end
	end
	local region = Region3.new(Vector3.new(t.x0, y0, t.z0), Vector3.new(t.x0 + nx * 4, y1, t.z0 + nz * 4))
	return region, mats, occs
end

return TerrainBuild
