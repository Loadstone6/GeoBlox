--!nocheck
-- Lune tests: decode the generated city data with the same Luau parsers the
-- game uses and check structural invariants.  Usage: lune run tests/run

local fs = require("@lune/fs")
local serde = require("@lune/serde")
local Harness = require("./harness")

local Codec = Harness.require("src/shared/Codec")
local ChunkFormat = Harness.require("src/shared/ChunkFormat")
local TerrainBuild = Harness.require("src/shared/TerrainBuild")
local Manifest = Harness.require("src/generated/Manifest")

local passed, failed = 0, 0
local function check(name, cond, detail)
	if cond then
		passed += 1
	else
		failed += 1
		print("FAIL  " .. name .. (if detail ~= nil then "  (" .. tostring(detail) .. ")" else ""))
	end
end

local function payload(path)
	local src = fs.readFile(path)
	return string.match(src, "%[==%[(.-)%]==%]")
end

local function decode(path)
	local raw = Codec.base64(payload(path))
	return buffer.fromstring(serde.decompress("zstd", buffer.tostring(raw)))
end

-- Base64 decoder round-trips.
do
	local b = Codec.base64("R2VvQmxveA==")
	check("base64 decode", buffer.tostring(b) == "GeoBlox", buffer.tostring(b))
end

-- Manifest sanity.
check("manifest chunks", #Manifest.chunks > 100, #Manifest.chunks)
check("manifest terrain", #Manifest.terrain > 100, #Manifest.terrain)
check("manifest groups", #Manifest.groups >= 20)
check("manifest spawn", Manifest.spawn and #Manifest.spawn == 3)
check("london eye measured", Manifest.londonEye and Manifest.londonEye.hub ~= nil)
check("big ben dials", #Manifest.bigBenDials == 4, #Manifest.bigBenDials)

-- Every mesh chunk parses; geometry invariants hold.
local totalTris, totalTrees, totalProps, totalColl, meshes = 0, 0, 0, 0, 0
local badIndex, badNormal, bigMesh, badGroup = 0, 0, 0, 0
for _, c in Manifest.chunks do
	local b = decode("src/generated/Chunks/" .. c.name .. ".luau")
	local ok, chunk = pcall(ChunkFormat.parse, b)
	check("parse " .. c.name, ok, chunk)
	if ok then
		check("anchor " .. c.name, math.abs(chunk.ax - c.center[1]) < 0.01 and math.abs(chunk.az - c.center[3]) < 0.01)
		for _, m in chunk.meshes do
			meshes += 1
			totalTris += m.ntris
			if m.nverts > 60000 or m.ntris > 20000 then
				bigMesh += 1
			end
			if m.group < 0 or m.group >= #Manifest.groups then
				badGroup += 1
			end
			for t = 0, m.ntris - 1 do
				local o = m.indexOffset + t * 6
				for k = 0, 2 do
					if buffer.readu16(b, o + k * 2) >= m.nverts then
						badIndex += 1
					end
				end
			end
			for i = 0, math.min(m.nverts, 64) - 1 do
				local _, _, _, nx, ny, nz = ChunkFormat.vertex(b, m.vertexOffset + i * 16)
				local len = math.sqrt(nx * nx + ny * ny + nz * nz)
				if len < 0.9 or len > 1.1 then
					badNormal += 1
				end
			end
		end
		totalTrees += #chunk.trees
		totalProps += #chunk.props
		totalColl += #chunk.colls
	end
end
check("indices in range", badIndex == 0, badIndex)
check("unit normals", badNormal == 0, badNormal)
check("EditableMesh caps respected", bigMesh == 0, bigMesh)
check("group ids valid", badGroup == 0, badGroup)
check("trees present", totalTrees > 1000, totalTrees)
check("props present", totalProps > 500, totalProps)
check("collision present", totalColl > 1000, totalColl)
print(
	string.format(
		"meshes %d, triangles %d, trees %d, props %d, collision boxes %d",
		meshes,
		totalTris,
		totalTrees,
		totalProps,
		totalColl
	)
)

-- Terrain chunks decode and voxelise.
local waterCols, landCols = 0, 0
for i, t in Manifest.terrain do
	local blob = ChunkFormat.parseTerrain(decode("src/generated_server/Terrain/" .. t.name .. ".luau"))
	check("terrain origin " .. t.name, blob.x0 == t.x0 and blob.z0 == t.z0)
	for ix = 0, blob.nx - 1, 8 do
		for iz = 0, blob.nz - 1, 8 do
			local h, m, w = ChunkFormat.column(blob, ix, iz)
			if w > 0 then
				waterCols += 1
				check("water above bed", w > h, string.format("%s %d,%d", t.name, ix, iz))
			else
				landCols += 1
			end
			check("material code", m >= 0 and m <= 12, m)
		end
	end
	if i == 1 then
		local region, mats, occs = TerrainBuild.voxels(blob)
		check("voxel region", region ~= nil)
		check("voxel dims", #mats == blob.nx and #mats[1][1] == blob.nz)
	end
end
check("thames present", waterCols > 500, waterCols)
print(string.format("terrain sample: %d land, %d water columns", landCols, waterCols))

print(string.format("%d passed, %d failed", passed, failed))
if failed > 0 then
	error("tests failed")
end
