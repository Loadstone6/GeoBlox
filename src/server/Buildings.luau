--!strict
-- Procedural Dutch farm buildings, the polder mill and the canal bridge, built
-- from parts and triangle-wedges. Each building kind has a regional character:
-- hallehuis, langgevel, stolp (pyramid roof), post-war farmhouse, tarred-wood
-- barn, steel shed, feed silo and manure silo.

local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Geometry = require(Shared.Geometry)
local Layout = require(Shared.Layout)
local Rng = require(Shared.Rng)
local Sampler = require(Shared.Sampler)

local M = Enum.Material
local rgb = Color3.fromRGB
local V3 = Vector3.new

local Buildings = {}

type Shell = {
	w: number, -- local X
	dep: number, -- local Z (front = +Z)
	eave: number,
	ridge: number,
	hipFront: number,
	hipBack: number,
	overhang: number,
	wallColor: Color3,
	wallMat: Enum.Material,
	roofColor: Color3,
	roofMat: Enum.Material,
	plinthColor: Color3?,
	plinthMat: Enum.Material?,
	plinthH: number?,
}

local WHITE = rgb(236, 234, 228)
local GLASS = rgb(28, 34, 42)
local DUTCH_GREEN = rgb(30, 74, 50)

local function darker(c: Color3, f: number): Color3
	return Color3.new(c.R * f, c.G * f, c.B * f)
end

-- Walls block + roof planes. `cf` sits at the footprint centre on the ground.
local function shell(parent: Instance, cf: CFrame, o: Shell)
	local sink = 3
	local w, dep = o.w, o.dep
	Geometry.block(
		parent,
		V3(w, o.eave + sink, dep),
		cf * CFrame.new(0, (o.eave - sink) / 2, 0),
		o.wallColor,
		o.wallMat
	)
	if o.plinthColor then
		local ph = o.plinthH or 1.8
		Geometry.block(
			parent,
			V3(w + 0.5, ph + sink, dep + 0.5),
			cf * CFrame.new(0, (ph - sink) / 2, 0),
			o.plinthColor,
			o.plinthMat or M.Brick
		)
	end

	local ov = o.overhang
	local pitch = (o.ridge - o.eave) / (w / 2)
	local ey = o.eave - ov * pitch
	local hx, hz = w / 2 + ov, dep / 2 + ov
	local zf = if o.hipFront > 0 then dep / 2 - o.hipFront else hz
	local zb = if o.hipBack > 0 then -(dep / 2 - o.hipBack) else -hz
	local function P(x: number, y: number, z: number): Vector3
		return cf:PointToWorldSpace(V3(x, y, z))
	end
	local up, rx, fz = cf.UpVector, cf.RightVector, -cf.LookVector
	local t = 0.6

	Geometry.quad(
		parent,
		P(hx, ey, -hz),
		P(hx, ey, hz),
		P(0, o.ridge, zf),
		P(0, o.ridge, zb),
		t,
		o.roofColor,
		o.roofMat,
		rx + up
	)
	Geometry.quad(
		parent,
		P(-hx, ey, hz),
		P(-hx, ey, -hz),
		P(0, o.ridge, zb),
		P(0, o.ridge, zf),
		t,
		o.roofColor,
		o.roofMat,
		-rx + up
	)
	if o.hipFront > 0 then
		Geometry.triangle(parent, P(hx, ey, hz), P(-hx, ey, hz), P(0, o.ridge, zf), t, o.roofColor, o.roofMat, fz + up)
	else
		Geometry.triangle(
			parent,
			P(w / 2, o.eave, dep / 2),
			P(-w / 2, o.eave, dep / 2),
			P(0, o.ridge, dep / 2),
			0.8,
			o.wallColor,
			o.wallMat,
			-fz
		)
	end
	if o.hipBack > 0 then
		Geometry.triangle(
			parent,
			P(-hx, ey, -hz),
			P(hx, ey, -hz),
			P(0, o.ridge, zb),
			t,
			o.roofColor,
			o.roofMat,
			-fz + up
		)
	else
		Geometry.triangle(
			parent,
			P(-w / 2, o.eave, -dep / 2),
			P(w / 2, o.eave, -dep / 2),
			P(0, o.ridge, -dep / 2),
			0.8,
			o.wallColor,
			o.wallMat,
			fz
		)
	end
	if zf - zb > 1 then
		Geometry.block(
			parent,
			V3(1.1, 0.8, zf - zb + 0.6),
			cf * CFrame.new(0, o.ridge + 0.25, (zf + zb) / 2),
			darker(o.roofColor, 0.75),
			o.roofMat
		)
	end
	-- Gutters along the eaves.
	for _, sx in { 1, -1 } do
		Geometry.block(
			parent,
			V3(0.6, 0.6, dep + ov * 2),
			cf * CFrame.new(sx * (hx - 0.1), ey - 0.2, 0),
			rgb(70, 72, 70),
			M.Metal
		)
	end
end

-- Wall frame: position at the wall centre at ground level, LookVector outward.
local function wallFrame(cf: CFrame, face: string, o: Shell): (CFrame, number)
	local offset, out, length
	if face == "front" then
		offset, out, length = V3(0, 0, o.dep / 2), -cf.LookVector, o.w
	elseif face == "back" then
		offset, out, length = V3(0, 0, -o.dep / 2), cf.LookVector, o.w
	elseif face == "right" then
		offset, out, length = V3(o.w / 2, 0, 0), cf.RightVector, o.dep
	else
		offset, out, length = V3(-o.w / 2, 0, 0), -cf.RightVector, o.dep
	end
	local p = cf:PointToWorldSpace(offset)
	return CFrame.lookAt(p, p + out), length
end

type WindowStyle = { w: number, h: number, frame: Color3, shutters: Color3? }

local function window(parent: Instance, at: CFrame, s: WindowStyle)
	Geometry.block(parent, V3(s.w + 0.8, s.h + 0.8, 0.3), at * CFrame.new(0, 0, -0.15), s.frame)
	local glass = Geometry.block(parent, V3(s.w, s.h, 0.3), at * CFrame.new(0, 0, -0.22), GLASS, M.Glass)
	glass.Reflectance = 0.25
	glass.CastShadow = false
	Geometry.block(parent, V3(0.25, s.h, 0.3), at * CFrame.new(0, 0, -0.35), s.frame)
	Geometry.block(parent, V3(s.w, 0.25, 0.3), at * CFrame.new(0, s.h * 0.18, -0.35), s.frame)
	if s.shutters then
		for _, sx in { -1, 1 } do
			Geometry.block(
				parent,
				V3(s.w / 2 + 0.1, s.h + 0.6, 0.25),
				at * CFrame.new(sx * (s.w * 0.75 + 0.55), 0, -0.2),
				s.shutters
			)
		end
	end
end

local function windowRow(
	parent: Instance,
	wall: CFrame,
	x0: number,
	x1: number,
	count: number,
	y: number,
	s: WindowStyle
)
	for i = 1, count do
		local x = x0 + (x1 - x0) * (i - 0.5) / count
		window(parent, wall * CFrame.new(x, y, 0), s)
	end
end

local function door(parent: Instance, wall: CFrame, x: number, w: number, h: number, color: Color3)
	Geometry.block(parent, V3(w + 0.8, h + 0.4, 0.3), wall * CFrame.new(x, h / 2, -0.15), WHITE)
	Geometry.block(parent, V3(w, h, 0.35), wall * CFrame.new(x, h / 2 - 0.2, -0.25), color, M.Wood)
end

local function chimney(parent: Instance, cf: CFrame, x: number, y: number, z: number)
	Geometry.block(parent, V3(2.4, 7, 2.4), cf * CFrame.new(x, y, z), rgb(120, 58, 44), M.Brick)
	Geometry.block(parent, V3(3, 0.6, 3), cf * CFrame.new(x, y + 3.6, z), rgb(60, 60, 60), M.Concrete)
end

local function brickColor(rng: Rng.Rng): Color3
	local options = { rgb(138, 64, 48), rgb(122, 58, 44), rgb(150, 80, 58), rgb(112, 52, 42) }
	return options[rng:int(1, #options)]
end

local THATCH = rgb(108, 94, 66)
local THATCH_MAT = M.Grass

local builders: { [string]: (Instance, CFrame, number, number, Rng.Rng) -> () } = {}

builders.hallehuis = function(parent, cf, w, dep, rng)
	local thatch = rng:chance(0.45)
	local o: Shell = {
		w = w,
		dep = dep,
		eave = 9,
		ridge = 34,
		hipFront = 0,
		hipBack = 16,
		overhang = 1.6,
		wallColor = brickColor(rng),
		wallMat = M.Brick,
		roofColor = if thatch then THATCH else (if rng:chance(0.5) then rgb(66, 66, 70) else rgb(170, 82, 50)),
		roofMat = if thatch then THATCH_MAT else M.ClayRoofTiles,
		plinthColor = rgb(58, 40, 34),
	}
	shell(parent, cf, o)
	local style: WindowStyle = { w = 3.2, h = 4.4, frame = WHITE, shutters = DUTCH_GREEN }
	local front = wallFrame(cf, "front", o)
	windowRow(parent, front, -w / 2 + 2, -4, 2, 4.6, style)
	windowRow(parent, front, 4, w / 2 - 2, 2, 4.6, style)
	door(parent, front, 0, 3.6, 7.2, DUTCH_GREEN)
	window(parent, front * CFrame.new(0, 15, 0), { w = 2.6, h = 3.2, frame = WHITE })
	-- Owl board at the gable top.
	local function P(x: number, y: number, z: number): Vector3
		return cf:PointToWorldSpace(V3(x, y, z))
	end
	Geometry.triangle(
		parent,
		P(-2.4, o.ridge - 4, dep / 2 + 1.9),
		P(2.4, o.ridge - 4, dep / 2 + 1.9),
		P(0, o.ridge + 0.6, dep / 2 + 1.9),
		0.4,
		WHITE,
		M.Wood
	)

	-- Living quarters at the front, stable behind.
	for _, face in { "right", "left" } do
		local wall, len = wallFrame(cf, face, o)
		local sign = if face == "right" then 1 else -1
		windowRow(parent, wall, sign * (len / 2 - 26), sign * (len / 2 - 2), 3, 4.6, style)
		windowRow(parent, wall, sign * (-len / 2 + 4), sign * (len / 2 - 32), 6, 6, { w = 2.2, h = 1.6, frame = WHITE })
	end
	local back = wallFrame(cf, "back", o)
	door(parent, back, 0, 12, 8.4, rgb(70, 44, 34))
	chimney(parent, cf, 0, o.ridge - 1, dep / 2 - 9)
end

builders.langgevel = function(parent, cf, w, dep, rng)
	local plaster = rng:chance(0.5)
	local thatch = rng:chance(0.5)
	local o: Shell = {
		w = w,
		dep = dep,
		eave = 8,
		ridge = 25,
		hipFront = 7,
		hipBack = 7,
		overhang = 1.6,
		wallColor = if plaster then rgb(232, 228, 216) else brickColor(rng),
		wallMat = if plaster then M.Plaster else M.Brick,
		roofColor = if thatch then THATCH else rgb(174, 84, 52),
		roofMat = if thatch then THATCH_MAT else M.ClayRoofTiles,
		plinthColor = rgb(40, 40, 42),
		plinthMat = M.Concrete,
		plinthH = 1.2,
	}
	shell(parent, cf, o)
	local shutters = if rng:chance(0.5) then DUTCH_GREEN else rgb(150, 30, 34)
	local style: WindowStyle = { w = 3, h = 4, frame = WHITE, shutters = shutters }
	local front, len = wallFrame(cf, "right", o)
	-- House half (towards +Z) with door, barn half with big doors.
	windowRow(parent, front, -len / 2 + 3, -6, 4, 4.4, style)
	door(parent, front, -2, 3.4, 6.8, shutters)
	door(parent, front, len / 4, 10, 7.4, rgb(64, 46, 36))
	windowRow(parent, front, len / 4 + 8, len / 2 - 3, 2, 5.4, { w = 2.2, h = 1.6, frame = WHITE })
	chimney(parent, cf, 0, o.ridge - 1, -dep / 2 + 18)
end

builders.stolp = function(parent, cf, w, dep, rng)
	local o: Shell = {
		w = w,
		dep = dep,
		eave = 7,
		ridge = 42,
		hipFront = dep / 2,
		hipBack = dep / 2,
		overhang = 1.8,
		wallColor = brickColor(rng),
		wallMat = M.Brick,
		roofColor = THATCH,
		roofMat = THATCH_MAT,
		plinthColor = rgb(58, 40, 34),
	}
	shell(parent, cf, o)
	-- Decorative brick front ("pronkgevel") rising through the roof edge.
	local pw, ph = 24, 18
	local front = wallFrame(cf, "front", o)
	local frontPos = front * CFrame.new(0, 0, -1.2)
	Geometry.block(parent, V3(pw, ph + 3, 2.4), frontPos * CFrame.new(0, (ph - 3) / 2, 0), o.wallColor, M.Brick)
	local function F(x: number, y: number): Vector3
		return (frontPos * CFrame.new(x, y, 0)).Position
	end
	Geometry.triangle(parent, F(-pw / 2, ph), F(pw / 2, ph), F(0, ph + 7), 2.4, o.wallColor, M.Brick)
	local style: WindowStyle = { w = 3, h = 4.4, frame = WHITE, shutters = DUTCH_GREEN }
	windowRow(parent, frontPos * CFrame.new(0, 0, -1.2), -pw / 2 + 1, -3.5, 2, 4.6, style)
	windowRow(parent, frontPos * CFrame.new(0, 0, -1.2), 3.5, pw / 2 - 1, 2, 4.6, style)
	door(parent, frontPos * CFrame.new(0, 0, -1.2), 0, 3.4, 6.6, DUTCH_GREEN)
	window(parent, frontPos * CFrame.new(0, 13, -1.2), { w = 2.6, h = 3.4, frame = WHITE })
	for _, face in { "right", "left", "back" } do
		local wall, len = wallFrame(cf, face, o)
		windowRow(parent, wall, -len / 2 + 6, len / 2 - 6, 4, 4, { w = 2.4, h = 2.2, frame = WHITE })
	end
	chimney(parent, cf, 0, o.ridge - 2.5, 0)
end

builders.modern = function(parent, cf, w, dep, rng)
	local o: Shell = {
		w = w,
		dep = dep,
		eave = 7.5,
		ridge = 20,
		hipFront = 0,
		hipBack = 0,
		overhang = 1.4,
		wallColor = if rng:chance(0.6) then rgb(190, 152, 96) else rgb(150, 92, 66),
		wallMat = M.Brick,
		roofColor = rgb(56, 56, 60),
		roofMat = M.ClayRoofTiles,
	}
	shell(parent, cf, o)
	local front, len = wallFrame(cf, "right", o)
	windowRow(parent, front, -len / 2 + 3, len / 2 - 10, 3, 4.2, { w = 5.6, h = 4.6, frame = WHITE })
	door(parent, front, len / 2 - 5, 3.4, 6.8, rgb(60, 60, 62))
	for _, face in { "front", "back" } do
		local wall = wallFrame(cf, face, o)
		window(parent, wall * CFrame.new(0, 12, 0), { w = 3, h = 3, frame = WHITE })
		windowRow(parent, wall, -w / 2 + 3, w / 2 - 3, 2, 4.2, { w = 4, h = 4.4, frame = WHITE })
	end
	chimney(parent, cf, 0, o.ridge - 1, dep / 2 - 6)
end

builders.oldbarn = function(parent, cf, w, dep, rng)
	local o: Shell = {
		w = w,
		dep = dep,
		eave = 8,
		ridge = 30,
		hipFront = 9,
		hipBack = 9,
		overhang = 1.4,
		wallColor = rgb(40, 38, 36),
		wallMat = M.WoodPlanks,
		roofColor = if rng:chance(0.5) then rgb(170, 82, 50) else rgb(64, 64, 68),
		roofMat = M.ClayRoofTiles,
		plinthColor = rgb(128, 60, 46),
		plinthH = 4,
	}
	shell(parent, cf, o)
	local wall = wallFrame(cf, "front", o)
	door(parent, wall, 0, math.min(12, w - 6), 7.6, rgb(54, 40, 32))
	for _, face in { "right", "left" } do
		local side, len = wallFrame(cf, face, o)
		windowRow(parent, side, -len / 2 + 4, len / 2 - 4, 3, 5.5, { w = 2.2, h = 1.6, frame = WHITE })
	end
end

builders.steelshed = function(parent, cf, w, dep, rng)
	local panel = if rng:chance(0.6) then rgb(96, 110, 98) else rgb(120, 116, 104)
	local o: Shell = {
		w = w,
		dep = dep,
		eave = 14,
		ridge = 22,
		hipFront = 0,
		hipBack = 0,
		overhang = 0.8,
		wallColor = panel,
		wallMat = M.Metal,
		roofColor = rgb(150, 150, 146),
		roofMat = M.Slate,
		plinthColor = rgb(150, 150, 144),
		plinthMat = M.Concrete,
		plinthH = 4,
	}
	shell(parent, cf, o)
	-- Ventilation mesh band under the eaves.
	for _, face in { "right", "left" } do
		local side, len = wallFrame(cf, face, o)
		Geometry.block(
			parent,
			V3(len - 2, 3.4, 0.3),
			side * CFrame.new(0, o.eave - 2.6, -0.2),
			rgb(36, 54, 40),
			M.Fabric
		)
	end
	local front = wallFrame(cf, "front", o)
	Geometry.block(parent, V3(18, 13, 0.4), front * CFrame.new(0, 6.5, -0.25), rgb(80, 82, 84), M.Metal)
end

builders.silo = function(parent, cf, w, _dep, _rng)
	local p = cf.Position
	local metal = rgb(186, 190, 188)
	Geometry.cylinder(parent, 40, w, p + V3(0, 20 + 4, 0), metal, M.Metal)
	Geometry.frustum(parent, p + V3(0, 44, 0), w / 2 + 0.2, 0.6, 4, 12, metal, M.Metal)
	Geometry.block(parent, V3(w * 0.6, 4, w * 0.6), cf * CFrame.new(0, 2, 0), rgb(150, 150, 144), M.Concrete)
	for i = 0, 8 do
		Geometry.block(
			parent,
			V3(1.6, 0.25, 0.25),
			cf * CFrame.new(0, 6 + i * 4.4, -w / 2 - 0.6),
			rgb(90, 92, 92),
			M.Metal
		)
	end
end

builders.mestsilo = function(parent, cf, w, _dep, _rng)
	local p = cf.Position
	Geometry.cylinder(parent, 11, w, p + V3(0, 3.5, 0), rgb(146, 146, 138), M.Concrete)
	Geometry.frustum(parent, p + V3(0, 9, 0), w / 2 + 0.6, 0.8, 7, 16, rgb(58, 78, 58), M.Fabric)
	Geometry.cylinder(parent, 2, 2, p + V3(0, 16.5, 0), rgb(58, 78, 58), M.Fabric)
end

function Buildings.buildFarm(parent: Instance, farm: Layout.Farm, sampler: Sampler.Sampler)
	local model = Instance.new("Model")
	model.Name = string.format("Farm_%02d_%s", farm.id, farm.style)
	local sideSign = Layout.sideSign(farm.side)
	for _, b in farm.buildings do
		local x, z = Layout.toWorld(farm.s + b.ls, farm.side, b.d)
		local y = sampler:height(x, z)
		local pos = V3(x, y, z)
		local cf: CFrame
		local w, dep
		if b.axis == "d" then
			cf = CFrame.lookAt(pos, pos + V3(0, 0, sideSign)) -- local +Z faces the road
			w, dep = b.w, b.dep
		else
			cf = CFrame.lookAt(pos, pos + V3(-sideSign, 0, 0)) -- local +X faces the road
			w, dep = b.dep, b.w
		end
		local part = Instance.new("Model")
		part.Name = b.kind
		builders[b.kind](part, cf, w, dep, Rng.new(b.seed))
		part.Parent = model
	end
	model.Parent = parent
	return model
end

function Buildings.buildBridge(parent: Instance, layout: Layout.Layout)
	local model = Instance.new("Model")
	model.Name = "CanalBridge"
	local C = layout.canal
	local x = C.s
	local zc, slope = Layout.road(x)
	local yaw = math.atan(slope)
	local cf = CFrame.new(x, Config.ROAD_Y, zc) * CFrame.Angles(0, -yaw, 0)
	local span = (C.half + C.bank) * 2 + 6
	local width = Config.VERGE * 2
	Geometry.block(model, V3(span, 2.4, width), cf * CFrame.new(0, -1.2, 0), rgb(64, 64, 66), M.Asphalt)
	Geometry.block(model, V3(span, 1.4, width + 1), cf * CFrame.new(0, -3, 0), rgb(150, 150, 144), M.Concrete)
	for _, sz in { 1, -1 } do
		-- White steel railing on brick kerbs.
		Geometry.block(
			model,
			V3(span, 1.2, 1.6),
			cf * CFrame.new(0, 0.6, sz * (width / 2 - 0.8)),
			rgb(130, 66, 50),
			M.Brick
		)
		Geometry.block(model, V3(span, 0.5, 0.5), cf * CFrame.new(0, 4.4, sz * (width / 2 - 0.8)), WHITE, M.Metal)
		Geometry.block(model, V3(span, 0.35, 0.35), cf * CFrame.new(0, 2.8, sz * (width / 2 - 0.8)), WHITE, M.Metal)
		for i = 0, math.floor(span / 4) do
			Geometry.block(
				model,
				V3(0.4, 3.2, 0.4),
				cf * CFrame.new(-span / 2 + i * 4, 2.8, sz * (width / 2 - 0.8)),
				WHITE,
				M.Metal
			)
		end
	end
	-- Brick abutments down to the canal bed.
	for _, sx in { 1, -1 } do
		Geometry.block(
			model,
			V3(3, 18, width + 14),
			cf * CFrame.new(sx * (C.half + 0.5), -9, 0),
			rgb(124, 60, 46),
			M.Brick
		)
	end
	model.Parent = parent
end

function Buildings.buildWindmill(parent: Instance, layout: Layout.Layout, sampler: Sampler.Sampler)
	local W = layout.windmill
	local x, z = Layout.toWorld(W.s, W.side, W.d)
	local ground = sampler:height(x, z)
	local base = V3(x, ground, z)
	local model = Instance.new("Model")
	model.Name = "PolderMill"

	local brick = rgb(124, 58, 44)
	Geometry.frustum(model, base - V3(0, 2, 0), 16, 15.4, 7, 8, brick, M.Brick, 1.2, math.pi / 8)
	Geometry.cylinder(model, 8, 30, base + V3(0, 1, 0), brick, M.Brick)
	-- Thatched octagonal body.
	local bodyBase = base + V3(0, 5, 0)
	Geometry.frustum(model, bodyBase, 15.4, 9.2, 46, 8, THATCH, THATCH_MAT, 1, math.pi / 8)
	Geometry.cylinder(model, 46, 18, bodyBase + V3(0, 23, 0), darker(THATCH, 0.6), M.Wood)

	-- Facing: sails turned towards the road, slightly tilted up.
	local face = V3(-0.35, 0, -1).Unit
	local top = bodyBase + V3(0, 46, 0)
	local capCf = CFrame.lookAt(top + V3(0, 4, 0), top + V3(0, 4, 0) + face)
	-- Boat-shaped cap: a long wedge roof over a short drum.
	Geometry.block(model, V3(18, 3, 20), capCf * CFrame.new(0, -2, 0), rgb(60, 52, 44), M.Wood)
	local function C(px: number, py: number, pz: number): Vector3
		return capCf:PointToWorldSpace(V3(px, py, pz))
	end
	Geometry.quad(
		model,
		C(9.5, -0.5, -12),
		C(9.5, -0.5, 10),
		C(0, 7, 8),
		C(0, 7, -10),
		0.8,
		THATCH,
		THATCH_MAT,
		capCf.RightVector + capCf.UpVector
	)
	Geometry.quad(
		model,
		C(-9.5, -0.5, 10),
		C(-9.5, -0.5, -12),
		C(0, 7, -10),
		C(0, 7, 8),
		0.8,
		THATCH,
		THATCH_MAT,
		-capCf.RightVector + capCf.UpVector
	)
	Geometry.triangle(
		model,
		C(9.5, -0.5, -12),
		C(-9.5, -0.5, -12),
		C(0, 7, -10),
		0.8,
		THATCH,
		THATCH_MAT,
		-capCf.LookVector
	)
	Geometry.triangle(
		model,
		C(9.5, -0.5, 10),
		C(-9.5, -0.5, 10),
		C(0, 7, 8),
		0.8,
		rgb(52, 46, 40),
		M.WoodPlanks,
		capCf.LookVector
	)
	-- Tail pole down to the ground for turning the cap (the "staart").
	local tailTop = C(0, -1, 10)
	local tailBottom = base + face * -26 + V3(0, 3, 0)
	local mid = (tailTop + tailBottom) / 2
	Geometry.block(
		model,
		V3(1.2, 1.2, (tailTop - tailBottom).Magnitude),
		CFrame.lookAt(mid, tailTop),
		rgb(70, 52, 38),
		M.Wood
	)
	Geometry.block(
		model,
		V3(5, 0.8, 0.8),
		CFrame.lookAt(tailBottom, tailBottom + face) * CFrame.new(0, 0, 0),
		rgb(70, 52, 38),
		M.Wood
	)

	-- Door and windows on the body.
	local doorCf = CFrame.lookAt(base + face * 15.6 + V3(0, 4, 0), base + face * 30 + V3(0, 4, 0))
	Geometry.block(model, V3(4, 7, 0.6), doorCf, rgb(30, 74, 50), M.Wood)
	Geometry.block(model, V3(2.4, 3, 0.6), doorCf * CFrame.new(0, 17, 4.3), WHITE)

	-- Sails (rotated by the client around Hub/Axis).
	local axis = (face + V3(0, 0.16, 0)).Unit
	local hub = C(0, 0.5, -13)
	local sails = Instance.new("Model")
	sails.Name = "Sails"
	local hubCf = CFrame.lookAt(hub, hub + axis)
	Geometry.cylinderAlong(sails, 6, 4, hubCf * CFrame.Angles(0, math.pi / 2, 0), rgb(50, 44, 38), M.Wood)
	local wood = rgb(232, 230, 222)
	local stockColor = rgb(48, 40, 34)
	for i = 0, 3 do
		local arm = hubCf * CFrame.Angles(0, 0, i * math.pi / 2)
		if i < 2 then
			Geometry.block(sails, V3(1.2, 92, 1.2), arm * CFrame.new(0, 0, -1.4), stockColor, M.Wood)
		end
		-- Lattice on one side of the stock (sail frame), cloth furled along it.
		local frame = arm * CFrame.new(3.6, 27, -1.6)
		Geometry.block(sails, V3(0.4, 38, 0.4), frame * CFrame.new(3.2, 0, 0), wood, M.Wood)
		Geometry.block(sails, V3(0.4, 38, 0.4), frame * CFrame.new(-3.2, 0, 0), wood, M.Wood)
		for k = 0, 12 do
			Geometry.block(sails, V3(6.8, 0.3, 0.3), frame * CFrame.new(0, -18.5 + k * 3.1, 0), wood, M.Wood)
		end
		Geometry.block(sails, V3(1, 36, 1), frame * CFrame.new(-2.8, 0, -0.5), rgb(214, 206, 186), M.Fabric)
	end
	sails:SetAttribute("Hub", hub)
	sails:SetAttribute("Axis", axis)
	sails:SetAttribute("Speed", 0.9)
	CollectionService:AddTag(sails, "GeoBloxRotor")
	sails.Parent = model
	model.Parent = parent
end

return Buildings
