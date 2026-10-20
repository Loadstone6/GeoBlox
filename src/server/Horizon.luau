--!strict
-- Far field beyond the terrain region: a patchwork of field planes, the road
-- continuing on its dike, distant tree lines, farms, a church village and a
-- row of wind turbines. Cheap parts, no shadows, hidden by atmosphere haze.

local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Geometry = require(Shared.Geometry)
local Layout = require(Shared.Layout)
local Materials = require(Shared.Materials)
local Rng = require(Shared.Rng)

local M = Enum.Material
local rgb = Color3.fromRGB
local V3 = Vector3.new

local Horizon = {}

local EXTENT = 8192
local PLANE_TOP = Config.FIELD_Y - 0.4
local PATCH_MATERIALS = { M.Grass, M.Grass, M.Sand, M.Ground }

local function noShadow(container: Instance)
	for _, d in container:GetDescendants() do
		if d:IsA("BasePart") then
			d.CastShadow = false
			d.CanQuery = false
		end
	end
end

local function patches(parent: Instance, x0: number, x1: number, z0: number, z1: number, seed: number)
	local palette = Materials.PALETTES.Spring
	local dx, dz = 256, 1024
	local i = 0
	for x = x0, x1 - dx, dx do
		i += 1
		local j = 0
		for z = z0, z1 - dz, dz do
			j += 1
			local idx = 1 + Rng.hash(seed, i, j) % 4
			local p = Geometry.block(
				parent,
				V3(dx + 0.5, 2, dz + 0.5),
				CFrame.new(x + dx / 2, PLANE_TOP - 1, z + dz / 2),
				palette.horizon[idx],
				PATCH_MATERIALS[idx]
			)
			p:SetAttribute("HorizonIdx", idx)
			CollectionService:AddTag(p, "GeoBloxHorizonField")
		end
	end
end

local function roadExtension(parent: Instance, x0: number, x1: number)
	local step = 64
	for x = x0, x1 - step, step do
		local cx = x + step / 2
		local zc, slope = Layout.road(cx)
		local cf = CFrame.new(cx, Config.ROAD_Y, zc) * CFrame.Angles(0, -math.atan(slope), 0)
		local len = step * math.sqrt(1 + slope * slope) + 1
		Geometry.block(
			parent,
			V3(len, Config.ROAD_Y - PLANE_TOP + 1, 34),
			cf * CFrame.new(0, -(Config.ROAD_Y - PLANE_TOP + 1) / 2, 0),
			rgb(92, 128, 52),
			M.Grass
		)
		Geometry.block(parent, V3(len, 0.6, Config.ROAD_HALF * 2), cf * CFrame.new(0, 0, 0), rgb(64, 64, 66), M.Asphalt)
		for _, sz in { 1, -1 } do
			local mid = (Config.STRIP_IN + Config.STRIP_OUT) / 2
			Geometry.block(
				parent,
				V3(len, 0.6, Config.STRIP_OUT - Config.STRIP_IN),
				cf * CFrame.new(0, 0.05, sz * mid),
				rgb(150, 72, 62),
				M.Asphalt
			)
		end
	end
end

local function treeLine(parent: Instance, rng: Rng.Rng, x: number, z: number, alongX: boolean)
	local len = rng:range(180, 620)
	local h = rng:range(40, 90)
	local size = if alongX then V3(len, h, 22) else V3(22, h, len)
	local p = Geometry.block(parent, size, CFrame.new(x, PLANE_TOP + h / 2 + 8, z), rgb(56, 82, 46), M.LeafyGrass)
	CollectionService:AddTag(p, "GeoBloxHorizonTrees")
	-- Trunk band under the crowns so lines read as trees, not hedges.
	Geometry.block(parent, V3(size.X * 0.98, 8, size.Z * 0.6), CFrame.new(x, PLANE_TOP + 4, z), rgb(62, 54, 46), M.Wood)
end

local function farmSilhouette(parent: Instance, rng: Rng.Rng, x: number, z: number)
	local w, d = rng:range(28, 40), rng:range(50, 80)
	local cf = CFrame.new(x, PLANE_TOP, z)
	local wall = if rng:chance(0.5) then rgb(124, 60, 46) else rgb(48, 46, 44)
	Geometry.block(parent, V3(w, 10, d), cf * CFrame.new(0, 5, 0), wall, M.Brick)
	local roof = if rng:chance(0.5) then rgb(66, 66, 70) else rgb(166, 82, 52)
	local function P(px: number, py: number, pz: number): Vector3
		return cf:PointToWorldSpace(V3(px, py, pz))
	end
	Geometry.quad(
		parent,
		P(w / 2 + 1, 10, -d / 2),
		P(w / 2 + 1, 10, d / 2),
		P(0, 28, d / 2),
		P(0, 28, -d / 2),
		1,
		roof,
		M.ClayRoofTiles,
		V3(1, 1, 0)
	)
	Geometry.quad(
		parent,
		P(-w / 2 - 1, 10, d / 2),
		P(-w / 2 - 1, 10, -d / 2),
		P(0, 28, -d / 2),
		P(0, 28, d / 2),
		1,
		roof,
		M.ClayRoofTiles,
		V3(-1, 1, 0)
	)
	Geometry.triangle(parent, P(w / 2, 10, d / 2), P(-w / 2, 10, d / 2), P(0, 28, d / 2), 1, wall, M.Brick)
	Geometry.triangle(parent, P(w / 2, 10, -d / 2), P(-w / 2, 10, -d / 2), P(0, 28, -d / 2), 1, wall, M.Brick)
end

local function village(parent: Instance, rng: Rng.Rng, x: number, z: number)
	local brick = rgb(118, 56, 44)
	local base = V3(x, PLANE_TOP, z)
	-- Church: nave, tower and spire, the landmark of every Dutch horizon.
	Geometry.block(parent, V3(34, 30, 80), CFrame.new(base + V3(0, 15, 20)), brick, M.Brick)
	local nave = CFrame.new(base + V3(0, 0, 20))
	local function P(px: number, py: number, pz: number): Vector3
		return nave:PointToWorldSpace(V3(px, py, pz))
	end
	local slate = rgb(52, 56, 62)
	Geometry.quad(parent, P(18, 30, -40), P(18, 30, 40), P(0, 52, 40), P(0, 52, -40), 1, slate, M.Slate, V3(1, 1, 0))
	Geometry.quad(parent, P(-18, 30, 40), P(-18, 30, -40), P(0, 52, -40), P(0, 52, 40), 1, slate, M.Slate, V3(-1, 1, 0))
	Geometry.block(parent, V3(20, 90, 20), CFrame.new(base + V3(0, 45, -30)), brick, M.Brick)
	Geometry.frustum(parent, base + V3(0, 90, -30), 13, 0.5, 70, 8, slate, M.Slate, 1, math.pi / 8)
	Geometry.block(parent, V3(0.6, 10, 0.6), CFrame.new(base + V3(0, 164, -30)), rgb(200, 170, 80), M.Metal)
	for _ = 1, 28 do
		local a = rng:range(0, math.pi * 2)
		local r = rng:range(70, 260)
		farmSilhouette(parent, rng, x + math.cos(a) * r, z + math.sin(a) * r)
	end
end

local function turbine(parent: Instance, pos: Vector3, facing: Vector3, phase: number)
	local white = rgb(232, 234, 236)
	local towerH = 300
	Geometry.frustum(parent, pos, 7, 4, towerH, 12, white, M.SmoothPlastic, 1)
	local hubPos = pos + V3(0, towerH + 5, 0)
	local cf = CFrame.lookAt(hubPos, hubPos + facing)
	Geometry.block(parent, V3(11, 11, 30), cf * CFrame.new(0, 0, 8), white, M.SmoothPlastic)
	local rotor = Instance.new("Model")
	rotor.Name = "Rotor"
	local hub = cf * CFrame.new(0, 0, -8)
	Geometry.ball(rotor, 8, hub.Position, white, M.SmoothPlastic)
	for i = 0, 2 do
		local arm = hub * CFrame.Angles(0, 0, phase + i * math.pi * 2 / 3)
		Geometry.block(rotor, V3(5, 70, 1.2), arm * CFrame.new(0, 38, 0), white, M.SmoothPlastic)
		Geometry.block(rotor, V3(3, 60, 1), arm * CFrame.new(0.6, 102, 0), white, M.SmoothPlastic)
	end
	rotor:SetAttribute("Hub", hub.Position)
	rotor:SetAttribute("Axis", facing)
	rotor:SetAttribute("Speed", 1.3)
	CollectionService:AddTag(rotor, "GeoBloxRotor")
	rotor.Parent = parent
end

function Horizon.build(parent: Instance, seed: number)
	local folder = Instance.new("Folder")
	folder.Name = "Horizon"
	local rng = Rng.new(Rng.hash(seed, 97))

	patches(folder, -EXTENT, EXTENT, Config.Z_MAX, EXTENT, Rng.hash(seed, 1))
	patches(folder, -EXTENT, EXTENT, -EXTENT, Config.Z_MIN, Rng.hash(seed, 2))
	patches(folder, -EXTENT, Config.X_MIN, Config.Z_MIN, Config.Z_MAX, Rng.hash(seed, 3))
	patches(folder, Config.X_MAX, EXTENT, Config.Z_MIN, Config.Z_MAX, Rng.hash(seed, 4))
	roadExtension(folder, -EXTENT, Config.X_MIN)
	roadExtension(folder, Config.X_MAX, EXTENT)

	local function outside(x: number, z: number, margin: number): boolean
		return x < Config.X_MIN - margin
			or x > Config.X_MAX + margin
			or z < Config.Z_MIN - margin
			or z > Config.Z_MAX + margin
	end
	local placed = 0
	while placed < 90 do
		local x, z = rng:range(-7600, 7600), rng:range(-7600, 7600)
		if outside(x, z, 300) then
			placed += 1
			treeLine(folder, rng, x, z, rng:chance(0.35))
			if rng:chance(0.35) then
				farmSilhouette(folder, rng, x + rng:range(-60, 60), z + rng:range(-60, 60))
			end
		end
	end

	village(folder, rng, 4600, 3400)

	local facing = V3(-0.6, 0, -0.8).Unit
	for i = 0, 6 do
		turbine(folder, V3(-6400 + i * 620, PLANE_TOP, -4300 + i * 140), facing, i * 0.7)
	end

	noShadow(folder)
	folder.Parent = parent
end

return Horizon
