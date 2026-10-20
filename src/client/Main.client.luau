--!strict
-- Client entry: sky and weather, night lighting and street life on the baked
-- city (tags), animated landmarks, the car controller, controls and HUD.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Controls = require(script.Parent.Controls)
local Drive = require(script.Parent.Drive)
local Effects = require(script.Parent.Effects)
local Hud = require(script.Parent.Hud)
local Landmarks = require(script.Parent.Landmarks)
local Sky = require(script.Parent.Sky)

Sky.start()
Effects.start()
Sky.onNight(function(night)
	Effects.setNight(night)
	Drive.headlights = night
end)
Drive.start()
task.spawn(Landmarks.londonEye)
Landmarks.bigBen()

local toggle, toast = Hud.start(function()
	local clock, weather, paused = Sky.status()
	local h = math.floor(clock)
	local m = math.floor((clock - h) * 60)
	local city = workspace:FindFirstChild("City")
	local loaded = 0
	if city then
		for _, chunk in city:GetChildren() do
			loaded += #chunk:GetChildren()
		end
	end
	local speed = if Drive.car
		then string.format("%d mph   ", math.floor(math.abs(Drive.speed) * 0.3 * 2.23694 + 0.5))
		else ""
	return string.format(
		"%s%02d:%02d%s  %s   models streamed %d   terrain %d/%d",
		speed,
		h,
		m,
		if paused then " (paused)" else "",
		weather,
		loaded,
		ReplicatedStorage:GetAttribute("TerrainDone") or 0,
		ReplicatedStorage:GetAttribute("TerrainTotal") or 0
	)
end)
Controls.setHud(toggle, toast)
Controls.start()
