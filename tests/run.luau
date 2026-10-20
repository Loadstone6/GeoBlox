--!nocheck
-- Lune tests: decode the generated terrain and world chunks with the same
-- readers the game and the baker use, and check structural invariants.
--   lune run tests/run            data checks
--   lune run tests/run <place>    also checks a baked place file

local fs = require("@lune/fs")
local process = require("@lune/process")
local roblox = require("@lune/roblox")
local serde = require("@lune/serde")
local Harness = require("./harness")
local WorldFormat = require("../tools/WorldFormat")

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

local function finite(x)
	return x == x and x ~= math.huge and x ~= -math.huge
end

-- Base64 decoder round-trips.
do
	local b = Codec.base64("R2VvQmxveA==")
	check("base64 decode", buffer.tostring(b) == "GeoBlox", buffer.tostring(b))
end

-- Manifest sanity.
check("manifest version", Manifest.version == 3)
check("manifest spawn", type(Manifest.spawn) == "table" and #Manifest.spawn == 3)
check("manifest spawn look", type(Manifest.spawnLook) == "table" and #Manifest.spawnLook == 3)
local nviews = 0
for name, p in Manifest.viewpoints do
	nviews += 1
	check("viewpoint " .. name, #p == 3 and finite(p[1]) and finite(p[2]) and finite(p[3]))
end
check("viewpoints", nviews >= 6, nviews)
check("london eye", Manifest.londonEye.hub ~= nil and Manifest.londonEye.radius > 100)

-- Terrain chunks: decode, ranges, voxelisation.
local tcount, water = 0, 0
for i, t in Manifest.terrain do
	local blob = ChunkFormat.parseTerrain(decode("src/generated_server/Terrain/" .. t.name .. ".luau"))
	tcount += 1
	check("terrain origin " .. t.name, blob.x0 == t.x0 and blob.z0 == t.z0)
	local lo, hi = math.huge, -math.huge
	for ix = 0, blob.nx - 1, 7 do
		for iz = 0, blob.nz - 1, 7 do
			local h, m, w = ChunkFormat.column(blob, ix, iz)
			lo = math.min(lo, h)
			hi = math.max(hi, h)
			if w > 0 then
				water += 1
			end
			if m > 12 then
				check("terrain material " .. t.name, false, m)
			end
		end
	end
	check("terrain heights " .. t.name, lo > 0 and hi < 400, string.format("%.1f..%.1f", lo, hi))
	if i % 97 == 1 then
		local region, mats, occs = TerrainBuild.voxels(blob)
		check("voxels " .. t.name, region ~= nil and #mats == blob.nx and #occs == blob.nx)
	end
end
check("terrain chunk count", tcount == #Manifest.terrain)
check("thames water present", water > 100, water)

-- Quaternion convention: CFrame.new(x, y, z, qx, qy, qz, qw) must match the
-- pipeline's matrix_from_quat (columns = Right, Up, Back vectors).
do
	local function mat(qx, qy, qz, qw)
		return {
			{ 1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw) },
			{ 2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw) },
			{ 2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy) },
		}
	end
	local worst = 0
	for k = 1, 40 do
		local a, b2, c, d = math.sin(k * 1.7), math.cos(k * 0.9), math.sin(k * 2.3 + 1), math.cos(k * 3.1)
		local n = math.sqrt(a * a + b2 * b2 + c * c + d * d)
		a, b2, c, d = a / n, b2 / n, c / n, d / n
		local cf = roblox.CFrame.new(0, 0, 0, a, b2, c, d)
		local m = mat(a, b2, c, d)
		local back = -cf.LookVector
		for i, v in { cf.RightVector, cf.UpVector, back } do
			worst = math.max(worst, math.abs(v.X - m[1][i]), math.abs(v.Y - m[2][i]), math.abs(v.Z - m[3][i]))
		end
	end
	check("quaternion convention matches the pipeline", worst < 1e-5, worst)
end

-- World chunks.
local index = serde.decode("json", fs.readFile("world/index.json"))
local total, wedges, tagged, prefabs = 0, 0, 0, 0
local kinds = {}
local Prefabs = require("../src/shared/Prefabs").new({
	Instance = roblox.Instance,
	Vector3 = roblox.Vector3,
	CFrame = roblox.CFrame,
	Color3 = roblox.Color3,
	Enum = roblox.Enum,
})
local known = {}
for _, n in Prefabs.names() do
	known[n] = true
end
for _, entry in index.chunks do
	local header, b, base, n = WorldFormat.open(fs.readFile("world/" .. entry.name .. ".gbw"))
	check("chunk count " .. entry.name, n == entry.parts, n)
	local covered = 0
	for _, m in header.models do
		kinds[m.kind] = (kinds[m.kind] or 0) + 1
		covered += m.count
		for _, pf in m.prefabs do
			prefabs += 1
			if not known[pf[1]] then
				check("prefab name " .. tostring(pf[1]), false)
			end
		end
	end
	check("models cover parts " .. entry.name, covered == n)
	local bad = 0
	for i = 0, n - 1 do
		local p = WorldFormat.part(b, base + i * WorldFormat.RECORD)
		total += 1
		if p.shape == 1 then
			wedges += 1
		end
		if p.tag > 0 then
			tagged += 1
		end
		local qn = p.qx * p.qx + p.qy * p.qy + p.qz * p.qz + p.qw * p.qw
		local ok = p.shape <= 3
			and p.material < #index.materials
			and p.tag < #index.tags
			and p.sx >= 0.001
			and p.sy >= 0.001
			and p.sz >= 0.001
			and p.sx < 2048
			and p.sy < 2048
			and p.sz < 2048
			and math.abs(qn - 1) < 1e-3
			and finite(p.px)
			and finite(p.py)
			and finite(p.pz)
			and math.abs(p.px) < 2048 -- big buildings reach far beyond their anchor chunk
			and math.abs(p.pz) < 2048
			and (p.shape ~= 3 or (math.abs(p.sx - p.sy) < 1e-3 and math.abs(p.sy - p.sz) < 1e-3))
		if not ok then
			bad += 1
		end
	end
	check("parts valid " .. entry.name, bad == 0, bad)
end
check("world has buildings", (kinds.building or 0) > 500, kinds.building)
check("world has streets", (kinds.streets or 0) > 100, kinds.streets)
check("world has bridges", (kinds.bridge or 0) >= 4, kinds.bridge)
check("world has landmarks", (kinds.landmark or 0) >= 2, kinds.landmark)
check("world parts", total > 100000, total)
check("wedges present", wedges > 1000, wedges)
check("tagged parts", tagged > 1000, tagged)
check("prefab placements", prefabs > 1000, prefabs)

-- Every prefab builds in Lune exactly as the baker will build it.
for name in known do
	local args = if string.sub(name, 1, 5) == "tree_" then { 14, 4, 7 } else { 0 }
	local m = Prefabs.build(name, 0, 0, 0, 0.5, args)
	local nparts = 0
	for _, d in m:GetDescendants() do
		if d:IsA("BasePart") then
			nparts += 1
		end
	end
	check("prefab " .. name, nparts >= 1 and nparts <= 40, nparts)
end

-- Optional: a baked place.
local placePath = process.args[1]
if placePath then
	local game = roblox.deserializePlace(fs.readFile(placePath))
	local ws = game:GetService("Workspace")
	local city = ws:FindFirstChild("City")
	check("baked City folder", city ~= nil)
	check("streaming enabled", ws.StreamingEnabled == true)
	if city then
		local parts, atomic, persistent = 0, 0, 0
		for _, d in city:GetDescendants() do
			if d:IsA("BasePart") then
				parts += 1
			elseif d:IsA("Model") then
				if d.ModelStreamingMode == roblox.Enum.ModelStreamingMode.Atomic then
					atomic += 1
				elseif d.ModelStreamingMode == roblox.Enum.ModelStreamingMode.Persistent then
					persistent += 1
				end
			end
		end
		check("baked parts", parts >= total, parts)
		check("atomic models", atomic > 1000, atomic)
		check("persistent landmarks", persistent >= 2, persistent)
	end
	check("prefab library", game:GetService("ServerStorage"):FindFirstChild("GeoBloxPrefabs") ~= nil)
end

print(
	string.format("%d checks passed, %d failed  (%d world parts, %d prefab placements)", passed, failed, total, prefabs)
)
if failed > 0 then
	process.exit(1)
end
