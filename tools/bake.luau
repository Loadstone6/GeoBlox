--!nocheck
-- Bakes the compiled world into the place: every part of every chunk in
-- world/*.gbw, plus the prefab placements (trees, street furniture) built by
-- src/shared/Prefabs.luau. Run it after `rojo build`:
--
--   lune run tools/bake [in.rbxl] [out.rbxl]
--
-- Layout: Workspace.City.<chunk>.<Model>. Streaming per model kind:
--   building, bridge  Atomic + StreamingMesh impostor when out of range
--   streets           Atomic (kerbs, pavement edges, markings of one chunk)
--   trees, props      one Atomic model per placement
--   landmark          Persistent (the client animates these)

local fs = require("@lune/fs")
local process = require("@lune/process")
local roblox = require("@lune/roblox")
local serde = require("@lune/serde")

local Instance, Vector3, CFrame, Color3, Enum =
	roblox.Instance, roblox.Vector3, roblox.CFrame, roblox.Color3, roblox.Enum
local WorldFormat = require("./WorldFormat")

local Prefabs = require("../src/shared/Prefabs").new({
	Instance = Instance,
	Vector3 = Vector3,
	CFrame = CFrame,
	Color3 = Color3,
	Enum = Enum,
})

local inPath = process.args[1] or "build/GeoBlox.rbxl"
local outPath = process.args[2] or inPath
local worldDir = process.args[3] or "world"

local t0 = os.clock()
local game = roblox.deserializePlace(fs.readFile(inPath))
local workspace = game:GetService("Workspace")
local index = serde.decode("json", fs.readFile(worldDir .. "/index.json"))

local MATS = {}
for i, name in index.materials do
	MATS[i - 1] = Enum.Material[name]
end
local TAGS = index.tags
local SMOOTH = Enum.SurfaceType.Smooth
local CYL, BALL = Enum.PartType.Cylinder, Enum.PartType.Ball

local stats = { parts = 0, prefabs = 0, prefabParts = 0, models = 0 }

local function makePart(r, ax: number, ay: number, az: number, parent: Instance)
	local p
	if r.shape == 1 then
		p = Instance.new("WedgePart")
	else
		p = Instance.new("Part")
		if r.shape == 2 then
			p.Shape = CYL
		elseif r.shape == 3 then
			p.Shape = BALL
		end
	end
	p.Anchored = true
	p.Material = MATS[r.material]
	p.CanCollide = r.collide
	p.CanQuery = r.collide
	p.CanTouch = false
	p.CastShadow = r.shadow
	p.Color = Color3.fromRGB(r.r, r.g, r.b)
	if r.transparency > 0 then
		p.Transparency = r.transparency
	end
	if r.reflectance > 0 then
		p.Reflectance = r.reflectance
	end
	p.TopSurface = SMOOTH
	p.BottomSurface = SMOOTH
	p.Size = Vector3.new(r.sx, r.sy, r.sz)
	p.CFrame = CFrame.new(ax + r.px, ay + r.py, az + r.pz, r.qx, r.qy, r.qz, r.qw)
	if r.extra == 1 then
		local mesh = Instance.new("SpecialMesh")
		mesh.MeshType = Enum.MeshType.Sphere
		mesh.Parent = p
	end
	if r.tag > 0 then
		p:AddTag(TAGS[r.tag + 1])
	end
	p.Parent = parent
	stats.parts += 1
	return p
end

local function attr(model: Instance, key: string, value: any)
	if key == "tag" then
		model:AddTag(value)
	elseif type(value) == "table" and #value == 3 and type(value[1]) == "number" then
		model:SetAttribute(key, Vector3.new(value[1], value[2], value[3]))
	elseif type(value) == "number" or type(value) == "string" or type(value) == "boolean" then
		model:SetAttribute(key, value)
	end
end

local function configure(model: Instance, kind: string)
	if kind == "building" or kind == "bridge" then
		model.ModelStreamingMode = Enum.ModelStreamingMode.Atomic
		model.LevelOfDetail = Enum.ModelLevelOfDetail.StreamingMesh
	elseif kind == "streets" then
		model.ModelStreamingMode = Enum.ModelStreamingMode.Atomic
	elseif kind == "trees" then
		model.LevelOfDetail = Enum.ModelLevelOfDetail.StreamingMesh
	elseif kind == "landmark" then
		model.ModelStreamingMode = Enum.ModelStreamingMode.Persistent
	end
end

local function countParts(m: Instance): number
	local n = 0
	for _, d in m:GetDescendants() do
		if d:IsA("BasePart") then
			n += 1
		end
	end
	return n
end

local old = workspace:FindFirstChild("City")
if old then
	old:Destroy()
end
local city = Instance.new("Folder")
city.Name = "City"

for _, entry in index.chunks do
	local header, b, base = WorldFormat.open(fs.readFile(worldDir .. "/" .. entry.name .. ".gbw"))
	local ax, ay, az = header.anchor[1], header.anchor[2], header.anchor[3]
	local folder = Instance.new("Folder")
	folder.Name = header.name
	for _, def in header.models do
		local model = Instance.new("Model")
		model.Name = def.name
		configure(model, def.kind)
		for i = def.first, def.first + def.count - 1 do
			makePart(WorldFormat.part(b, base + i * WorldFormat.RECORD), ax, ay, az, model)
		end
		for _, pf in def.prefabs do
			local name, x, y, z, yaw = pf[1], pf[2], pf[3], pf[4], pf[5]
			local args = {}
			for k = 6, #pf do
				table.insert(args, pf[k])
			end
			local inst = Prefabs.build(name, x, y, z, yaw, args)
			if inst then
				inst.ModelStreamingMode = Enum.ModelStreamingMode.Atomic
				inst.Parent = model
				stats.prefabs += 1
				stats.prefabParts += countParts(inst)
			else
				warn("unknown prefab " .. tostring(name))
			end
		end
		for k, v in def.attrs do
			attr(model, k, v)
		end
		model.Parent = folder
		stats.models += 1
	end
	folder.Parent = city
end
city.Parent = workspace

-- A copy of every prefab for designers to inspect or swap (ServerStorage).
local storage = game:GetService("ServerStorage")
local lib = storage:FindFirstChild("GeoBloxPrefabs")
if lib then
	lib:Destroy()
end
lib = Instance.new("Folder")
lib.Name = "GeoBloxPrefabs"
for i, name in Prefabs.names() do
	local args = if string.sub(name, 1, 5) == "tree_" then { 14, 4, i } else { 0 }
	local m = Prefabs.build(name, i * 30, 0, 0, 0, args)
	if m then
		m.Parent = lib
	end
end
lib.Parent = storage

local bytes = roblox.serializePlace(game)
fs.writeFile(outPath, bytes)
print(
	string.format(
		"[bake] %d chunks, %d models, %d world parts, %d prefabs (%d parts) -> %s (%.1f MB) in %.1fs",
		#index.chunks,
		stats.models,
		stats.parts,
		stats.prefabs,
		stats.prefabParts,
		outPath,
		#bytes / 1e6,
		os.clock() - t0
	)
)
