--!strict
-- Client entry: streams the reconstructed city around the camera, builds the
-- landmarks, runs sky/night lighting, controls and HUD.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Manifest =
	require(ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Generated"):WaitForChild("Manifest")) :: any
local Config = require(ReplicatedStorage.GeoBlox:WaitForChild("Shared"):WaitForChild("Config"))

local Controls = require(script.Parent.Controls)
local Hud = require(script.Parent.Hud)
local Landmarks = require(script.Parent.Landmarks)
local Meshes = require(script.Parent.Meshes)
local Props = require(script.Parent.Props)
local Sky = require(script.Parent.Sky)
local World = require(script.Parent.World)

Sky.start()
World.start()

local landmarks = Instance.new("Folder")
landmarks.Name = "GeoBloxLandmarks"
landmarks.Parent = workspace
task.spawn(Landmarks.londonEye, Manifest.londonEye, landmarks)
task.spawn(Landmarks.bigBen, Manifest.bigBenDials, landmarks)

-- Night: lit windows, glowing shopfronts and dials, lamp lights near the camera.
local lights: { [BasePart]: PointLight } = {}
Sky.onNight(function(night)
	for _, p in World.nightParts do
		p.Transparency = if night then 0.05 else 1
	end
	for _, p in World.shopParts do
		p.Material = if night then Enum.Material.Neon else Enum.Material.Glass
		p.Transparency = if night then 0.45 else 0
	end
	for _, g in Landmarks.dialGlow do
		g.Transparency = if night then 0.15 else 1
	end
end)

local lastLamp = 0
RunService.Heartbeat:Connect(function()
	local now = os.clock()
	-- Belisha beacons flash at ~1 Hz; signals cycle on a fixed plan.
	local on = (now % 1.0) < 0.5
	for _, b in Props.beacons do
		b.Transparency = if on then 0 else 0.75
	end
	local phase = now % 24
	for _, s in Props.signals do
		s[1].Transparency = if phase < 10 or phase >= 22 then 0 else 0.85
		s[2].Transparency = if phase >= 10 and phase < 12 or phase >= 22 then 0 else 0.85
		s[3].Transparency = if phase >= 12 and phase < 22 then 0 else 0.85
	end
	if now - lastLamp < 1 then
		return
	end
	lastLamp = now
	-- Real point lights only on the lamps nearest the camera.
	local cam = workspace.CurrentCamera
	if not cam then
		return
	end
	local night = Sky.isNight()
	local pos = cam.CFrame.Position
	local near: { { part: BasePart, d: number } } = {}
	for _, lamp in Props.lamps do
		if lamp.Parent then
			local d = (lamp.Position - pos).Magnitude
			if d < Config.LAMP_LIGHT_RADIUS then
				table.insert(near, { part = lamp, d = d })
			end
		end
	end
	table.sort(near, function(a, b)
		return a.d < b.d
	end)
	local keep: { [BasePart]: boolean } = {}
	if night then
		for i = 1, math.min(#near, Config.MAX_LAMP_LIGHTS) do
			keep[near[i].part] = true
		end
	end
	for part, light in lights do
		if not keep[part] then
			light:Destroy()
			lights[part] = nil
		end
	end
	for part in keep do
		if not lights[part] then
			local l = Instance.new("PointLight")
			l.Color = Color3.fromRGB(255, 214, 160)
			l.Range = 38
			l.Brightness = 1.6
			l.Shadows = false
			l.Parent = part
			lights[part] = l
		end
	end
	for _, lamp in Props.lamps do
		lamp.Transparency = if night then 0.1 else 0.55
	end
end)

local toggle, toast = Hud.start(function()
	local clock, weather, paused = Sky.status()
	local h = math.floor(clock)
	local m = math.floor((clock - h) * 60)
	local s = World.stats
	local mode = if World.liteMode then "LITE (mesh APIs blocked)" else "meshes"
	return string.format(
		"%s %d  tris %dk  fails %d   LOD0 %d / LOD1 %d chunks   trees %dk tris   props %d   terrain %d/%d\n%02d:%02d%s  %s",
		mode,
		s.meshes,
		s.tris // 1000,
		s.failures + Meshes.failures,
		s.chunksLod0,
		s.chunksLod1,
		s.trees // 1000,
		s.props,
		ReplicatedStorage:GetAttribute("TerrainDone") or 0,
		ReplicatedStorage:GetAttribute("TerrainTotal") or 0,
		h,
		m,
		if paused then " (paused)" else "",
		weather
	)
end)
Controls.setHud(toggle, toast)
Controls.start()
