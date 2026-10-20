--!strict
-- Time of day and weather tuned for London (latitude 51.5°): soft, often
-- overcast light, long golden hours in summer, haze over the river. At night
-- the reconstruction lights up: window_lit meshes, shopfronts, lamp lanterns,
-- Big Ben's dials and blinking Belisha beacons.

local Lighting = game:GetService("Lighting")
local RunService = game:GetService("RunService")

local Sky = {}

local rgb = Color3.fromRGB

type Key = {
	t: number,
	brightness: number,
	ambient: Color3,
	outdoor: Color3,
	shift: Color3,
	exposure: number,
	atmo: Color3,
	decay: Color3,
	density: number,
	haze: number,
	glare: number,
	tint: Color3,
	sat: number,
	contrast: number,
	bloom: number,
}

local KEYS: { Key } = {
	{
		t = 0,
		brightness = 0.35,
		ambient = rgb(28, 32, 50),
		outdoor = rgb(46, 54, 82),
		shift = rgb(0, 0, 0),
		exposure = 0.2,
		atmo = rgb(62, 70, 98),
		decay = rgb(40, 40, 60),
		density = 0.34,
		haze = 1.6,
		glare = 0,
		tint = rgb(214, 220, 255),
		sat = -0.1,
		contrast = 0.06,
		bloom = 0.9,
	},
	{
		t = 5.6,
		brightness = 1.3,
		ambient = rgb(72, 70, 84),
		outdoor = rgb(112, 108, 126),
		shift = rgb(255, 168, 120),
		exposure = 0.1,
		atmo = rgb(204, 178, 178),
		decay = rgb(120, 104, 124),
		density = 0.38,
		haze = 2.4,
		glare = 0.5,
		tint = rgb(255, 238, 226),
		sat = 0,
		contrast = 0.06,
		bloom = 0.55,
	},
	{
		t = 8.5,
		brightness = 2.4,
		ambient = rgb(92, 98, 110),
		outdoor = rgb(132, 138, 150),
		shift = rgb(255, 238, 216),
		exposure = 0,
		atmo = rgb(196, 206, 220),
		decay = rgb(112, 126, 148),
		density = 0.33,
		haze = 1.9,
		glare = 0.15,
		tint = rgb(255, 252, 248),
		sat = 0.04,
		contrast = 0.08,
		bloom = 0.35,
	},
	{
		t = 13,
		brightness = 2.8,
		ambient = rgb(98, 104, 116),
		outdoor = rgb(140, 146, 156),
		shift = rgb(255, 250, 242),
		exposure = 0,
		atmo = rgb(198, 210, 228),
		decay = rgb(108, 130, 158),
		density = 0.31,
		haze = 1.6,
		glare = 0.1,
		tint = rgb(255, 255, 255),
		sat = 0.06,
		contrast = 0.09,
		bloom = 0.3,
	},
	{
		t = 17.8,
		brightness = 2.2,
		ambient = rgb(104, 96, 92),
		outdoor = rgb(148, 136, 128),
		shift = rgb(255, 204, 148),
		exposure = 0.05,
		atmo = rgb(226, 200, 176),
		decay = rgb(138, 114, 104),
		density = 0.35,
		haze = 2.2,
		glare = 0.45,
		tint = rgb(255, 242, 226),
		sat = 0.08,
		contrast = 0.1,
		bloom = 0.45,
	},
	{
		t = 20.2,
		brightness = 1.4,
		ambient = rgb(86, 70, 78),
		outdoor = rgb(136, 106, 108),
		shift = rgb(255, 144, 86),
		exposure = 0.15,
		atmo = rgb(236, 170, 142),
		decay = rgb(146, 92, 94),
		density = 0.38,
		haze = 2.7,
		glare = 1.0,
		tint = rgb(255, 228, 210),
		sat = 0.04,
		contrast = 0.08,
		bloom = 0.65,
	},
	{
		t = 21.6,
		brightness = 0.7,
		ambient = rgb(46, 46, 70),
		outdoor = rgb(72, 74, 104),
		shift = rgb(120, 90, 110),
		exposure = 0.1,
		atmo = rgb(108, 104, 138),
		decay = rgb(58, 54, 90),
		density = 0.36,
		haze = 2.1,
		glare = 0.2,
		tint = rgb(226, 226, 255),
		sat = -0.04,
		contrast = 0.06,
		bloom = 0.8,
	},
}

type Weather = {
	name: string,
	cover: number,
	density: number,
	light: number,
	sun: number,
	fog: number,
	haze: number,
	sat: number,
	wind: Vector3,
	rain: number,
	soft: number,
}
local WEATHERS: { Weather } = {
	{
		name = "Overcast",
		cover = 0.86,
		density = 0.72,
		light = 0.66,
		sun = 0.3,
		fog = 0.04,
		haze = 0.8,
		sat = -0.08,
		wind = Vector3.new(14, 0, 6),
		rain = 0,
		soft = 0.9,
	},
	{
		name = "Bright intervals",
		cover = 0.62,
		density = 0.6,
		light = 0.95,
		sun = 0.95,
		fog = 0.0,
		haze = 0.2,
		sat = 0,
		wind = Vector3.new(10, 0, 5),
		rain = 0,
		soft = 0.3,
	},
	{
		name = "Clear",
		cover = 0.35,
		density = 0.5,
		light = 1.0,
		sun = 1.0,
		fog = -0.02,
		haze = 0,
		sat = 0.02,
		wind = Vector3.new(6, 0, 3),
		rain = 0,
		soft = 0.2,
	},
	{
		name = "River mist",
		cover = 0.7,
		density = 0.5,
		light = 0.7,
		sun = 0.45,
		fog = 0.24,
		haze = 2.6,
		sat = -0.1,
		wind = Vector3.new(2, 0, 1),
		rain = 0,
		soft = 0.7,
	},
	{
		name = "Drizzle",
		cover = 0.95,
		density = 0.86,
		light = 0.5,
		sun = 0.15,
		fog = 0.1,
		haze = 1.8,
		sat = -0.18,
		wind = Vector3.new(18, 0, 7),
		rain = 1,
		soft = 1,
	},
}

local clock = 18.4
local paused = false
local hoursPerSecond = 1 / 90
local weatherIdx = 2
local w: Weather = table.clone(WEATHERS[weatherIdx])
local nightListeners: { (boolean) -> () } = {}
local isNight = false

local function lerp(a: number, b: number, t: number): number
	return a + (b - a) * t
end

local function sample(t: number): (Key, Key, number)
	for i = #KEYS, 1, -1 do
		if t >= KEYS[i].t then
			local a, b = KEYS[i], KEYS[i % #KEYS + 1]
			local span = (b.t - a.t) % 24
			return a, b, ((t - a.t) % 24) / (if span == 0 then 24 else span)
		end
	end
	return KEYS[1], KEYS[2], 0
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

function Sky.onNight(fn: (boolean) -> ())
	table.insert(nightListeners, fn)
end

function Sky.status(): (number, string, boolean)
	return clock, WEATHERS[weatherIdx].name, paused
end

function Sky.isNight(): boolean
	return isNight
end

function Sky.start()
	local atmosphere = Lighting:WaitForChild("Atmosphere") :: Atmosphere
	local grade = Lighting:WaitForChild("Grade") :: ColorCorrectionEffect
	local bloom = Lighting:WaitForChild("Bloom") :: BloomEffect
	local rays = Lighting:WaitForChild("SunRays") :: SunRaysEffect
	local clouds = workspace.Terrain:WaitForChild("Clouds") :: Clouds

	local rainPart = Instance.new("Part")
	rainPart.Anchored = true
	rainPart.CanCollide = false
	rainPart.CanQuery = false
	rainPart.CanTouch = false
	rainPart.Transparency = 1
	rainPart.Size = Vector3.new(180, 1, 180)
	local rain = Instance.new("ParticleEmitter")
	rain.Rate = 0
	rain.Lifetime = NumberRange.new(1.2, 1.6)
	rain.Speed = NumberRange.new(80, 100)
	rain.EmissionDirection = Enum.NormalId.Bottom
	rain.Size = NumberSequence.new(0.1)
	rain.Squash = NumberSequence.new(4)
	rain.Orientation = Enum.ParticleOrientation.VelocityParallel
	rain.Color = ColorSequence.new(rgb(196, 206, 216))
	rain.Transparency = NumberSequence.new(0.5)
	rain.Parent = rainPart
	rainPart.Parent = workspace

	RunService.RenderStepped:Connect(function(dt: number)
		if not paused then
			clock = (clock + dt * hoursPerSecond) % 24
		end
		local target = WEATHERS[weatherIdx]
		local k = math.min(1, dt * 0.2)
		for _, f in { "cover", "density", "light", "sun", "fog", "haze", "sat", "rain", "soft" } do
			(w :: any)[f] = lerp((w :: any)[f], (target :: any)[f], k)
		end
		w.wind = w.wind:Lerp(target.wind, k)
		local a, b, t = sample(clock)
		Lighting.ClockTime = clock
		Lighting.Brightness = lerp(a.brightness, b.brightness, t) * w.light
		Lighting.Ambient = a.ambient:Lerp(b.ambient, t)
		Lighting.OutdoorAmbient = a.outdoor:Lerp(b.outdoor, t)
		Lighting.ColorShift_Top = Color3.new():Lerp(a.shift:Lerp(b.shift, t), w.sun)
		Lighting.ExposureCompensation = lerp(a.exposure, b.exposure, t)
		Lighting.ShadowSoftness = w.soft
		atmosphere.Color = a.atmo:Lerp(b.atmo, t):Lerp(rgb(178, 184, 192), (1 - w.sun) * 0.55)
		atmosphere.Decay = a.decay:Lerp(b.decay, t)
		atmosphere.Density = math.clamp(lerp(a.density, b.density, t) + w.fog, 0, 1)
		atmosphere.Haze = lerp(a.haze, b.haze, t) + w.haze
		atmosphere.Glare = lerp(a.glare, b.glare, t) * w.sun
		grade.TintColor = a.tint:Lerp(b.tint, t)
		grade.Saturation = lerp(a.sat, b.sat, t) + w.sat
		grade.Contrast = lerp(a.contrast, b.contrast, t)
		bloom.Intensity = lerp(a.bloom, b.bloom, t)
		rays.Intensity = 0.07 * w.sun
		clouds.Cover = w.cover
		clouds.Density = w.density
		workspace.GlobalWind = w.wind
		local cam = workspace.CurrentCamera
		if cam then
			rainPart.CFrame = CFrame.new(cam.CFrame.Position + Vector3.new(w.wind.X * 0.8, 70, w.wind.Z * 0.8))
		end
		rain.Rate = w.rain * 1600

		local night = clock < 6.2 or clock > 20.4
		if night ~= isNight then
			isNight = night
			for _, fn in nightListeners do
				fn(night)
			end
		end
	end)
end

return Sky
