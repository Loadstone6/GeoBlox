--!strict
--!native
-- Parser for pipeline terrain blobs (see pipeline/geoblox/export.py for the
-- byte layout). Pure buffer code: runs in Roblox and in Lune tests.

local ChunkFormat = {}

ChunkFormat.STUDS_PER_METRE = 1 / 0.3
ChunkFormat.TERRAIN_SCALE = 32

-- Terrain blob ("GBT2"): 64 x 64 columns.
export type TerrainChunk = { x0: number, z0: number, nx: number, nz: number, buf: buffer }

function ChunkFormat.parseTerrain(b: buffer): TerrainChunk
	assert(buffer.readstring(b, 0, 4) == "GBT2", "not a GeoBlox terrain chunk")
	local x0 = buffer.readi32(b, 6)
	local z0 = buffer.readi32(b, 10)
	local nx = buffer.readu16(b, 14)
	local nz = buffer.readu16(b, 16)
	assert(buffer.len(b) == 18 + nx * nz * 5, "terrain length mismatch")
	return { x0 = x0, z0 = z0, nx = nx, nz = nz, buf = b }
end

-- Column (ix, iz) zero-based: height (studs), material code, water level (studs or -1).
function ChunkFormat.column(t: TerrainChunk, ix: number, iz: number): (number, number, number)
	local o = 18 + (ix * t.nz + iz) * 5
	local s = ChunkFormat.TERRAIN_SCALE
	local h = buffer.readu16(t.buf, o) / s
	local m = buffer.readu8(t.buf, o + 2)
	local w = buffer.readu16(t.buf, o + 3)
	return h, m, if w == 0 then -1 else w / s
end

return ChunkFormat
