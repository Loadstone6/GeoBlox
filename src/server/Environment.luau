--!strict
-- Lighting, atmosphere, clouds and post-processing objects. The client's Sky
-- controller animates them (time of day, weather).

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
	Lighting.Brightness = 2.4
	Lighting.Ambient = rgb(92, 98, 110)
	Lighting.OutdoorAmbient = rgb(132, 138, 150)
	Lighting.EnvironmentDiffuseScale = 1
	Lighting.EnvironmentSpecularScale = 1
	Lighting.GlobalShadows = true
	Lighting.GeographicLatitude = 51.5

	local atmosphere = make("Atmosphere", "Atmosphere", Lighting) :: Atmosphere
	atmosphere.Density = 0.33
	atmosphere.Offset = 0.1
	atmosphere.Color = rgb(196, 204, 216)
	atmosphere.Decay = rgb(112, 124, 146)
	atmosphere.Glare = 0.15
	atmosphere.Haze = 1.8

	local sky = make("Sky", "Sky", Lighting) :: Sky
	sky.CelestialBodiesShown = true
	sky.StarCount = 1500
	sky.SunAngularSize = 11

	local clouds = make("Clouds", "Clouds", workspace.Terrain) :: Clouds
	clouds.Cover = 0.7
	clouds.Density = 0.62
	clouds.Color = rgb(238, 240, 244)

	local grade = make("ColorCorrectionEffect", "Grade", Lighting) :: ColorCorrectionEffect
	grade.Saturation = 0.04
	grade.Contrast = 0.08

	local bloom = make("BloomEffect", "Bloom", Lighting) :: BloomEffect
	bloom.Intensity = 0.4
	bloom.Size = 26
	bloom.Threshold = 1.4

	local rays = make("SunRaysEffect", "SunRays", Lighting) :: SunRaysEffect
	rays.Intensity = 0.06
	rays.Spread = 0.6

	local dof = make("DepthOfFieldEffect", "PhotoDepthOfField", Lighting) :: DepthOfFieldEffect
	dof.Enabled = false
	dof.FarIntensity = 0.2
	dof.FocusDistance = 80
	dof.InFocusRadius = 120

	local terrain = workspace.Terrain
	terrain.WaterColor = rgb(58, 70, 62)
	terrain.WaterTransparency = 0.25
	terrain.WaterReflectance = 0.55
	terrain.WaterWaveSize = 0.12
	terrain.WaterWaveSpeed = 7
	terrain:SetMaterialColor(Enum.Material.Asphalt, rgb(62, 62, 64))
	terrain:SetMaterialColor(Enum.Material.Pavement, rgb(160, 156, 148))
	terrain:SetMaterialColor(Enum.Material.Grass, rgb(86, 116, 56))
	terrain:SetMaterialColor(Enum.Material.Mud, rgb(70, 62, 50))
	workspace.GlobalWind = Vector3.new(9, 0, 4)
end

return Environment
