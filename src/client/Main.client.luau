--!strict
-- Client entry point. Rebuilds the same deterministic layout as the server and
-- streams the local-only world detail around the camera.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)
local Sampler = require(Shared.Sampler)

local Animals = require(script.Parent.Animals)
local Birds = require(script.Parent.Birds)
local Controls = require(script.Parent.Controls)
local Decor = require(script.Parent.Decor)
local Hud = require(script.Parent.Hud)
local Sky = require(script.Parent.Sky)

local layout = Layout.build(Config.SEED)
local sampler = Sampler.new(layout)

Sky.start()
Sky.onSeason(Decor.setSeason)
Decor.start(layout, Sampler.new(layout))
Animals.start(layout, Sampler.new(layout))
Birds.start()
Controls.start(layout, sampler)

local toggle, toast = Hud.start(function()
	local clock, weather, season, paused = Sky.status()
	local h = math.floor(clock)
	local m = math.floor((clock - h) * 60)
	return Decor.partCount(), Animals.active(), string.format("%02d:%02d", h, m), weather, season, paused
end)
Controls.setHud(toggle, toast)
