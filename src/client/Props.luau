--!strict
-- Small procedural props: field gates, wrapped silage bales, hedges, mailboxes,
-- red cycle strips on the road and green hectometre posts.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Geometry = require(Shared.Geometry)
local Layout = require(Shared.Layout)
local Rng = require(Shared.Rng)

local M = Enum.Material
local rgb = Color3.fromRGB
local V3 = Vector3.new

export type Leaf = { part: BasePart, kind: string, idx: number }

local Props = {}

local GALVANISED = rgb(172, 174, 170)
local BALE_COLORS = { rgb(236, 236, 230), rgb(30, 30, 32), rgb(70, 100, 62) }

local function gate(parent: Instance, prop: Layout.Prop, ground: number)
	local cf = CFrame.new(prop.x, ground, prop.z) * CFrame.Angles(0, prop.yaw, 0)
	local half = prop.len / 2
	for _, sx in { -1, 1 } do
		Geometry.block(parent, V3(0.7, 6, 0.7), cf * CFrame.new(sx * half, 1.5, 0), rgb(96, 82, 66), M.Wood)
	end
	for k = 0, 4 do
		Geometry.block(
			parent,
			V3(prop.len - 0.8, 0.22, 0.22),
			cf * CFrame.new(0, 0.8 + k * 0.85, 0),
			GALVANISED,
			M.Metal
		)
	end
	local diag = Geometry.block(
		parent,
		V3(math.sqrt(prop.len ^ 2 + 3.4 ^ 2) - 1, 0.22, 0.22),
		cf * CFrame.new(0, 2.5, 0) * CFrame.Angles(0, 0, math.atan(3.4 / prop.len)),
		GALVANISED,
		M.Metal
	)
	diag.CastShadow = false
end

local function bale(parent: Instance, prop: Layout.Prop, ground: number)
	local stacked = prop.variant >= 10
	local color = BALE_COLORS[prop.variant % 10]
	local y = ground + 2.2 + (if stacked then 3.9 else 0)
	local x = prop.x + (if stacked then 0 else 0)
	local z = prop.z + (if stacked then 2.2 else 0)
	local p = Geometry.cylinderAlong(parent, 4, 4.4, CFrame.new(x, y, z), color, M.SmoothPlastic)
	p.Reflectance = 0.06
end

local function hedge(parent: Instance, prop: Layout.Prop, ground: number, palette: any, leaves: { Leaf })
	local rng = Rng.new(prop.seed)
	local h = rng:range(4.5, 6)
	local p = Geometry.block(
		parent,
		V3(prop.len + 0.6, h, 3.6),
		CFrame.new(prop.x, ground + h / 2 - 0.5, prop.z),
		palette.hedge or rgb(60, 92, 44),
		M.LeafyGrass
	)
	p.CanQuery = false
	table.insert(leaves, { part = p :: BasePart, kind = "hedge", idx = 1 })
end

local function mailbox(parent: Instance, prop: Layout.Prop, ground: number)
	local rng = Rng.new(prop.seed)
	Geometry.block(parent, V3(0.4, 4, 0.4), CFrame.new(prop.x, ground + 1.6, prop.z), rgb(60, 60, 62), M.Metal)
	local colors = { rgb(30, 74, 50), rgb(160, 30, 32), rgb(40, 40, 42) }
	Geometry.block(parent, V3(1.3, 1.3, 2), CFrame.new(prop.x, ground + 4, prop.z), colors[rng:int(1, 3)], M.Metal)
end

function Props.build(parent: Instance, prop: Layout.Prop, ground: number, palette: any, leaves: { Leaf })
	if prop.kind == "gate" then
		gate(parent, prop, ground)
	elseif prop.kind == "bale" then
		bale(parent, prop, ground)
	elseif prop.kind == "hedge" then
		hedge(parent, prop, ground, palette, leaves)
	elseif prop.kind == "mailbox" then
		mailbox(parent, prop, ground)
	end
end

-- Red cycle strips ("suggestiestroken") and hectometre posts for x in [x0, x1).
function Props.road(parent: Instance, x0: number, x1: number)
	local step = 16
	local mid = (Config.STRIP_IN + Config.STRIP_OUT) / 2
	local width = Config.STRIP_OUT - Config.STRIP_IN
	for x = x0, x1 - step, step do
		local cx = x + step / 2
		local zc, slope = Layout.road(cx)
		local norm = 1 / math.sqrt(1 + slope * slope)
		local yaw = -math.atan(slope)
		local len = step / norm + 0.2
		for _, side in { 1, -1 } do
			local p = Geometry.block(
				parent,
				V3(len, 0.5, width),
				CFrame.new(cx, Config.ROAD_Y + 0.02, zc + side * mid / norm) * CFrame.Angles(0, yaw, 0),
				rgb(150, 72, 62),
				M.Asphalt
			)
			p.CastShadow = false
			p.CanQuery = false
		end
	end
	-- Hectometre posts every 100 m on the +z verge.
	local hm = Config.m(100)
	local first = math.ceil(x0 / hm) * hm
	for x = first, x1 - 1, hm do
		local zc, slope = Layout.road(x)
		local norm = 1 / math.sqrt(1 + slope * slope)
		local pos = V3(x, Config.ROAD_Y, zc + 11 / norm)
		Geometry.block(parent, V3(0.5, 3.4, 0.3), CFrame.new(pos + V3(0, 1.2, 0)), rgb(30, 90, 56), M.SmoothPlastic)
		Geometry.block(parent, V3(0.55, 1, 0.32), CFrame.new(pos + V3(0, 2.3, 0)), rgb(236, 236, 230), M.SmoothPlastic)
	end
end

return Props
