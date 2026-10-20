--!strict
-- Chunk streaming. Every 100 m chunk has shared geometry (streets, bridges,
-- embankments: built once), LOD0 geometry (full facades, roof TINs, markings,
-- kerbs) and LOD1 geometry (simplified roofs). The camera distance decides
-- which LOD exists; work runs under a per-frame budget with a cap on
-- concurrent CreateMeshPartAsync calls. Trees, props and collision proxies
-- stream on their own (smaller) radii.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local GeoBlox = ReplicatedStorage:WaitForChild("GeoBlox")
local Shared = GeoBlox:WaitForChild("Shared")
local Generated = GeoBlox:WaitForChild("Generated")
local ChunkFormat = require(Shared:WaitForChild("ChunkFormat"))
local Codec = require(Shared:WaitForChild("Codec"))
local Config = require(Shared:WaitForChild("Config"))
local Manifest = require(Generated:WaitForChild("Manifest")) :: any

local Groups = require(script.Parent.Groups)
local Meshes = require(script.Parent.Meshes)
local Props = require(script.Parent.Props)
local Trees = require(script.Parent.Trees)

local World = {}

type ChunkState = {
	name: string,
	center: Vector3,
	data: ChunkFormat.Chunk?,
	shared: Folder?,
	lodFolder: Folder?,
	lod: number, -- built LOD (-1 none)
	treeFolder: Folder?,
	treeLod: number,
	propFolder: Folder?,
	collFolder: Folder?,
	busy: boolean,
}

local chunks: { ChunkState } = {}
local root: Folder
local groundFolder: Folder
local inflight = 0
World.stats = { meshes = 0, tris = 0, failures = 0, trees = 0, props = 0, chunksLod0 = 0, chunksLod1 = 0 }
World.groundParts = {} :: { BasePart }
World.nightParts = {} :: { BasePart }
World.shopParts = {} :: { BasePart }
World.liteMode = false

local ALL_LODS = 2
local GROUND = { road = true, pavement = true, kerb = true, bridge = true, embankment = true }

local function decode(c: ChunkState): ChunkFormat.Chunk
	if c.data == nil then
		local module = Generated:WaitForChild("Chunks"):WaitForChild(c.name) :: ModuleScript
		c.data = ChunkFormat.parse(Codec.decode(require(module) :: any))
	end
	return c.data :: ChunkFormat.Chunk
end

local function waitSlot()
	while inflight >= Config.MAX_CONCURRENT_MESHPARTS do
		task.wait()
	end
end

local function buildMeshes(c: ChunkState, data: ChunkFormat.Chunk, lods: { [number]: boolean }, folder: Folder)
	for _, ref in data.meshes do
		if lods[ref.lod] then
			local name = Manifest.groups[ref.group + 1]
			waitSlot()
			inflight += 1
			local ok, mp = pcall(Meshes.build, data, ref, name)
			inflight -= 1
			if ok and mp then
				local part = mp :: MeshPart
				part.Parent = folder
				World.stats.meshes += 1
				World.stats.tris += ref.ntris
				if GROUND[name] then
					part.CanQuery = true -- feet snap to the visual street surface
					table.insert(World.groundParts, part)
				end
				local style = Groups[name]
				if style and style.night then
					table.insert(World.nightParts, part)
				elseif style and style.glowAtNight then
					table.insert(World.shopParts, part)
				end
			else
				World.stats.failures += 1
			end
		end
	end
end

-- Fallback when EditableMesh is unavailable: walls from collision proxies.
local function buildLite(data: ChunkFormat.Chunk, folder: Folder)
	local origin = Vector3.new(data.ax, data.ay, data.az)
	for _, r in data.colls do
		if r.sz < 4 then
			local p = Instance.new("Part")
			p.Anchored = true
			p.CanCollide = false
			p.CanQuery = false
			p.CanTouch = false
			p.Size = Vector3.new(r.sx, r.sy, r.sz)
			p.CFrame = CFrame.new(origin + Vector3.new(r.x, r.y, r.z)) * CFrame.Angles(0, math.pi / 2 - r.yaw, 0)
			p.Color = Color3.fromRGB(196, 186, 160)
			p.Material = Enum.Material.Limestone
			p.Parent = folder
		end
	end
end

local function setLod(c: ChunkState, lod: number)
	if c.busy then
		return
	end
	c.busy = true
	task.spawn(function()
		local data = decode(c)
		if World.liteMode then
			if lod >= 0 and not c.shared then
				local f = Instance.new("Folder")
				f.Name = c.name
				buildLite(data, f)
				f.Parent = root
				c.shared = f
			end
			c.lod = lod
			c.busy = false
			return
		end
		if lod >= 0 and c.shared == nil then
			local f = Instance.new("Folder")
			f.Name = c.name .. "_streets"
			f.Parent = groundFolder
			c.shared = f
			buildMeshes(c, data, { [ALL_LODS] = true }, f)
		end
		local old = c.lodFolder
		c.lodFolder = nil
		if lod >= 0 then
			local f = Instance.new("Folder")
			f.Name = c.name .. "_lod" .. lod
			buildMeshes(c, data, { [lod] = true }, f)
			f.Parent = root
			c.lodFolder = f
		end
		if old then
			old:Destroy()
		end
		if lod < 0 and c.shared then
			c.shared:Destroy()
			c.shared = nil
			c.data = nil
		end
		c.lod = lod
		c.busy = false
	end)
end

local function setTrees(c: ChunkState, lod: number)
	local data = decode(c)
	if #data.trees == 0 then
		c.treeLod = lod
		return
	end
	local old = c.treeFolder
	c.treeFolder = nil
	if lod >= 0 then
		local f = Instance.new("Folder")
		f.Name = c.name .. "_trees"
		World.stats.trees += Trees.build(data, lod, f)
		f.Parent = root
		c.treeFolder = f
	end
	if old then
		old:Destroy()
	end
	c.treeLod = lod
end

local function setProps(c: ChunkState, on: boolean)
	if on and not c.propFolder then
		local data = decode(c)
		local f = Instance.new("Folder")
		f.Name = c.name .. "_props"
		local origin = Vector3.new(data.ax, data.ay, data.az)
		for _, p in data.props do
			Props.build(p, origin, f)
		end
		f.Parent = root
		c.propFolder = f
		World.stats.props += #data.props
	elseif not on and c.propFolder then
		Props.forget(c.propFolder)
		c.propFolder:Destroy()
		c.propFolder = nil
	end
end

local function setCollision(c: ChunkState, on: boolean)
	if on and not c.collFolder then
		local data = decode(c)
		local f = Instance.new("Folder")
		f.Name = c.name .. "_collision"
		local origin = Vector3.new(data.ax, data.ay, data.az)
		for _, r in data.colls do
			local p = Instance.new("Part")
			p.Anchored = true
			p.CanCollide = true
			p.CanQuery = false
			p.CanTouch = false
			p.Transparency = 1
			p.Size = Vector3.new(math.max(0.2, r.sx), math.max(0.2, r.sy), math.max(0.2, r.sz))
			-- Collision yaw: clockwise-from-north of the box's +X axis.
			p.CFrame = CFrame.new(origin + Vector3.new(r.x, r.y, r.z)) * CFrame.Angles(0, math.pi / 2 - r.yaw, 0)
			p.Parent = f
		end
		f.Parent = root
		c.collFolder = f
	elseif not on and c.collFolder then
		c.collFolder:Destroy()
		c.collFolder = nil
	end
end

function World.start()
	root = Instance.new("Folder")
	root.Name = "GeoBloxCity"
	root.Parent = workspace
	groundFolder = Instance.new("Folder")
	groundFolder.Name = "Streets"
	groundFolder.Parent = root

	if not Codec.available() then
		warn("[GeoBlox] EncodingService missing: cannot decode city data")
		return
	end

	for _, c in Manifest.chunks do
		table.insert(chunks, {
			name = c.name,
			center = Vector3.new(c.center[1], c.center[2], c.center[3]),
			data = nil,
			shared = nil,
			lodFolder = nil,
			lod = -1,
			treeFolder = nil,
			treeLod = -1,
			propFolder = nil,
			collFolder = nil,
			busy = false,
		})
	end

	-- Probe the mesh API once; fall back to lite walls if blocked.
	task.spawn(function()
		local ok = pcall(function()
			local em = game:GetService("AssetService"):CreateEditableMesh()
			em:Destroy()
		end)
		if not ok then
			World.liteMode = true
			warn(
				"[GeoBlox] EditableMesh unavailable (enable Game Settings > Security > Allow Mesh & Image APIs). Using lite walls."
			)
		end
	end)

	local queue: { ChunkState } = {}
	local lastScan = 0
	RunService.Heartbeat:Connect(function()
		local camera = workspace.CurrentCamera
		if not camera then
			return
		end
		local now = os.clock()
		if now - lastScan < 0.4 then
			return
		end
		lastScan = now
		local focus = camera.CFrame.Position
		local character = game:GetService("Players").LocalPlayer.Character
		local body = if character and character.PrimaryPart then character.PrimaryPart.Position else focus
		table.clear(queue)
		local l0, l1 = 0, 0
		for _, c in chunks do
			local d = Vector3.new(c.center.X - focus.X, 0, c.center.Z - focus.Z).Magnitude
			local slack = if c.lod == 0 then Config.HYSTERESIS else 0
			local want = if d < Config.LOD0_RADIUS + slack then 0 elseif d < Config.LOD1_RADIUS then 1 else -1
			if want == 0 then
				l0 += 1
			elseif want == 1 then
				l1 += 1
			end
			if want ~= c.lod and not c.busy then
				table.insert(queue, c)
			end
			-- Trees / props / collision on their own radii (cheap, synchronous-ish).
			local tw = if d < Config.TREE_LOD0_RADIUS then 0 elseif d < Config.LOD1_RADIUS then 1 else -1
			if tw ~= c.treeLod and c.data ~= nil then
				task.spawn(setTrees, c, tw)
				c.treeLod = tw -- reserve; setTrees sets the final value
			end
			if c.data ~= nil then
				setProps(c, d < Config.PROP_RADIUS)
				local db = Vector3.new(c.center.X - body.X, 0, c.center.Z - body.Z).Magnitude
				setCollision(c, db < Config.COLLISION_RADIUS)
			end
		end
		World.stats.chunksLod0, World.stats.chunksLod1 = l0, l1
		table.sort(queue, function(a, b)
			return (a.center - focus).Magnitude < (b.center - focus).Magnitude
		end)
		local deadline = os.clock() + Config.BUILD_BUDGET_MS / 1000
		for _, c in queue do
			if os.clock() > deadline then
				break
			end
			local d = Vector3.new(c.center.X - focus.X, 0, c.center.Z - focus.Z).Magnitude
			setLod(c, if d < Config.LOD0_RADIUS then 0 elseif d < Config.LOD1_RADIUS then 1 else -1)
		end
	end)
end

return World
