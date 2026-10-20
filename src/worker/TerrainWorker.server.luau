--!strict
-- Runs inside an Actor: decodes one terrain chunk, builds voxel arrays in
-- parallel, writes them in the serial phase, and reports back.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local actor = script:GetActor()
if not actor then
	return
end

local Shared = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared")
local TerrainBuild = require(Shared:WaitForChild("TerrainBuild"))
local source = ServerStorage:WaitForChild("GeoBloxTerrain")
local doneEvent = ServerStorage:WaitForChild("GeoBlox"):WaitForChild("ChunkDone") :: BindableEvent
local terrain = workspace.Terrain
local actorName = actor.Name

actor:BindToMessageParallel("Build", function(name: string)
	local module = source:FindFirstChild(name)
	local ok, err = pcall(function()
		assert(module and module:IsA("ModuleScript"), "missing terrain module " .. name)
		task.synchronize() -- require + EncodingService in the serial phase
		local payload = require(module) :: any
		local blob = TerrainBuild.decode(payload)
		task.desynchronize()
		debug.profilebegin("GB:Terrain:Voxels")
		local region, mats, occs = TerrainBuild.voxels(blob)
		debug.profileend()
		task.synchronize()
		if region then
			terrain:WriteVoxels(region, 4, mats, occs)
		end
	end)
	if not ok then
		task.synchronize()
		warn("[GeoBlox] terrain chunk " .. name .. " failed: " .. tostring(err))
	end
	doneEvent:Fire(actorName, name)
end)

actor:SetAttribute("Ready", true)
