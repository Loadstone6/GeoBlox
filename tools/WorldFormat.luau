--!nocheck
-- Reader for compiled world chunks (pipeline/geoblox/world.py, "GBW1"):
--   "GBW1", u32 json length, json header, u32 part count, part records.
-- Part record (52 bytes, little-endian; see pipeline/geoblox/partmap.py):
--   u8 shape (0 block, 1 wedge, 2 cylinder, 3 ball), u8 material, u8 flags
--   (1 collide, 2 shadow), u8 tag, u8[3] colour, u8 transparency,
--   u8 reflectance, u8 extra (1 = sphere mesh), u16 pad,
--   f32[3] position (studs, relative to the chunk anchor), f32[3] size,
--   f32[4] rotation quaternion (x, y, z, w).

local serde = require("@lune/serde")

local WorldFormat = {}

WorldFormat.RECORD = 52

export type Part = {
	shape: number,
	material: number,
	collide: boolean,
	shadow: boolean,
	tag: number,
	r: number,
	g: number,
	b: number,
	transparency: number,
	reflectance: number,
	extra: number,
	px: number,
	py: number,
	pz: number,
	sx: number,
	sy: number,
	sz: number,
	qx: number,
	qy: number,
	qz: number,
	qw: number,
}

-- Decompress and parse a chunk file's bytes: (header, buffer, first record offset, part count).
function WorldFormat.open(compressed: string)
	local raw = serde.decompress("zstd", compressed)
	local b = buffer.fromstring(raw)
	assert(buffer.readstring(b, 0, 4) == "GBW1", "not a GeoBlox world chunk")
	local jlen = buffer.readu32(b, 4)
	local header = serde.decode("json", buffer.readstring(b, 8, jlen))
	local o = 8 + jlen
	local n = buffer.readu32(b, o)
	local base = o + 4
	assert(buffer.len(b) == base + n * WorldFormat.RECORD, "chunk length mismatch")
	return header, b, base, n
end

function WorldFormat.part(b: buffer, o: number): Part
	local flags = buffer.readu8(b, o + 2)
	return {
		shape = buffer.readu8(b, o),
		material = buffer.readu8(b, o + 1),
		collide = bit32.band(flags, 1) ~= 0,
		shadow = bit32.band(flags, 2) ~= 0,
		tag = buffer.readu8(b, o + 3),
		r = buffer.readu8(b, o + 4),
		g = buffer.readu8(b, o + 5),
		b = buffer.readu8(b, o + 6),
		transparency = buffer.readu8(b, o + 7) / 255,
		reflectance = buffer.readu8(b, o + 8) / 255,
		extra = buffer.readu8(b, o + 9),
		px = buffer.readf32(b, o + 12),
		py = buffer.readf32(b, o + 16),
		pz = buffer.readf32(b, o + 20),
		sx = buffer.readf32(b, o + 24),
		sy = buffer.readf32(b, o + 28),
		sz = buffer.readf32(b, o + 32),
		qx = buffer.readf32(b, o + 36),
		qy = buffer.readf32(b, o + 40),
		qz = buffer.readf32(b, o + 44),
		qw = buffer.readf32(b, o + 48),
	}
end

return WorldFormat
