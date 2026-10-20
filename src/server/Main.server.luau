--!strict
-- Server entry point: sets up the environment, generates terrain chunks
-- (in parallel Actors, nearest to spawn first), builds farms, the mill, the
-- bridge and the horizon, and spawns players once the ground under them exists.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerScriptService = game:GetService("ServerScriptService")
local ServerStorage = game:GetService("ServerStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)
local Materials = require(Shared.Materials)
local Sampler = require(Shared.Sampler)
local TerrainChunk = require(Shared.TerrainChunk)

local Buildings = require(script.Parent.Buildings)
local Environment = require(script.Parent.Environment)
local Horizon = require(script.Parent.Horizon)

local layout = Layout.build(Config.SEED)
local sampler = Sampler.new(layout)
local terrain = workspace.Terrain

Environment.setup()
Materials.applyTerrain(terrain, "Spring")
pcall(function()
	(terrain :: any).Decoration = true -- grass blades; also set in the project file
end)

-- Spawn on the road, facing along it.
local spawnX, spawnZ = Layout.toWorld(Config.SPAWN_S, 1, 0)
local spawnPos = Vector3.new(spawnX, Config.ROAD_Y + 0.6, spawnZ)
local spawn = Instance.new("SpawnLocation")
spawn.Name = "RoadSpawn"
spawn.Anchored = true
spawn.Size = Vector3.new(10, 1, 10)
spawn.CFrame = CFrame.lookAt(spawnPos, spawnPos + Vector3.new(1, 0, 0))
spawn.Transparency = 1
spawn.CanCollide = false
spawn.Neutral = true
spawn.Duration = 0
spawn.Parent = workspace

-- Terrain jobs, nearest to spawn first.
type Job = { cx: number, cz: number, dist: number }
local jobs: { Job } = {}
for cx = Config.CHUNK_X0, Config.CHUNK_X1 do
	for cz = Config.CHUNK_Z0, Config.CHUNK_Z1 do
		local mx = (cx + 0.5) * Config.CHUNK_STUDS - spawnX
		local mz = (cz + 0.5) * Config.CHUNK_STUDS - spawnZ
		table.insert(jobs, { cx = cx, cz = cz, dist = math.sqrt(mx * mx + mz * mz) })
	end
end
table.sort(jobs, function(a, b)
	return a.dist < b.dist
end)

local total = #jobs
local completed = 0
local nextJob = 1
local started = os.clock()
ReplicatedStorage:SetAttribute("TerrainTotal", total)
ReplicatedStorage:SetAttribute("TerrainDone", 0)

local function markDone()
	completed += 1
	ReplicatedStorage:SetAttribute("TerrainDone", completed)
	if completed == total then
		print(string.format("[GeoBlox] terrain: %d chunks in %.1fs", total, os.clock() - started))
	end
end

local function runSerial()
	while nextJob <= total do
		local job = jobs[nextJob]
		nextJob += 1
		local region, mats, occs = TerrainChunk.compute(job.cx, job.cz)
		terrain:WriteVoxels(region, Config.VOXEL, mats, occs)
		markDone()
		task.wait()
	end
end

local function runParallel(): boolean
	local actors: { Actor } = {}
	for _, child in ServerScriptService:WaitForChild("TerrainWorkers"):GetChildren() do
		if child:IsA("Actor") then
			table.insert(actors, child)
		end
	end
	local deadline = os.clock() + 10
	for _, actor in actors do
		while not actor:GetAttribute("Ready") and os.clock() < deadline do
			task.wait()
		end
	end
	local ready = {}
	for _, actor in actors do
		if actor:GetAttribute("Ready") then
			ready[actor.Name] = actor
		end
	end
	if next(ready) == nil then
		return false
	end

	local doneEvent = ServerStorage:WaitForChild("GeoBlox"):WaitForChild("ChunkDone") :: BindableEvent
	local function feed(actor: Actor)
		if nextJob <= total then
			local job = jobs[nextJob]
			nextJob += 1
			actor:SendMessage("Build", job.cx, job.cz)
		end
	end
	doneEvent.Event:Connect(function(actorName: string)
		markDone()
		local actor = ready[actorName]
		if actor then
			feed(actor)
		end
	end)
	for _, actor in ready do
		feed(actor)
		feed(actor) -- keep two jobs in flight per worker
	end

	-- Watchdog: if the workers never report back, finish serially.
	task.delay(15, function()
		if completed == 0 then
			warn("[GeoBlox] parallel terrain workers did not respond; falling back to serial generation")
			nextJob = 1
			runSerial()
		end
	end)
	return true
end

task.spawn(function()
	if not (Config.PARALLEL and runParallel()) then
		runSerial()
	end
end)

-- Players spawn once the chunks around the spawn point are written.
local function spawnPlayer(player: Player)
	while completed < math.min(9, total) do
		task.wait(0.1)
	end
	player:LoadCharacter()
end

local function onPlayer(player: Player)
	player.CharacterAdded:Connect(function(character)
		local humanoid = character:WaitForChild("Humanoid") :: Humanoid
		humanoid.Died:Connect(function()
			task.wait(3)
			if player.Parent then
				player:LoadCharacter()
			end
		end)
	end)
	task.spawn(spawnPlayer, player)
end
Players.PlayerAdded:Connect(onPlayer)
for _, player in Players:GetPlayers() do
	onPlayer(player)
end

-- Static structures (independent of terrain timing).
local world = Instance.new("Folder")
world.Name = "GeoBlox"
world.Parent = workspace

local farmsFolder = Instance.new("Folder")
farmsFolder.Name = "Farms"
farmsFolder.Parent = world
Buildings.buildBridge(world, layout)
Buildings.buildWindmill(world, layout, sampler)
for _, farm in layout.farmList do
	Buildings.buildFarm(farmsFolder, farm, sampler)
	task.wait()
end
Horizon.build(world, Config.SEED)
print(
	string.format("[GeoBlox] world ready: %d farms, %d trees, %d herds", #layout.farmList, #layout.trees, #layout.herds)
)
