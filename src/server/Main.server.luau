--!strict
-- Server: lighting/atmosphere, terrain from the reconstructed ground model
-- (parallel Actors, nearest-to-spawn first), spawning players once the ground
-- under them exists. All mesh geometry is built on clients (EditableMesh
-- content does not replicate), so the server only owns terrain.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerScriptService = game:GetService("ServerScriptService")
local ServerStorage = game:GetService("ServerStorage")

local GeoBlox = ReplicatedStorage:WaitForChild("GeoBlox")
local Manifest = require(GeoBlox:WaitForChild("Generated"):WaitForChild("Manifest")) :: any
local Codec = require(GeoBlox:WaitForChild("Shared"):WaitForChild("Codec"))
local TerrainBuild = require(GeoBlox.Shared:WaitForChild("TerrainBuild"))
local Environment = require(script.Parent.Environment)

Environment.setup()
pcall(function()
	(workspace.Terrain :: any).Decoration = true
end)

local spawnPos = Vector3.new(Manifest.spawn[1], Manifest.spawn[2] + 3, Manifest.spawn[3])
local spawn = Instance.new("SpawnLocation")
spawn.Name = "GeoBloxSpawn"
spawn.Anchored = true
spawn.Size = Vector3.new(6, 1, 6)
spawn.CFrame = CFrame.lookAt(spawnPos, spawnPos + Vector3.new(-1, 0, 0))
spawn.Transparency = 1
spawn.CanCollide = false
spawn.Neutral = true
spawn.Duration = 0
spawn.Parent = workspace

ReplicatedStorage:SetAttribute("TerrainTotal", #Manifest.terrain)
ReplicatedStorage:SetAttribute("TerrainDone", 0)
ReplicatedStorage:SetAttribute("CodecOk", Codec.available())
if not Codec.available() then
	warn("[GeoBlox] EncodingService unavailable: update Roblox Studio to load the reconstruction.")
end

type Job = { name: string, dist: number }
local jobs: { Job } = {}
for _, t in Manifest.terrain do
	local cx, cz = t.x0 + 128, t.z0 + 128
	table.insert(jobs, { name = t.name, dist = math.sqrt((cx - spawnPos.X) ^ 2 + (cz - spawnPos.Z) ^ 2) })
end
table.sort(jobs, function(a, b)
	return a.dist < b.dist
end)

local total, completed, nextJob = #jobs, 0, 1
local started = os.clock()
local nearSpawnDone = false

local function markDone()
	completed += 1
	ReplicatedStorage:SetAttribute("TerrainDone", completed)
	if completed >= math.min(9, total) then
		nearSpawnDone = true
	end
	if completed == total then
		print(string.format("[GeoBlox] terrain: %d chunks in %.1fs", total, os.clock() - started))
	end
end

local source = ServerStorage:WaitForChild("GeoBloxTerrain")

local function runSerial()
	while nextJob <= total do
		local job = jobs[nextJob]
		nextJob += 1
		local ok, err = pcall(function()
			local blob = TerrainBuild.decode(require(source:WaitForChild(job.name)) :: any)
			local region, mats, occs = TerrainBuild.voxels(blob)
			if region then
				workspace.Terrain:WriteVoxels(region, 4, mats, occs)
			end
		end)
		if not ok then
			warn("[GeoBlox] terrain " .. job.name .. ": " .. tostring(err))
		end
		markDone()
		task.wait()
	end
end

local function runParallel(): boolean
	local ready: { [string]: Actor } = {}
	local deadline = os.clock() + 10
	for _, child in ServerScriptService:WaitForChild("TerrainWorkers"):GetChildren() do
		if child:IsA("Actor") then
			while not child:GetAttribute("Ready") and os.clock() < deadline do
				task.wait()
			end
			if child:GetAttribute("Ready") then
				ready[child.Name] = child
			end
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
			actor:SendMessage("Build", job.name)
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
		feed(actor)
	end
	task.delay(20, function()
		if completed == 0 then
			warn("[GeoBlox] terrain workers did not respond; generating serially")
			nextJob = 1
			runSerial()
		end
	end)
	return true
end

task.spawn(function()
	if not runParallel() then
		runSerial()
	end
end)

local spawnPlaced = false
local function placeSpawn()
	if spawnPlaced then
		return
	end
	spawnPlaced = true
	-- Put the spawn on the generated terrain surface (the manifest height is approximate).
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Include
	params.FilterDescendantsInstances = { workspace.Terrain }
	local hit = workspace:Raycast(Vector3.new(spawnPos.X, 600, spawnPos.Z), Vector3.new(0, -800, 0), params)
	if hit then
		local p = hit.Position + Vector3.new(0, 3, 0)
		spawn.CFrame = CFrame.lookAt(p, p + Vector3.new(-1, 0, 0))
	end
end

local function spawnPlayer(player: Player)
	local t0 = os.clock()
	while not nearSpawnDone and os.clock() - t0 < 30 do
		task.wait(0.1)
	end
	placeSpawn()
	player:LoadCharacterAsync()
end

local function onPlayer(player: Player)
	player.CharacterAdded:Connect(function(character)
		local humanoid = character:WaitForChild("Humanoid") :: Humanoid
		humanoid.Died:Connect(function()
			task.wait(3)
			if player.Parent then
				player:LoadCharacterAsync()
			end
		end)
	end)
	task.spawn(spawnPlayer, player)
end
Players.PlayerAdded:Connect(onPlayer)
for _, player in Players:GetPlayers() do
	onPlayer(player)
end

print("[GeoBlox] " .. Manifest.attribution)
