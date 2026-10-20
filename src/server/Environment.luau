--!strict
-- Creates the lighting, atmosphere, clouds and post-processing objects. The
-- client's Sky controller animates them (time of day, weather, season).

local Lighting = game:GetService("Lighting")

local rgb = Color3.fromRGB

local Environment = {}

local function make<T>(className: string, name: string, parent: Instance): T
	local existing = parent:FindFirstChild(name)
	if existing then
		return (existing :: any) :: T
	end
	local inst = Instance.new(className :: any)
	inst.Name = name
	inst.Parent = parent
	return (inst :: any) :: T
end

function Environment.setup()
	Lighting.Brightness = 2.6
	Lighting.Ambient = rgb(96, 100, 110)
	Lighting.OutdoorAmbient = rgb(136, 140, 150)
	Lighting.EnvironmentDiffuseScale = 1
	Lighting.EnvironmentSpecularScale = 1
	Lighting.GlobalShadows = true
	Lighting.GeographicLatitude = 52.1

	local atmosphere = make("Atmosphere", "Atmosphere", Lighting) :: Atmosphere
	atmosphere.Density = 0.3
	atmosphere.Offset = 0.12
	atmosphere.Color = rgb(199, 210, 225)
	atmosphere.Decay = rgb(106, 130, 160)
	atmosphere.Glare = 0.2
	atmosphere.Haze = 1.6

	local sky = make("Sky", "Sky", Lighting) :: Sky
	sky.CelestialBodiesShown = true
	sky.StarCount = 3000
	sky.SunAngularSize = 12

	local clouds = make("Clouds", "Clouds", workspace.Terrain) :: Clouds
	clouds.Cover = 0.62
	clouds.Density = 0.6
	clouds.Color = rgb(242, 242, 246)

	local grade = make("ColorCorrectionEffect", "Grade", Lighting) :: ColorCorrectionEffect
	grade.Saturation = 0.05
	grade.Contrast = 0.08

	local bloom = make("BloomEffect", "Bloom", Lighting) :: BloomEffect
	bloom.Intensity = 0.35
	bloom.Size = 24
	bloom.Threshold = 1.6

	local rays = make("SunRaysEffect", "SunRays", Lighting) :: SunRaysEffect
	rays.Intensity = 0.08
	rays.Spread = 0.6

	local dof = make("DepthOfFieldEffect", "PhotoDepthOfField", Lighting) :: DepthOfFieldEffect
	dof.Enabled = false
	dof.FarIntensity = 0.25
	dof.FocusDistance = 60
	dof.InFocusRadius = 80

	workspace.GlobalWind = Vector3.new(10, 0, 5)
end

return Environment
