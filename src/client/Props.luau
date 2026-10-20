--!strict
-- Parametric London street furniture, sized from real dimensions (metres ->
-- studs). Records come from OSM (lamps, benches, K6 boxes, pillar boxes, bus
-- stops, cycle hire, memorials...) or from the pipeline (lamps where OSM has
-- none, signal heads and Belisha beacons at mapped crossings).

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared")
local ChunkFormat = require(Shared:WaitForChild("ChunkFormat"))

local Props = {}

local S = ChunkFormat.STUDS_PER_METRE
local M = Enum.Material
local rgb = Color3.fromRGB
local V3 = Vector3.new

local BLACK = rgb(24, 24, 26)
local IRON = rgb(32, 34, 34)
local PO_RED = rgb(176, 18, 22)
local K6_RED = rgb(186, 24, 26)
local GALV = rgb(160, 162, 160)
local GOLD = rgb(190, 152, 70)

Props.lamps = {} :: { BasePart } -- lantern parts (for night lights)
Props.beacons = {} :: { BasePart } -- Belisha globes (blink)
Props.signals = {} :: { { BasePart } } -- {red, amber, green}

local function part(
	parent: Instance,
	size: Vector3,
	cf: CFrame,
	color: Color3,
	material: Enum.Material?,
	shape: Enum.PartType?
): Part
	local p = Instance.new("Part")
	p.Anchored = true
	p.CanTouch = false
	p.CanQuery = false
	p.CanCollide = false
	if shape then
		p.Shape = shape
	end
	p.Size = size
	p.CFrame = cf
	p.Color = color
	p.Material = material or M.SmoothPlastic
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	p.Parent = parent
	return p
end

-- Vertical cylinder: height h, diameter d, base centre at cf.
local function cyl(parent: Instance, h: number, d: number, cf: CFrame, color: Color3, material: Enum.Material?): Part
	return part(
		parent,
		V3(h, d, d),
		cf * CFrame.new(0, h / 2, 0) * CFrame.Angles(0, 0, math.pi / 2),
		color,
		material,
		Enum.PartType.Cylinder
	)
end

local function ball(parent: Instance, d: number, cf: CFrame, color: Color3, material: Enum.Material?): Part
	return part(parent, V3(d, d, d), cf, color, material, Enum.PartType.Ball)
end

local builders: { [number]: (Model, CFrame, number) -> () } = {}

-- 0: Westminster lamp column (black cast iron, lantern) / 1-variant: modern column.
builders[0] = function(m, cf, variant)
	if variant == 1 then
		cyl(m, 6.0 * S, 0.16 * S, cf, rgb(70, 72, 72), M.Metal)
		part(m, V3(0.12 * S, 0.12 * S, 1.2 * S), cf * CFrame.new(0, 6.0 * S, -0.55 * S), rgb(70, 72, 72), M.Metal)
		local head =
			part(m, V3(0.35 * S, 0.12 * S, 0.7 * S), cf * CFrame.new(0, 5.95 * S, -1.1 * S), rgb(230, 228, 220), M.Neon)
		head.Transparency = 0.6
		table.insert(Props.lamps, head)
		return
	end
	cyl(m, 0.5 * S, 0.34 * S, cf, BLACK, M.Metal)
	cyl(m, 4.2 * S, 0.15 * S, cf * CFrame.new(0, 0.5 * S, 0), BLACK, M.Metal)
	part(m, V3(0.5 * S, 0.06 * S, 0.5 * S), cf * CFrame.new(0, 4.6 * S, 0), BLACK, M.Metal)
	local glass = part(m, V3(0.42 * S, 0.62 * S, 0.42 * S), cf * CFrame.new(0, 4.95 * S, 0), rgb(255, 236, 196), M.Neon)
	glass.Transparency = 0.55
	table.insert(Props.lamps, glass)
	part(m, V3(0.56 * S, 0.1 * S, 0.56 * S), cf * CFrame.new(0, 5.3 * S, 0), BLACK, M.Metal)
	cyl(m, 0.25 * S, 0.12 * S, cf * CFrame.new(0, 5.35 * S, 0), GOLD, M.Metal)
end

builders[1] = function(m, cf, _)
	local w = 1.8 * S
	for _, x in { -0.8, 0.8 } do
		part(m, V3(0.06 * S, 0.45 * S, 0.5 * S), cf * CFrame.new(x * S, 0.22 * S, 0), IRON, M.Metal)
		part(m, V3(0.06 * S, 0.5 * S, 0.06 * S), cf * CFrame.new(x * S, 0.7 * S, 0.24 * S), IRON, M.Metal)
	end
	for k = 0, 3 do
		part(
			m,
			V3(w, 0.04 * S, 0.09 * S),
			cf * CFrame.new(0, 0.45 * S, (-0.18 + k * 0.12) * S),
			rgb(112, 80, 54),
			M.Wood
		)
	end
	for k = 0, 2 do
		part(m, V3(w, 0.09 * S, 0.04 * S), cf * CFrame.new(0, (0.6 + k * 0.13) * S, 0.25 * S), rgb(112, 80, 54), M.Wood)
	end
end

builders[2] = function(m, cf, _)
	cyl(m, 1.0 * S, 0.5 * S, cf, BLACK, M.Metal)
	cyl(m, 0.08 * S, 0.52 * S, cf * CFrame.new(0, 0.82 * S, 0), GOLD, M.Metal)
end

builders[3] = function(m, cf, _)
	cyl(m, 0.95 * S, 0.17 * S, cf, BLACK, M.Metal)
	cyl(m, 0.06 * S, 0.21 * S, cf * CFrame.new(0, 0.75 * S, 0), BLACK, M.Metal)
	ball(m, 0.17 * S, cf * CFrame.new(0, 0.95 * S, 0), BLACK, M.Metal)
end

-- 4: Pillar box (red, black base, domed cap).
builders[4] = function(m, cf, _)
	cyl(m, 0.12 * S, 0.62 * S, cf, BLACK, M.Metal)
	cyl(m, 1.35 * S, 0.55 * S, cf * CFrame.new(0, 0.12 * S, 0), PO_RED, M.Metal)
	cyl(m, 0.08 * S, 0.62 * S, cf * CFrame.new(0, 1.42 * S, 0), PO_RED, M.Metal)
	ball(m, 0.56 * S, cf * CFrame.new(0, 1.52 * S, 0), PO_RED, M.Metal)
	part(m, V3(0.3 * S, 0.05 * S, 0.06 * S), cf * CFrame.new(0, 1.15 * S, -0.27 * S), BLACK)
end

-- 5: K6 telephone kiosk (Giles Gilbert Scott, 1935).
builders[5] = function(m, cf, _)
	local w, h = 0.91 * S, 2.5 * S
	part(m, V3(w, h, w), cf * CFrame.new(0, h / 2, 0), K6_RED, M.Metal)
	for _, face in { 0, 1, 2, 3 } do
		local r = cf * CFrame.Angles(0, face * math.pi / 2, 0)
		if face ~= 3 then
			for row = 0, 7 do
				for col = 0, 2 do
					part(
						m,
						V3(0.2 * S, 0.17 * S, 0.02 * S),
						r * CFrame.new((col - 1) * 0.22 * S, (0.55 + row * 0.205) * S, -w / 2 - 0.01),
						rgb(40, 50, 58),
						M.Glass
					)
				end
			end
		end
		local sign = part(
			m,
			V3(0.62 * S, 0.12 * S, 0.02 * S),
			r * CFrame.new(0, 2.25 * S, -w / 2 - 0.02),
			rgb(240, 236, 220),
			M.Neon
		)
		sign.Transparency = 0.2
	end
	part(m, V3(w * 1.06, 0.12 * S, w * 1.06), cf * CFrame.new(0, h + 0.06 * S, 0), K6_RED, M.Metal)
	local dome = part(
		m,
		V3(0.5 * S, w * 1.0, w * 1.0),
		cf * CFrame.new(0, h + 0.12 * S, 0) * CFrame.Angles(0, 0, math.pi / 2),
		K6_RED,
		M.Metal,
		Enum.PartType.Cylinder
	)
	dome.Size = V3(0.3 * S, w, w)
end

-- 6: Bus stop flag.
builders[6] = function(m, cf, _)
	cyl(m, 2.9 * S, 0.1 * S, cf, rgb(40, 40, 42), M.Metal)
	part(
		m,
		V3(0.5 * S, 0.5 * S, 0.04 * S),
		cf * CFrame.new(0.28 * S, 2.6 * S, 0),
		rgb(220, 30, 34),
		M.SmoothPlastic,
		Enum.PartType.Cylinder
	).CFrame = cf
		* CFrame.new(0.3 * S, 2.6 * S, 0)
		* CFrame.Angles(0, math.pi / 2, 0)
	part(m, V3(0.56 * S, 0.1 * S, 0.05 * S), cf * CFrame.new(0.3 * S, 2.6 * S, 0), rgb(18, 40, 120))
	part(m, V3(0.3 * S, 0.5 * S, 0.06 * S), cf * CFrame.new(0, 1.5 * S, 0.06 * S), rgb(236, 236, 230))
end

-- 7: Bus shelter.
builders[7] = function(m, cf, v)
	builders[6](m, cf * CFrame.new(-2.2 * S, 0, 0), v)
	local w, d, h = 3.6 * S, 1.3 * S, 2.5 * S
	part(m, V3(w, 0.06 * S, d), cf * CFrame.new(0, h, 0.2 * S), rgb(60, 64, 66), M.Metal)
	local back = part(m, V3(w, h, 0.03 * S), cf * CFrame.new(0, h / 2, 0.8 * S), rgb(170, 190, 200), M.Glass)
	back.Transparency = 0.5
	for _, x in { -w / 2, w / 2 } do
		part(m, V3(0.06 * S, h, 0.06 * S), cf * CFrame.new(x, h / 2, 0.8 * S), rgb(60, 64, 66), M.Metal)
	end
	part(m, V3(w * 0.6, 0.06 * S, 0.3 * S), cf * CFrame.new(0, 0.5 * S, 0.62 * S), rgb(60, 64, 66), M.Metal)
	local ad = part(
		m,
		V3(1.2 * S, 1.8 * S, 0.1 * S),
		cf * CFrame.new(w / 2 - 0.6 * S, 1.2 * S, 0.8 * S),
		rgb(240, 238, 230),
		M.Neon
	)
	ad.Transparency = 0.35
end

-- 8: Traffic signal (three-aspect head with backboard + pedestrian head).
builders[8] = function(m, cf, _)
	cyl(m, 3.1 * S, 0.13 * S, cf, BLACK, M.Metal)
	local head = cf * CFrame.new(0, 2.75 * S, -0.12 * S)
	part(m, V3(0.42 * S, 1.05 * S, 0.04 * S), head * CFrame.new(0, 0, 0.1 * S), rgb(250, 250, 250))
	part(m, V3(0.3 * S, 0.9 * S, 0.22 * S), head, BLACK, M.Metal)
	local lights: { BasePart } = {}
	for i, c in { rgb(255, 40, 30), rgb(255, 160, 20), rgb(40, 255, 110) } do
		local l = part(
			m,
			V3(0.18 * S, 0.18 * S, 0.04 * S),
			head * CFrame.new(0, (1 - (i - 1)) * 0.27 * S, -0.12 * S),
			c,
			M.Neon,
			Enum.PartType.Cylinder
		)
		l.CFrame = head * CFrame.new(0, (1 - (i - 1)) * 0.27 * S, -0.12 * S) * CFrame.Angles(0, math.pi / 2, 0)
		l.Size = V3(0.04 * S, 0.18 * S, 0.18 * S)
		table.insert(lights, l :: BasePart)
	end
	table.insert(Props.signals, lights)
	part(m, V3(0.28 * S, 0.5 * S, 0.18 * S), cf * CFrame.new(0, 2.0 * S, 0.14 * S), BLACK, M.Metal)
end

-- 9: Belisha beacon (black/white pole, flashing amber globe).
builders[9] = function(m, cf, _)
	for k = 0, 8 do
		cyl(
			m,
			0.3 * S,
			0.12 * S,
			cf * CFrame.new(0, k * 0.3 * S, 0),
			if k % 2 == 0 then BLACK else rgb(240, 240, 236),
			M.SmoothPlastic
		)
	end
	local globe = ball(m, 0.36 * S, cf * CFrame.new(0, 2.9 * S, 0), rgb(255, 170, 30), M.Neon)
	table.insert(Props.beacons, globe)
end

-- 10: Sheffield cycle stand.
builders[10] = function(m, cf, _)
	for _, x in { -0.35, 0.35 } do
		part(m, V3(0.06 * S, 0.8 * S, 0.06 * S), cf * CFrame.new(x * S, 0.4 * S, 0), GALV, M.Metal)
	end
	part(m, V3(0.76 * S, 0.06 * S, 0.06 * S), cf * CFrame.new(0, 0.8 * S, 0), GALV, M.Metal)
end

-- 11: Cycle-hire docking station with red bikes.
builders[11] = function(m, cf, _)
	part(m, V3(0.4 * S, 2.0 * S, 0.3 * S), cf * CFrame.new(-2.6 * S, 1.0 * S, 0), rgb(70, 72, 76), M.Metal)
	for k = 0, 5 do
		local x = (-1.8 + k * 0.75) * S
		part(m, V3(0.12 * S, 0.9 * S, 0.12 * S), cf * CFrame.new(x, 0.45 * S, 0), rgb(70, 72, 76), M.Metal)
		if k % 2 == 0 then
			local bike = cf * CFrame.new(x, 0, -0.9 * S) * CFrame.Angles(0, math.pi / 2, 0)
			for _, wx in { -0.5, 0.5 } do
				local wheel = part(
					m,
					V3(0.05 * S, 0.66 * S, 0.66 * S),
					bike * CFrame.new(wx * S, 0.33 * S, 0) * CFrame.Angles(0, math.pi / 2, 0),
					BLACK,
					M.Rubber,
					Enum.PartType.Cylinder
				)
				wheel.Size = V3(0.05 * S, 0.66 * S, 0.66 * S)
			end
			part(
				m,
				V3(0.95 * S, 0.08 * S, 0.06 * S),
				bike * CFrame.new(0, 0.62 * S, 0) * CFrame.Angles(0, 0, 0.2),
				rgb(200, 30, 30),
				M.Metal
			)
			part(
				m,
				V3(0.06 * S, 0.5 * S, 0.06 * S),
				bike * CFrame.new(-0.25 * S, 0.62 * S, 0),
				rgb(200, 30, 30),
				M.Metal
			)
		end
	end
end

-- 12: Memorial / statue on a granite plinth.
builders[12] = function(m, cf, variant)
	local granite = rgb(124, 122, 118)
	local bronze = rgb(64, 58, 44)
	part(m, V3(2.2 * S, 0.4 * S, 2.2 * S), cf * CFrame.new(0, 0.2 * S, 0), granite, M.Granite)
	part(m, V3(1.6 * S, 2.0 * S, 1.6 * S), cf * CFrame.new(0, 1.4 * S, 0), granite, M.Granite)
	part(m, V3(1.9 * S, 0.25 * S, 1.9 * S), cf * CFrame.new(0, 2.5 * S, 0), granite, M.Granite)
	local top = cf * CFrame.new(0, 2.62 * S, 0)
	local h = (2.3 + (variant % 3) * 0.3) * S
	part(m, V3(0.55 * S, h * 0.45, 0.4 * S), top * CFrame.new(0, h * 0.22, 0), bronze, M.Metal)
	part(m, V3(0.7 * S, h * 0.38, 0.45 * S), top * CFrame.new(0, h * 0.62, 0), bronze, M.Metal)
	ball(m, 0.32 * S, top * CFrame.new(0, h * 0.88, 0), bronze, M.Metal)
	part(
		m,
		V3(0.18 * S, h * 0.32, 0.18 * S),
		top * CFrame.new(0.42 * S, h * 0.6, 0) * CFrame.Angles(0, 0, 0.25),
		bronze,
		M.Metal
	)
end

-- 13: Flagpole with a Union flag.
builders[13] = function(m, cf, _)
	cyl(m, 9 * S, 0.14 * S, cf, rgb(240, 240, 236), M.SmoothPlastic)
	local f = cf * CFrame.new(1.1 * S, 8.2 * S, 0)
	part(m, V3(2.0 * S, 1.0 * S, 0.02 * S), f, rgb(18, 34, 110), M.Fabric)
	part(m, V3(2.0 * S, 0.3 * S, 0.03 * S), f, rgb(240, 240, 240), M.Fabric)
	part(m, V3(0.36 * S, 1.0 * S, 0.03 * S), f, rgb(240, 240, 240), M.Fabric)
	part(m, V3(2.0 * S, 0.16 * S, 0.04 * S), f, rgb(200, 20, 40), M.Fabric)
	part(m, V3(0.2 * S, 1.0 * S, 0.04 * S), f, rgb(200, 20, 40), M.Fabric)
end

-- 14: "Legible London" wayfinding monolith.
builders[14] = function(m, cf, _)
	part(m, V3(0.65 * S, 2.4 * S, 0.16 * S), cf * CFrame.new(0, 1.2 * S, 0), rgb(46, 50, 56), M.Metal)
	part(m, V3(0.66 * S, 0.22 * S, 0.17 * S), cf * CFrame.new(0, 2.2 * S, 0), rgb(240, 200, 40), M.SmoothPlastic)
	local map =
		part(m, V3(0.5 * S, 1.2 * S, 0.02 * S), cf * CFrame.new(0, 1.3 * S, -0.09 * S), rgb(236, 236, 230), M.Neon)
	map.Transparency = 0.4
end

-- 15: Modern phone kiosk.
builders[15] = function(m, cf, _)
	part(m, V3(0.9 * S, 2.3 * S, 0.5 * S), cf * CFrame.new(0, 1.15 * S, 0), rgb(54, 58, 64), M.Metal)
	local screen =
		part(m, V3(0.7 * S, 1.2 * S, 0.02 * S), cf * CFrame.new(0, 1.3 * S, -0.26 * S), rgb(200, 220, 236), M.Neon)
	screen.Transparency = 0.45
end

builders[16] = builders[0]

-- Build one prop. `origin` is the chunk anchor (studs).
function Props.build(rec: ChunkFormat.PropRec, origin: Vector3, parent: Instance): Model?
	local builder = builders[rec.kind]
	if not builder then
		return nil
	end
	local m = Instance.new("Model")
	m.Name = "Prop" .. rec.kind
	-- Pipeline yaw: clockwise from north of the object's front; Roblox front is -Z.
	local cf = CFrame.new(origin + Vector3.new(rec.x, rec.y, rec.z)) * CFrame.Angles(0, -rec.yaw, 0)
	builder(m, cf, rec.variant)
	m.Parent = parent
	return m
end

function Props.forget(model: Instance)
	-- Drop animation references to parts of a removed model.
	for _, list in { Props.lamps, Props.beacons } do
		for i = #list, 1, -1 do
			if list[i]:IsDescendantOf(model) or list[i].Parent == nil then
				table.remove(list, i)
			end
		end
	end
	for i = #Props.signals, 1, -1 do
		local s = Props.signals[i]
		if s[1]:IsDescendantOf(model) or s[1].Parent == nil then
			table.remove(Props.signals, i)
		end
	end
end

return Props
