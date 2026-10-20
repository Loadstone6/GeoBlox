--!strict
-- Grazing herds: Dutch cattle breeds (Holstein-Friesian, MRIJ red-white,
-- Blaarkop, Lakenvelder) and Texel sheep. Spawned only near the camera,
-- wandering inside their parcel with a graze / walk / rest state machine.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Geometry = require(Shared.Geometry)
local Layout = require(Shared.Layout)
local Rng = require(Shared.Rng)
local Sampler = require(Shared.Sampler)

local M = Enum.Material
local rgb = Color3.fromRGB
local V3 = Vector3.new

type Animal = {
	model: Model,
	head: BasePart,
	headUp: CFrame,
	headDown: CFrame,
	s: number,
	d: number,
	yaw: number,
	ts: number,
	td: number,
	state: string,
	timer: number,
	speed: number,
	rng: Rng.Rng,
}

type HerdState = { herd: Layout.Herd, center: Vector3, radius: number, animals: { Animal }?, folder: Folder? }

local Animals = {}

local herds: { HerdState } = {}
local sampler: Sampler.Sampler
local root: Folder
local active = 0

local WHITE = rgb(238, 236, 230)
local BLACK = rgb(30, 28, 28)
local RED = rgb(124, 58, 38)
local PINK = rgb(214, 150, 146)

local function cow(breed: string, rng: Rng.Rng): (Model, BasePart, CFrame, CFrame)
	local model = Instance.new("Model")
	local base = if breed == "blaarkop" or breed == "lakenvelder" then BLACK else WHITE
	local patch = if breed == "mrij" then RED else BLACK
	Geometry.block(model, V3(2.8, 2.8, 6.8), CFrame.new(0, 4.3, 0), base, M.SmoothPlastic)
	if breed == "holstein" or breed == "mrij" then
		for _ = 1, rng:int(2, 4) do
			local size = V3(2.9, rng:range(1.4, 2.6), rng:range(1.4, 3))
			Geometry.block(
				model,
				size,
				CFrame.new(0, 4.3 + rng:range(-0.5, 0.5), rng:range(-2.4, 2.4)),
				patch,
				M.SmoothPlastic
			)
		end
	elseif breed == "lakenvelder" then
		Geometry.block(model, V3(2.9, 2.9, 2.4), CFrame.new(0, 4.3, 0.2), WHITE, M.SmoothPlastic)
	end
	for _, lx in { -0.9, 0.9 } do
		for _, lz in { -2.5, 2.5 } do
			Geometry.block(
				model,
				V3(0.7, 3, 0.7),
				CFrame.new(lx, 1.5, lz),
				if breed == "holstein" then WHITE else base,
				M.SmoothPlastic
			)
		end
	end
	Geometry.block(model, V3(1, 0.7, 1.3), CFrame.new(0, 2.6, 1.8), PINK, M.SmoothPlastic)
	Geometry.block(model, V3(0.25, 2.6, 0.25), CFrame.new(0, 4, 3.5), base, M.SmoothPlastic)
	local headColor = if breed == "blaarkop" then WHITE else (if breed == "mrij" then RED else base)
	local headUp = CFrame.new(0, 5, -4.2) * CFrame.Angles(math.rad(-15), 0, 0)
	local headDown = CFrame.new(0, 2.2, -4.4) * CFrame.Angles(math.rad(-60), 0, 0)
	local head = Geometry.block(model, V3(1.6, 1.7, 2.6), headUp, headColor, M.SmoothPlastic)
	model.WorldPivot = CFrame.new() -- pivot on the ground between the hooves
	for _, d in model:GetDescendants() do
		if d:IsA("BasePart") then
			d.CanQuery = false
			d.CanCollide = false
		end
	end
	return model, head, headUp, headDown
end

local function sheep(rng: Rng.Rng): (Model, BasePart, CFrame, CFrame)
	local model = Instance.new("Model")
	local wool = rgb(222 + rng:int(-8, 8), 218, 204)
	Geometry.block(model, V3(2.6, 2.3, 4.2), CFrame.new(0, 2.7, 0), wool, M.Fabric)
	for _, lx in { -0.7, 0.7 } do
		for _, lz in { -1.5, 1.5 } do
			Geometry.block(model, V3(0.45, 1.7, 0.45), CFrame.new(lx, 0.85, lz), rgb(210, 206, 196), M.SmoothPlastic)
		end
	end
	local headUp = CFrame.new(0, 3.2, -2.6) * CFrame.Angles(math.rad(-10), 0, 0)
	local headDown = CFrame.new(0, 1.3, -2.7) * CFrame.Angles(math.rad(-65), 0, 0)
	local head = Geometry.block(model, V3(1.1, 1.3, 1.7), headUp, rgb(232, 228, 220), M.SmoothPlastic)
	model.WorldPivot = CFrame.new() -- pivot on the ground between the hooves
	for _, d in model:GetDescendants() do
		if d:IsA("BasePart") then
			d.CanQuery = false
			d.CanCollide = false
		end
	end
	return model, head, headUp, headDown
end

local function pickTarget(a: Animal, herd: Layout.Herd)
	a.ts = math.clamp(a.s + a.rng:range(-40, 40), herd.s0, herd.s1)
	a.td = math.clamp(a.d + a.rng:range(-40, 40), herd.d0, herd.d1)
end

local function pose(a: Animal, herd: Layout.Herd)
	local x, z = Layout.toWorld(a.s, herd.side, a.d)
	local y = sampler:height(x, z) - 0.2
	local cf = CFrame.new(x, y, z) * CFrame.Angles(0, a.yaw, 0)
	local headLocal = if a.state == "graze" then a.headDown else a.headUp
	a.model:PivotTo(cf)
	a.head.CFrame = cf * headLocal
end

local function spawnHerd(h: HerdState)
	local herd = h.herd
	local rng = Rng.new(herd.seed)
	local folder = Instance.new("Folder")
	folder.Name = "Herd"
	local animals: { Animal } = {}
	-- Herds cluster: animals start near a shared centre.
	local cs, cd = rng:range(herd.s0, herd.s1), rng:range(herd.d0, herd.d1)
	for i = 1, herd.count do
		local arng = Rng.new(Rng.hash(herd.seed, i))
		local model, head, up, down
		if herd.kind == "cow" then
			model, head, up, down = cow(herd.breed, arng)
		else
			model, head, up, down = sheep(arng)
		end
		local a: Animal = {
			model = model,
			head = head,
			headUp = up,
			headDown = down,
			s = math.clamp(cs + arng:range(-30, 30), herd.s0, herd.s1),
			d = math.clamp(cd + arng:range(-30, 30), herd.d0, herd.d1),
			yaw = arng:range(0, math.pi * 2),
			ts = 0,
			td = 0,
			state = if arng:chance(0.7) then "graze" else "rest",
			timer = arng:range(2, 12),
			speed = if herd.kind == "cow" then 1.6 else 2.2,
			rng = arng,
		}
		pose(a, herd)
		model.Parent = folder
		table.insert(animals, a)
	end
	folder.Parent = root
	h.folder = folder
	h.animals = animals
	active += #animals
end

local function despawnHerd(h: HerdState)
	if h.folder then
		h.folder:Destroy()
	end
	if h.animals then
		active -= #h.animals
	end
	h.folder = nil
	h.animals = nil
end

local function step(a: Animal, herd: Layout.Herd, dt: number)
	a.timer -= dt
	if a.state == "walk" then
		local ds, dd = a.ts - a.s, a.td - a.d
		local dist = math.sqrt(ds * ds + dd * dd)
		if dist < 0.5 or a.timer <= 0 then
			a.state = if a.rng:chance(0.8) then "graze" else "rest"
			a.timer = a.rng:range(4, 16)
		else
			local wantYaw = math.atan2(-ds, -(dd * Layout.sideSign(herd.side)))
			local diff = (wantYaw - a.yaw + math.pi) % (math.pi * 2) - math.pi
			a.yaw += math.clamp(diff, -dt * 1.2, dt * 1.2)
			local move = math.min(dist, a.speed * dt)
			a.s += ds / dist * move
			a.d += dd / dist * move
		end
	elseif a.timer <= 0 then
		a.state = "walk"
		a.timer = 20
		pickTarget(a, herd)
	end
	pose(a, herd)
end

function Animals.active(): number
	return active
end

function Animals.start(layout: Layout.Layout, smp: Sampler.Sampler)
	sampler = smp
	root = Instance.new("Folder")
	root.Name = "GeoBloxAnimals"
	root.Parent = workspace
	for _, herd in layout.herds do
		local s, d = (herd.s0 + herd.s1) / 2, (herd.d0 + herd.d1) / 2
		local x, z = Layout.toWorld(s, herd.side, d)
		local radius = math.max(herd.s1 - herd.s0, herd.d1 - herd.d0) / 2
		table.insert(
			herds,
			{ herd = herd, center = V3(x, Config.FIELD_Y, z), radius = radius, animals = nil, folder = nil }
		)
	end

	local scan = 0
	RunService.Heartbeat:Connect(function(dt)
		local camera = workspace.CurrentCamera
		if not camera then
			return
		end
		local focus = camera.CFrame.Position
		scan -= dt
		if scan <= 0 then
			scan = 0.5
			for _, h in herds do
				local dist = (V3(h.center.X, focus.Y, h.center.Z) - focus).Magnitude - h.radius
				if not h.animals and dist < Config.ANIMAL_RADIUS then
					spawnHerd(h)
				elseif h.animals and dist > Config.ANIMAL_RADIUS + 150 then
					despawnHerd(h)
				end
			end
		end
		for _, h in herds do
			local animals = h.animals
			if animals then
				for _, a in animals do
					step(a, h.herd, dt)
				end
			end
		end
	end)
end

return Animals
