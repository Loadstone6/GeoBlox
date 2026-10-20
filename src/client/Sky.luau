--!strict
-- Time of day, weather and season. Lighting is driven by keyframes tuned for
-- a Dutch sky (soft light, long golden hours, high cloud cover), blended with
-- the current weather. Also spins windmill sails and turbine rotors with the wind.

local CollectionService = game:GetService("CollectionService")
local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Materials = require(Shared.Materials)

local rgb = Color3.fromRGB

type Key = {
	t: number,
	brightness: number,
	ambient: Color3,
	outdoor: Color3,
	shift: Color3,
	exposure: number,
	atmoColor: Color3,
	atmoDecay: Color3,
	density: number,
	haze: number,
	glare: number,
	tint: Color3,
	saturation: number,
	contrast: number,
	bloom: number,
	rays: number,
}

local KEYS: { Key } = {
	{
		t = 0,
		brightness = 0.5,
		ambient = rgb(22, 26, 42),
		outdoor = rgb(42, 52, 78),
		shift = rgb(0, 0, 0),
		exposure = -0.2,
		atmoColor = rgb(60, 70, 96),
		atmoDecay = rgb(30, 36, 56),
		density = 0.32,
		haze = 1.4,
		glare = 0,
		tint = rgb(200, 212, 255),
		saturation = -0.15,
		contrast = 0.05,
		bloom = 0.3,
		rays = 0,
	},
	{
		t = 5.4,
		brightness = 1.4,
		ambient = rgb(70, 66, 78),
		outdoor = rgb(112, 106, 122),
		shift = rgb(255, 160, 110),
		exposure = 0.1,
		atmoColor = rgb(204, 172, 172),
		atmoDecay = rgb(122, 100, 122),
		density = 0.36,
		haze = 2.2,
		glare = 0.6,
		tint = rgb(255, 236, 222),
		saturation = 0,
		contrast = 0.05,
		bloom = 0.5,
		rays = 0.15,
	},
	{
		t = 8,
		brightness = 2.6,
		ambient = rgb(94, 100, 110),
		outdoor = rgb(134, 140, 150),
		shift = rgb(255, 236, 212),
		exposure = 0,
		atmoColor = rgb(199, 210, 225),
		atmoDecay = rgb(110, 126, 150),
		density = 0.3,
		haze = 1.6,
		glare = 0.2,
		tint = rgb(255, 250, 246),
		saturation = 0.05,
		contrast = 0.08,
		bloom = 0.35,
		rays = 0.08,
	},
	{
		t = 13,
		brightness = 3,
		ambient = rgb(100, 106, 116),
		outdoor = rgb(140, 146, 156),
		shift = rgb(255, 250, 240),
		exposure = 0,
		atmoColor = rgb(199, 214, 234),
		atmoDecay = rgb(106, 130, 160),
		density = 0.28,
		haze = 1.2,
		glare = 0.1,
		tint = rgb(255, 255, 255),
		saturation = 0.08,
		contrast = 0.1,
		bloom = 0.3,
		rays = 0.05,
	},
	{
		t = 17.6,
		brightness = 2.4,
		ambient = rgb(106, 96, 90),
		outdoor = rgb(150, 136, 126),
		shift = rgb(255, 202, 142),
		exposure = 0.05,
		atmoColor = rgb(230, 200, 170),
		atmoDecay = rgb(140, 112, 100),
		density = 0.32,
		haze = 2,
		glare = 0.5,
		tint = rgb(255, 240, 222),
		saturation = 0.1,
		contrast = 0.1,
		bloom = 0.45,
		rays = 0.12,
	},
	{
		t = 19.8,
		brightness = 1.6,
		ambient = rgb(90, 70, 76),
		outdoor = rgb(140, 106, 106),
		shift = rgb(255, 140, 80),
		exposure = 0.15,
		atmoColor = rgb(240, 170, 140),
		atmoDecay = rgb(150, 90, 90),
		density = 0.36,
		haze = 2.6,
		glare = 1.2,
		tint = rgb(255, 226, 206),
		saturation = 0.05,
		contrast = 0.08,
		bloom = 0.6,
		rays = 0.2,
	},
	{
		t = 21.4,
		brightness = 0.9,
		ambient = rgb(44, 44, 66),
		outdoor = rgb(70, 72, 100),
		shift = rgb(120, 90, 110),
		exposure = -0.1,
		atmoColor = rgb(110, 106, 140),
		atmoDecay = rgb(60, 56, 90),
		density = 0.34,
		haze = 2,
		glare = 0.2,
		tint = rgb(222, 222, 255),
		saturation = -0.05,
		contrast = 0.05,
		bloom = 0.4,
		rays = 0.05,
	},
}

type Weather = {
	name: string,
	cover: number,
	cloudDensity: number,
	light: number, -- brightness multiplier
	sun: number, -- how much sun colour/shift survives
	density: number, -- added atmosphere density
	haze: number, -- added haze
	saturation: number,
	wind: Vector3,
	rain: number,
	softness: number,
}

local WEATHERS: { Weather } = {
	{
		name = "Clear",
		cover = 0.4,
		cloudDensity = 0.5,
		light = 1,
		sun = 1,
		density = 0,
		haze = 0,
		saturation = 0,
		wind = Vector3.new(6, 0, 3),
		rain = 0,
		softness = 0.2,
	},
	{
		name = "Broken clouds",
		cover = 0.64,
		cloudDensity = 0.62,
		light = 0.94,
		sun = 0.9,
		density = 0.02,
		haze = 0.3,
		saturation = 0,
		wind = Vector3.new(12, 0, 6),
		rain = 0,
		softness = 0.3,
	},
	{
		name = "Overcast",
		cover = 0.9,
		cloudDensity = 0.78,
		light = 0.62,
		sun = 0.25,
		density = 0.06,
		haze = 1,
		saturation = -0.12,
		wind = Vector3.new(16, 0, 7),
		rain = 0,
		softness = 0.9,
	},
	{
		name = "Mist",
		cover = 0.72,
		cloudDensity = 0.5,
		light = 0.72,
		sun = 0.5,
		density = 0.26,
		haze = 2.4,
		saturation = -0.08,
		wind = Vector3.new(2, 0, 1),
		rain = 0,
		softness = 0.7,
	},
	{
		name = "Rain",
		cover = 0.96,
		cloudDensity = 0.88,
		light = 0.48,
		sun = 0.15,
		density = 0.12,
		haze = 2,
		saturation = -0.2,
		wind = Vector3.new(24, 0, 9),
		rain = 1,
		softness = 1,
	},
}

local Sky = {}

local clock = 17.2
local paused = false
local hoursPerSecond = 1 / 120 -- one game hour per two real minutes
local weatherIdx = 2
local blend: Weather = table.clone(WEATHERS[weatherIdx])
local seasonIdx = 1
local seasonListeners: { (string) -> () } = {}

local function lerp(a: number, b: number, t: number): number
	return a + (b - a) * t
end

local function sample(t: number): (Key, Key, number)
	for i = #KEYS, 1, -1 do
		if t >= KEYS[i].t then
			local a = KEYS[i]
			local b = KEYS[i % #KEYS + 1]
			local span = (b.t - a.t) % 24
			if span == 0 then
				span = 24
			end
			return a, b, ((t - a.t) % 24) / span
		end
	end
	return KEYS[1], KEYS[2], 0
end

local function blendWeather(dt: number)
	local target = WEATHERS[weatherIdx]
	local k = math.min(1, dt * 0.15)
	for _, field in { "cover", "cloudDensity", "light", "sun", "density", "haze", "saturation", "rain", "softness" } do
		(blend :: any)[field] = lerp((blend :: any)[field], (target :: any)[field], k)
	end
	blend.wind = blend.wind:Lerp(target.wind, k)
	blend.name = target.name
end

local function makeRain(): (BasePart, ParticleEmitter)
	local part = Instance.new("Part")
	part.Name = "RainEmitter"
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.Transparency = 1
	part.Size = Vector3.new(160, 1, 160)
	local emitter = Instance.new("ParticleEmitter")
	emitter.Rate = 0
	emitter.Lifetime = NumberRange.new(1.2, 1.6)
	emitter.Speed = NumberRange.new(90, 110)
	emitter.EmissionDirection = Enum.NormalId.Bottom
	emitter.Size = NumberSequence.new(0.12)
	emitter.Squash = NumberSequence.new(4)
	emitter.Orientation = Enum.ParticleOrientation.VelocityParallel
	emitter.Color = ColorSequence.new(rgb(196, 206, 216))
	emitter.Transparency = NumberSequence.new(0.45)
	emitter.LightInfluence = 1
	emitter.Parent = part
	part.Parent = workspace
	return part, emitter
end

function Sky.addHours(h: number)
	clock = (clock + h) % 24
end

function Sky.togglePause(): boolean
	paused = not paused
	return paused
end

function Sky.nextWeather(): string
	weatherIdx = weatherIdx % #WEATHERS + 1
	return WEATHERS[weatherIdx].name
end

function Sky.nextSeason(): string
	seasonIdx = seasonIdx % #Materials.SEASONS + 1
	local name = Materials.SEASONS[seasonIdx]
	Materials.applyTerrain(workspace.Terrain, name)
	local palette = Materials.PALETTES[name]
	for _, p in CollectionService:GetTagged("GeoBloxHorizonField") do
		local idx = p:GetAttribute("HorizonIdx")
		if p:IsA("BasePart") and typeof(idx) == "number" then
			p.Color = palette.horizon[idx]
		end
	end
	for _, p in CollectionService:GetTagged("GeoBloxHorizonTrees") do
		if p:IsA("BasePart") then
			p.Color = if name == "Winter"
				then rgb(92, 84, 76)
				elseif name == "Autumn" then rgb(126, 104, 56)
				else rgb(56, 82, 46)
		end
	end
	for _, fn in seasonListeners do
		fn(name)
	end
	return name
end

function Sky.onSeason(fn: (string) -> ())
	table.insert(seasonListeners, fn)
end

function Sky.status(): (number, string, string, boolean)
	return clock, blend.name, Materials.SEASONS[seasonIdx], paused
end

function Sky.start()
	local atmosphere = Lighting:WaitForChild("Atmosphere") :: Atmosphere
	local grade = Lighting:WaitForChild("Grade") :: ColorCorrectionEffect
	local bloom = Lighting:WaitForChild("Bloom") :: BloomEffect
	local rays = Lighting:WaitForChild("SunRays") :: SunRaysEffect
	local clouds = workspace.Terrain:WaitForChild("Clouds") :: Clouds
	local rainPart, rain = makeRain()
	Materials.applyTerrain(workspace.Terrain, Materials.SEASONS[seasonIdx])

	type Rotor = { model: Model, hub: Vector3, axis: Vector3, speed: number, base: CFrame, angle: number }
	local rotors: { [Model]: Rotor } = {}
	local function addRotor(inst: Instance)
		if not inst:IsA("Model") then
			return
		end
		local hub, axis, speed = inst:GetAttribute("Hub"), inst:GetAttribute("Axis"), inst:GetAttribute("Speed")
		if typeof(hub) == "Vector3" and typeof(axis) == "Vector3" and typeof(speed) == "number" then
			rotors[inst] = {
				model = inst,
				hub = hub,
				axis = axis.Unit,
				speed = speed,
				base = CFrame.new(hub):Inverse() * inst:GetPivot(),
				angle = 0,
			}
		end
	end
	CollectionService:GetInstanceAddedSignal("GeoBloxRotor"):Connect(addRotor)
	for _, inst in CollectionService:GetTagged("GeoBloxRotor") do
		addRotor(inst)
	end

	RunService.RenderStepped:Connect(function(dt: number)
		if not paused then
			clock = (clock + dt * hoursPerSecond) % 24
		end
		blendWeather(dt)
		local a, b, t = sample(clock)
		local w = blend

		Lighting.ClockTime = clock
		Lighting.Brightness = lerp(a.brightness, b.brightness, t) * w.light
		Lighting.Ambient = a.ambient:Lerp(b.ambient, t)
		Lighting.OutdoorAmbient = a.outdoor:Lerp(b.outdoor, t)
		local shift = a.shift:Lerp(b.shift, t)
		Lighting.ColorShift_Top = Color3.new(0, 0, 0):Lerp(shift, w.sun)
		Lighting.ExposureCompensation = lerp(a.exposure, b.exposure, t)
		Lighting.ShadowSoftness = w.softness

		local overcastGrey = rgb(176, 182, 190)
		atmosphere.Color = a.atmoColor:Lerp(b.atmoColor, t):Lerp(overcastGrey, (1 - w.sun) * 0.6)
		atmosphere.Decay = a.atmoDecay:Lerp(b.atmoDecay, t)
		atmosphere.Density = math.clamp(lerp(a.density, b.density, t) + w.density, 0, 1)
		atmosphere.Haze = lerp(a.haze, b.haze, t) + w.haze
		atmosphere.Glare = lerp(a.glare, b.glare, t) * w.sun

		grade.TintColor = a.tint:Lerp(b.tint, t)
		grade.Saturation = lerp(a.saturation, b.saturation, t) + w.saturation
		grade.Contrast = lerp(a.contrast, b.contrast, t)
		bloom.Intensity = lerp(a.bloom, b.bloom, t)
		rays.Intensity = lerp(a.rays, b.rays, t) * w.sun

		clouds.Cover = w.cover
		clouds.Density = w.cloudDensity
		workspace.GlobalWind = w.wind

		local camera = workspace.CurrentCamera
		if camera then
			local cp = camera.CFrame.Position
			rainPart.CFrame = CFrame.new(cp + Vector3.new(w.wind.X * 0.8, 70, w.wind.Z * 0.8))
		end
		rain.Rate = w.rain * 1800

		local windFactor = 0.35 + w.wind.Magnitude / 20
		for _, r in rotors do
			r.angle += r.speed * windFactor * dt
			r.model:PivotTo(CFrame.new(r.hub) * CFrame.fromAxisAngle(r.axis, r.angle) * r.base)
		end
	end)
end

return Sky
