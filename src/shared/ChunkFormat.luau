--!strict
--!native
-- Parser for pipeline chunk blobs (see pipeline/geoblox/export.py for the
-- byte layout). Pure buffer code: runs in Roblox and in Lune tests.

local ChunkFormat = {}

export type MeshRef = {
	group: number,
	lod: number,
	nverts: number,
	ntris: number,
	vertexOffset: number, -- byte offset of the first vertex record
	indexOffset: number, -- byte offset of the first index
}

export type TreeRec = { x: number, y: number, z: number, height: number, crown: number, kind: number, seed: number }
export type PropRec = { kind: number, x: number, y: number, z: number, yaw: number, variant: number }
export type CollRec = { x: number, y: number, z: number, sx: number, sy: number, sz: number, yaw: number }

export type Chunk = {
	buf: buffer,
	ax: number,
	ay: number,
	az: number,
	meshes: { MeshRef },
	trees: { TreeRec },
	props: { PropRec },
	colls: { CollRec },
}

ChunkFormat.VERTEX_STRIDE = 16
ChunkFormat.STUDS_PER_METRE = 1 / 0.3

function ChunkFormat.parse(b: buffer): Chunk
	assert(buffer.readstring(b, 0, 4) == "GBM2", "not a GeoBlox mesh chunk")
	local ax = buffer.readf32(b, 6)
	local ay = buffer.readf32(b, 10)
	local az = buffer.readf32(b, 14)
	local nmesh = buffer.readu16(b, 18)
	local ntree = buffer.readu16(b, 20)
	local nprop = buffer.readu16(b, 22)
	local ncoll = buffer.readu16(b, 24)
	local o = 26
	local meshes: { MeshRef } = table.create(nmesh)
	for _ = 1, nmesh do
		local group = buffer.readu8(b, o)
		local lod = buffer.readu8(b, o + 1)
		local nv = buffer.readu16(b, o + 2)
		local nt = buffer.readu16(b, o + 4)
		o += 6
		table.insert(meshes, {
			group = group,
			lod = lod,
			nverts = nv,
			ntris = nt,
			vertexOffset = o,
			indexOffset = o + nv * 16,
		})
		o += nv * 16 + nt * 6
	end
	local trees: { TreeRec } = table.create(ntree)
	for _ = 1, ntree do
		table.insert(trees, {
			x = buffer.readi16(b, o) / 4,
			y = buffer.readi16(b, o + 2) / 4,
			z = buffer.readi16(b, o + 4) / 4,
			height = buffer.readu8(b, o + 6) / 4,
			crown = buffer.readu8(b, o + 7) / 10,
			kind = buffer.readu8(b, o + 8),
			seed = buffer.readu16(b, o + 9),
		})
		o += 11
	end
	local props: { PropRec } = table.create(nprop)
	for _ = 1, nprop do
		table.insert(props, {
			kind = buffer.readu8(b, o),
			x = buffer.readi16(b, o + 1) / 8,
			y = buffer.readi16(b, o + 3) / 8,
			z = buffer.readi16(b, o + 5) / 8,
			yaw = buffer.readu8(b, o + 7) / 256 * math.pi * 2,
			variant = buffer.readu8(b, o + 8),
		})
		o += 9
	end
	local colls: { CollRec } = table.create(ncoll)
	for _ = 1, ncoll do
		table.insert(colls, {
			x = buffer.readi16(b, o) / 8,
			y = buffer.readi16(b, o + 2) / 8,
			z = buffer.readi16(b, o + 4) / 8,
			sx = buffer.readu16(b, o + 6) / 8,
			sy = buffer.readu16(b, o + 8) / 8,
			sz = buffer.readu16(b, o + 10) / 8,
			yaw = buffer.readu8(b, o + 12) / 256 * math.pi * 2,
		})
		o += 13
	end
	assert(o == buffer.len(b), "chunk length mismatch")
	return { buf = b, ax = ax, ay = ay, az = az, meshes = meshes, trees = trees, props = props, colls = colls }
end

-- Vertex accessors (studs relative to the chunk anchor; colour 0..255; uv in tiles).
function ChunkFormat.vertex(
	b: buffer,
	o: number
): (number, number, number, number, number, number, number, number, number, number, number)
	return buffer.readi16(b, o) / 32,
		buffer.readi16(b, o + 2) / 32,
		buffer.readi16(b, o + 4) / 32,
		buffer.readi8(b, o + 6) / 127,
		buffer.readi8(b, o + 7) / 127,
		buffer.readi8(b, o + 8) / 127,
		buffer.readu8(b, o + 9),
		buffer.readu8(b, o + 10),
		buffer.readu8(b, o + 11),
		buffer.readi16(b, o + 12) / 64,
		buffer.readi16(b, o + 14) / 64
end

-- Terrain blob ("GBT1"): 64 x 64 columns.
export type TerrainChunk = { x0: number, z0: number, nx: number, nz: number, buf: buffer }

function ChunkFormat.parseTerrain(b: buffer): TerrainChunk
	assert(buffer.readstring(b, 0, 4) == "GBT1", "not a GeoBlox terrain chunk")
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
	local h = buffer.readu16(t.buf, o) / 8
	local m = buffer.readu8(t.buf, o + 2)
	local w = buffer.readu16(t.buf, o + 3)
	return h, m, if w == 0 then -1 else w / 8
end

return ChunkFormat
