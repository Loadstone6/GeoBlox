--!strict
-- Procedural trees from parts. Each species has a silhouette that reads at a
-- distance: columnar poplars along roads, broad oaks, pollard willows on ditch
-- banks, windbreak alders/ash behind farms, apple trees in orchards.
-- LOD 2 = full crown with shadows, LOD 1 = trunk + simplified crown.

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Geometry = require(Shared.Geometry)
local Layout = require(Shared.Layout)
local Rng = require(Shared.Rng)

local M = Enum.Material
local rgb = Color3.fromRGB
local V3 = Vector3.new

export type Leaf = { part: BasePart, kind: string, idx: number }

local Trees = {}

local BARK = {
	poplar = rgb(118, 112, 102),
	oak = rgb(76, 66, 56),
	ash = rgb(104, 100, 92),
	willow = rgb(92, 84, 70),
	linden = rgb(82, 74, 64),
	alder = rgb(70, 64, 58),
	apple = rgb(88, 74, 60),
	chestnut = rgb(74, 64, 54),
}

type Shape = {
	trunkFrac: number, -- trunk height as a fraction of tree height
	trunkWidth: { number },
	crownStart: number, -- crown bottom as fraction of height
	balls: number,
	spread: number, -- horizontal scatter as fraction of height
	size: { number }, -- ball diameter range as fraction of height
	columnar: boolean,
}

local SHAPES: { [string]: Shape } = {
	poplar = {
		trunkFrac = 0.6,
		trunkWidth = { 2.2, 3 },
		crownStart = 0.38,
		balls = 6,
		spread = 0.07,
		size = { 0.2, 0.3 },
		columnar = true,
	},
	oak = {
		trunkFrac = 0.45,
		trunkWidth = { 3.4, 4.6 },
		crownStart = 0.42,
		balls = 8,
		spread = 0.2,
		size = { 0.34, 0.46 },
		columnar = false,
	},
	ash = {
		trunkFrac = 0.5,
		trunkWidth = { 2.6, 3.4 },
		crownStart = 0.45,
		balls = 6,
		spread = 0.18,
		size = { 0.3, 0.4 },
		columnar = false,
	},
	linden = {
		trunkFrac = 0.4,
		trunkWidth = { 3, 4 },
		crownStart = 0.35,
		balls = 7,
		spread = 0.14,
		size = { 0.38, 0.5 },
		columnar = false,
	},
	chestnut = {
		trunkFrac = 0.38,
		trunkWidth = { 3.2, 4.2 },
		crownStart = 0.32,
		balls = 7,
		spread = 0.15,
		size = { 0.4, 0.5 },
		columnar = false,
	},
	alder = {
		trunkFrac = 0.5,
		trunkWidth = { 2, 2.8 },
		crownStart = 0.35,
		balls = 5,
		spread = 0.08,
		size = { 0.26, 0.34 },
		columnar = true,
	},
	apple = {
		trunkFrac = 0.42,
		trunkWidth = { 1.1, 1.5 },
		crownStart = 0.4,
		balls = 4,
		spread = 0.18,
		size = { 0.5, 0.65 },
		columnar = false,
	},
}

local function leafBall(
	parent: Instance,
	leaves: { Leaf },
	diameter: number,
	pos: Vector3,
	palette: any,
	idx: number,
	kind: string,
	shadow: boolean
)
	local color = if kind == "willow" then palette.willow else palette.leaves[(idx - 1) % #palette.leaves + 1]
	local ball = Geometry.ball(parent, diameter, pos, color, M.LeafyGrass)
	ball.CastShadow = shadow
	ball.CanCollide = false
	ball.CanQuery = false
	if kind ~= "willow" then
		ball.Transparency = palette.leafTransparency
	end
	table.insert(leaves, { part = ball :: BasePart, kind = kind, idx = idx })
end

local function pollardWillow(
	parent: Instance,
	tree: Layout.Tree,
	ground: number,
	lod: number,
	palette: any,
	leaves: { Leaf }
)
	local rng = Rng.new(tree.seed)
	local trunkH = tree.h * 0.5
	local width = rng:range(3.6, 5)
	local base = V3(tree.x, ground, tree.z)
	local lean = CFrame.Angles(rng:range(-0.12, 0.12), rng:range(0, 6.28), rng:range(-0.12, 0.12))
	local trunkCf = CFrame.new(base) * lean * CFrame.new(0, trunkH / 2 - 0.5, 0)
	local trunk = Geometry.cylinderAlong(
		parent,
		trunkH + 1,
		width,
		trunkCf * CFrame.Angles(0, 0, math.pi / 2),
		BARK.willow,
		M.Wood
	)
	trunk.CastShadow = lod >= 2
	local top = (trunkCf * CFrame.new(0, trunkH / 2, 0)).Position
	Geometry.ball(parent, width * 1.5, top, BARK.willow, M.Wood).CastShadow = lod >= 2
	local count = if lod >= 2 then rng:int(4, 6) else 1
	for i = 1, count do
		local off = if lod >= 2
			then V3(rng:range(-3, 3), rng:range(2, tree.h * 0.4), rng:range(-3, 3))
			else V3(0, tree.h * 0.25, 0)
		local d = if lod >= 2 then rng:range(6, 9) else tree.h * 0.55
		leafBall(parent, leaves, d, top + off, palette, i, "willow", lod >= 2)
	end
end

function Trees.build(parent: Instance, tree: Layout.Tree, ground: number, lod: number, palette: any, leaves: { Leaf })
	if tree.kind == "willow" then
		pollardWillow(parent, tree, ground, lod, palette, leaves)
		return
	end
	local shape = SHAPES[tree.kind]
	local rng = Rng.new(tree.seed)
	local h = tree.h
	local base = V3(tree.x, ground, tree.z)
	local trunkH = h * shape.trunkFrac
	local width = rng:range(shape.trunkWidth[1], shape.trunkWidth[2])
	local trunk = Geometry.cylinderAlong(
		parent,
		trunkH + 2,
		width,
		CFrame.new(base + V3(0, trunkH / 2 - 1, 0)) * CFrame.Angles(0, rng:range(0, 6.28), math.pi / 2),
		BARK[tree.kind],
		M.Wood
	)
	trunk.CastShadow = lod >= 2
	trunk.CanQuery = false

	local colorIdx = rng:int(1, 4)
	if lod < 2 then
		-- Far LOD: one or two balls that match the silhouette.
		local crownH = h * (1 - shape.crownStart)
		if shape.columnar then
			leafBall(
				parent,
				leaves,
				crownH * 0.55,
				base + V3(0, h * shape.crownStart + crownH * 0.3, 0),
				palette,
				colorIdx,
				"leaf",
				false
			)
			leafBall(
				parent,
				leaves,
				crownH * 0.45,
				base + V3(0, h * shape.crownStart + crownH * 0.68, 0),
				palette,
				colorIdx,
				"leaf",
				false
			)
		else
			leafBall(
				parent,
				leaves,
				h * (shape.size[2] + shape.spread),
				base + V3(0, h * (shape.crownStart + 0.3), 0),
				palette,
				colorIdx,
				"leaf",
				false
			)
		end
		return
	end

	-- Main limbs give the crown structure when the leaves are thin (winter).
	if not shape.columnar then
		for i = 1, 3 do
			local yaw = i * 2.1 + rng:range(-0.4, 0.4)
			local len = h * rng:range(0.22, 0.3)
			local from = base + V3(0, trunkH * 0.9, 0)
			local dir = (CFrame.Angles(0, yaw, 0) * CFrame.Angles(rng:range(0.5, 0.9), 0, 0)).LookVector
			local to = from + dir * len + V3(0, len * 0.5, 0)
			local limb = Geometry.block(
				parent,
				V3(width * 0.45, width * 0.45, (to - from).Magnitude),
				CFrame.lookAt((from + to) / 2, to),
				BARK[tree.kind],
				M.Wood
			)
			limb.CanQuery = false
		end
	end

	for i = 1, shape.balls do
		local t = (i - 1) / math.max(1, shape.balls - 1)
		local y, size
		if shape.columnar then
			y = h * (shape.crownStart + 0.08 + t * (0.9 - shape.crownStart))
			size = h * rng:range(shape.size[1], shape.size[2]) * (1.15 - t * 0.5)
		else
			y = h * (shape.crownStart + 0.22 + rng:range(-0.1, 0.18))
			size = h * rng:range(shape.size[1], shape.size[2])
		end
		local spread = h * shape.spread
		local off = V3(rng:range(-spread, spread), 0, rng:range(-spread, spread))
		leafBall(
			parent,
			leaves,
			size,
			base + off + V3(0, y, 0),
			palette,
			colorIdx + (if rng:chance(0.3) then 1 else 0),
			"leaf",
			true
		)
	end
end

return Trees
