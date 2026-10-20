--!strict
-- Client-side decor streaming. The layout is deterministic, so every client
-- builds the same trees, gates, bales, hedges and road markings locally, in
-- rings around the camera: LOD 2 near, LOD 1 far, nothing beyond. Work is
-- time-sliced so building never costs more than a few ms per frame.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)
local Materials = require(Shared.Materials)
local Sampler = require(Shared.Sampler)

local Props = require(script.Parent.Props)
local Trees = require(script.Parent.Trees)

type Leaf = Trees.Leaf

type Chunk = {
	cx: number,
	cz: number,
	center: Vector3,
	trees: { Layout.Tree },
	props: { Layout.Prop },
	hasRoad: boolean,
	lod: number,
	folder: Folder?,
	leaves: { Leaf },
	parts: number,
}

local Decor = {}

local rgb = Color3.fromRGB
local HEDGE = {
	Spring = rgb(84, 124, 52),
	Summer = rgb(56, 88, 40),
	Autumn = rgb(128, 96, 48),
	Winter = rgb(110, 80, 52),
}

local chunks: { [number]: Chunk } = {}
local queue: { Chunk } = {}
local root: Folder
local sampler: Sampler.Sampler
local season = "Spring"
local partCount = 0

local function key(cx: number, cz: number): number
	return (cx + 512) * 1024 + (cz + 512)
end

local function chunkOf(x: number, z: number): (number, number)
	return x // Config.CHUNK_STUDS, z // Config.CHUNK_STUDS
end

local function paletteFor(name: string): any
	local p = table.clone(Materials.PALETTES[name]) :: any
	p.hedge = HEDGE[name]
	return p
end

local function recolor(leaf: Leaf, palette: any)
	if leaf.kind == "willow" then
		leaf.part.Color = palette.willow
	elseif leaf.kind == "hedge" then
		leaf.part.Color = palette.hedge
	else
		leaf.part.Color = palette.leaves[(leaf.idx - 1) % #palette.leaves + 1]
		leaf.part.Transparency = palette.leafTransparency
	end
end

local function build(chunk: Chunk, lod: number)
	local old = chunk.folder
	chunk.folder = nil
	chunk.leaves = {}
	if lod > 0 then
		local folder = Instance.new("Folder")
		folder.Name = string.format("Decor_%d_%d", chunk.cx, chunk.cz)
		local palette = paletteFor(season)
		for _, tree in chunk.trees do
			Trees.build(folder, tree, sampler:height(tree.x, tree.z) - 0.6, lod, palette, chunk.leaves)
		end
		if lod >= 2 then
			for _, prop in chunk.props do
				Props.build(folder, prop, sampler:height(prop.x, prop.z), palette, chunk.leaves)
			end
		end
		if chunk.hasRoad then
			local x0 = chunk.cx * Config.CHUNK_STUDS
			Props.road(folder, x0, x0 + Config.CHUNK_STUDS)
		end
		local n = #folder:GetChildren()
		partCount += n - chunk.parts
		chunk.parts = n
		folder.Parent = root
		chunk.folder = folder
	else
		partCount -= chunk.parts
		chunk.parts = 0
	end
	if old then
		old:Destroy()
	end
	chunk.lod = lod
end

local function desiredLod(chunk: Chunk, focus: Vector3): number
	local dx, dz = chunk.center.X - focus.X, chunk.center.Z - focus.Z
	local dist = math.sqrt(dx * dx + dz * dz)
	-- Hysteresis so chunks on a ring boundary don't flip back and forth.
	local slack = if chunk.lod > 0 then 64 else 0
	if dist < Config.DECOR_NEAR + (if chunk.lod == 2 then slack else 0) then
		return 2
	elseif dist < Config.DECOR_FAR + slack then
		return 1
	end
	return 0
end

function Decor.setSeason(name: string)
	season = name
	local palette = paletteFor(name)
	for _, chunk in chunks do
		for _, leaf in chunk.leaves do
			recolor(leaf, palette)
		end
	end
end

function Decor.partCount(): number
	return partCount
end

function Decor.start(layout: Layout.Layout, smp: Sampler.Sampler)
	sampler = smp
	root = Instance.new("Folder")
	root.Name = "GeoBloxDecor"
	root.Parent = workspace

	for cx = Config.CHUNK_X0, Config.CHUNK_X1 do
		for cz = Config.CHUNK_Z0, Config.CHUNK_Z1 do
			local x0, z0 = cx * Config.CHUNK_STUDS, cz * Config.CHUNK_STUDS
			local hasRoad = false
			for x = x0, x0 + Config.CHUNK_STUDS, 32 do
				local zc = Layout.road(x)
				if zc + 14 > z0 and zc - 14 < z0 + Config.CHUNK_STUDS then
					hasRoad = true
				end
			end
			chunks[key(cx, cz)] = {
				cx = cx,
				cz = cz,
				center = Vector3.new(x0 + Config.CHUNK_STUDS / 2, Config.FIELD_Y, z0 + Config.CHUNK_STUDS / 2),
				trees = {},
				props = {},
				hasRoad = hasRoad,
				lod = 0,
				folder = nil,
				leaves = {},
				parts = 0,
			}
		end
	end
	for _, tree in layout.trees do
		local c = chunks[key(chunkOf(tree.x, tree.z))]
		if c then
			table.insert(c.trees, tree)
		end
	end
	for _, prop in layout.props do
		local c = chunks[key(chunkOf(prop.x, prop.z))]
		if c then
			table.insert(c.props, prop)
		end
	end

	local lastScan = 0
	RunService.Heartbeat:Connect(function()
		local now = os.clock()
		local camera = workspace.CurrentCamera
		if not camera then
			return
		end
		local focus = camera.CFrame.Position
		if now - lastScan > 0.3 then
			lastScan = now
			table.clear(queue)
			for _, chunk in chunks do
				if desiredLod(chunk, focus) ~= chunk.lod then
					table.insert(queue, chunk)
				end
			end
			table.sort(queue, function(a, b)
				return (a.center - focus).Magnitude < (b.center - focus).Magnitude
			end)
		end
		local deadline = now + Config.DECOR_BUDGET_MS / 1000
		while #queue > 0 and os.clock() < deadline do
			local chunk = table.remove(queue, 1) :: Chunk
			local lod = desiredLod(chunk, focus)
			if lod ~= chunk.lod then
				debug.profilebegin("GB:Decor:Build")
				build(chunk, lod)
				debug.profileend()
			end
		end
	end)
end

return Decor
