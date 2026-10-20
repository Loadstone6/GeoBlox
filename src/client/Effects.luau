--!strict
-- Life in the baked city, driven by CollectionService tags (parts stream in
-- and out, so every tag is tracked as instances arrive and leave):
--   GeoBloxWindow     window glass bands: a stable ~38% light up at night
--   GeoBloxShopfront  shop windows and bus-shelter adverts glow at night
--   GeoBloxLantern    street-lamp lanterns; real PointLights on the nearest
--   GeoBloxDialGlow   Elizabeth Tower clock dials, backlit at night
--   GeoBloxBeacon     Belisha beacons flash at ~1.5 Hz
--   GeoBloxSignal*    traffic signals cycle red / red+amber / green / amber

local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Config = require(ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared"):WaitForChild("Config"))

local Effects = {}

type Saved = { material: Enum.Material, color: Color3, transparency: number }

local night = false
local saved: { [BasePart]: Saved } = {}
local tracked: { [string]: { [BasePart]: boolean } } = {}

local WARM = { Color3.fromRGB(255, 214, 150), Color3.fromRGB(255, 226, 180), Color3.fromRGB(236, 240, 255) }

-- Deterministic 0..1 per part (stable across streaming and clients).
local function hash01(p: BasePart): number
	local v = p.Position
	local h = math.floor(v.X * 7.13) * 73856093 + math.floor(v.Y * 3.71) * 19349663 + math.floor(v.Z * 5.29) * 83492791
	return (h % 1000) / 1000
end

local function remember(p: BasePart)
	if not saved[p] then
		saved[p] = { material = p.Material, color = p.Color, transparency = p.Transparency }
	end
end

local function restore(p: BasePart)
	local s = saved[p]
	if s then
		p.Material = s.material
		p.Color = s.color
		p.Transparency = s.transparency
	end
end

local function applyNight(tag: string, p: BasePart)
	if tag == "GeoBloxWindow" then
		local r = hash01(p)
		if night and r < Config.LIT_WINDOW_SHARE then
			remember(p)
			p.Material = Enum.Material.Neon
			p.Color = WARM[math.floor(r * 97) % #WARM + 1]:Lerp(
				Color3.new(0, 0, 0),
				0.25 + 0.35 * (r / Config.LIT_WINDOW_SHARE)
			)
		else
			restore(p)
		end
	elseif tag == "GeoBloxShopfront" or tag == "GeoBloxDialGlow" then
		if night then
			remember(p)
			p.Material = Enum.Material.Neon
			p.Color = if tag == "GeoBloxDialGlow" then Color3.fromRGB(255, 236, 196) else Color3.fromRGB(250, 236, 206)
		else
			restore(p)
		end
	elseif tag == "GeoBloxLantern" then
		if night then
			remember(p)
			p.Material = Enum.Material.Neon
			p.Transparency = 0.1
		else
			restore(p)
		end
	end
end

local NIGHT_TAGS = { "GeoBloxWindow", "GeoBloxShopfront", "GeoBloxDialGlow", "GeoBloxLantern" }
local OTHER_TAGS = { "GeoBloxBeacon", "GeoBloxSignalRed", "GeoBloxSignalAmber", "GeoBloxSignalGreen" }

local function track(tag: string, apply: boolean)
	local set: { [BasePart]: boolean } = {}
	tracked[tag] = set
	local function add(inst: Instance)
		if inst:IsA("BasePart") then
			set[inst] = true
			if apply then
				applyNight(tag, inst)
			end
		end
	end
	for _, inst in CollectionService:GetTagged(tag) do
		add(inst)
	end
	CollectionService:GetInstanceAddedSignal(tag):Connect(add)
	CollectionService:GetInstanceRemovedSignal(tag):Connect(function(inst)
		set[inst :: BasePart] = nil
		saved[inst :: BasePart] = nil
	end)
end

function Effects.setNight(on: boolean)
	night = on
	for _, tag in NIGHT_TAGS do
		local set = tracked[tag]
		if set then
			for p in set do
				applyNight(tag, p)
			end
		end
	end
end

local lights: { [BasePart]: PointLight } = {}

function Effects.start()
	for _, tag in NIGHT_TAGS do
		track(tag, true)
	end
	for _, tag in OTHER_TAGS do
		track(tag, false)
	end
	local lastLamp = 0
	RunService.Heartbeat:Connect(function()
		local now = os.clock()
		local on = (now % 0.66) < 0.33
		for p in tracked.GeoBloxBeacon do
			p.Transparency = if on then 0 else 0.7
		end
		-- Signals: 24 s cycle, offset per junction so neighbours differ.
		for _, tag in { "GeoBloxSignalRed", "GeoBloxSignalAmber", "GeoBloxSignalGreen" } do
			for p in tracked[tag] do
				local phase = (now + (p.Position.X + p.Position.Z) * 0.013) % 24
				local lit
				if tag == "GeoBloxSignalRed" then
					lit = phase < 11
				elseif tag == "GeoBloxSignalAmber" then
					lit = (phase >= 9 and phase < 11) or phase >= 21
				else
					lit = phase >= 11 and phase < 21
				end
				p.Transparency = if lit then 0 else 0.85
			end
		end
		if now - lastLamp < 1 then
			return
		end
		lastLamp = now
		local cam = workspace.CurrentCamera
		if not cam then
			return
		end
		local pos = cam.CFrame.Position
		local near: { { part: BasePart, d: number } } = {}
		if night then
			for p in tracked.GeoBloxLantern do
				local d = (p.Position - pos).Magnitude
				if d < Config.LAMP_LIGHT_RADIUS then
					table.insert(near, { part = p, d = d })
				end
			end
			table.sort(near, function(a, b)
				return a.d < b.d
			end)
		end
		local keep: { [BasePart]: boolean } = {}
		for i = 1, math.min(#near, Config.MAX_LAMP_LIGHTS) do
			keep[near[i].part] = true
		end
		for p, l in lights do
			if not keep[p] or not p.Parent then
				l:Destroy()
				lights[p] = nil
			end
		end
		for p in keep do
			if not lights[p] then
				local l = Instance.new("PointLight")
				l.Color = Color3.fromRGB(255, 214, 160)
				l.Range = 40
				l.Brightness = 1.4
				l.Shadows = false
				l.Parent = p
				lights[p] = l
			end
		end
	end)
end

return Effects
