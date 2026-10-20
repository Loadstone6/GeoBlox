--!strict
-- Runs inside an Actor. Computes voxel arrays in parallel, then writes them to
-- terrain in the serial phase and reports back to the dispatcher.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local actor = script:GetActor()
if not actor then
	return
end

local TerrainChunk = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("TerrainChunk"))
local doneEvent = ServerStorage:WaitForChild("GeoBlox"):WaitForChild("ChunkDone") :: BindableEvent
local terrain = workspace.Terrain
local actorName = actor.Name

actor:BindToMessageParallel("Build", function(cx: number, cz: number)
	debug.profilebegin("GB:Terrain:Compute")
	local region, mats, occs = TerrainChunk.compute(cx, cz)
	debug.profileend()
	task.synchronize()
	debug.profilebegin("GB:Terrain:Write")
	terrain:WriteVoxels(region, 4, mats, occs)
	debug.profileend()
	doneEvent:Fire(actorName, cx, cz)
end)

actor:SetAttribute("Ready", true)
