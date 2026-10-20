--!strict
-- Part factories used by the server (buildings) and the client (decor).
-- Properties are set before parenting; everything is anchored and static.

local Geometry = {}

local function base(part: BasePart, size: Vector3, cf: CFrame, color: Color3, material: Enum.Material)
	part.Anchored = true
	part.CanTouch = false
	part.Size = size
	part.CFrame = cf
	part.Color = color
	part.Material = material
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
end

function Geometry.block(parent: Instance?, size: Vector3, cf: CFrame, color: Color3, material: Enum.Material?): Part
	local p = Instance.new("Part")
	base(p, size, cf, color, material or Enum.Material.SmoothPlastic)
	p.Parent = parent
	return p
end

-- Vertical cylinder centred at `pos`.
function Geometry.cylinder(
	parent: Instance?,
	height: number,
	diameter: number,
	pos: Vector3,
	color: Color3,
	material: Enum.Material?
): Part
	local p = Instance.new("Part")
	p.Shape = Enum.PartType.Cylinder
	base(
		p,
		Vector3.new(height, diameter, diameter),
		CFrame.new(pos) * CFrame.Angles(0, 0, math.pi / 2),
		color,
		material or Enum.Material.SmoothPlastic
	)
	p.Parent = parent
	return p
end

-- Cylinder whose axis runs along cf.RightVector.
function Geometry.cylinderAlong(
	parent: Instance?,
	length: number,
	diameter: number,
	cf: CFrame,
	color: Color3,
	material: Enum.Material?
): Part
	local p = Instance.new("Part")
	p.Shape = Enum.PartType.Cylinder
	base(p, Vector3.new(length, diameter, diameter), cf, color, material or Enum.Material.SmoothPlastic)
	p.Parent = parent
	return p
end

function Geometry.ball(parent: Instance?, diameter: number, pos: Vector3, color: Color3, material: Enum.Material?): Part
	local p = Instance.new("Part")
	p.Shape = Enum.PartType.Ball
	base(p, Vector3.new(diameter, diameter, diameter), CFrame.new(pos), color, material or Enum.Material.SmoothPlastic)
	p.Parent = parent
	return p
end

-- Solid triangle from two wedges. The slab extends `thickness` from the plane
-- on the side of `outward` (if given), so roofs sit on top of their outline.
function Geometry.triangle(
	parent: Instance?,
	a: Vector3,
	b: Vector3,
	c: Vector3,
	thickness: number,
	color: Color3,
	material: Enum.Material,
	outward: Vector3?
): (WedgePart?, WedgePart?)
	local ab, ac, bc = b - a, c - a, c - b
	local abd, acd, bcd = ab:Dot(ab), ac:Dot(ac), bc:Dot(bc)
	if abd > acd and abd > bcd then
		c, a = a, c
	elseif acd > bcd and acd > abd then
		a, b = b, a
	end
	ab, ac, bc = b - a, c - a, c - b
	local cross = ac:Cross(ab)
	if cross.Magnitude < 1e-4 then
		return nil, nil
	end
	local right = cross.Unit
	local up = bc:Cross(right).Unit
	local back = bc.Unit
	local height = math.abs(ab:Dot(up))
	local shift = right * (thickness / 2)
	if outward and right:Dot(outward) < 0 then
		shift = -shift
	end

	local w1 = Instance.new("WedgePart")
	base(
		w1,
		Vector3.new(thickness, height, math.abs(ab:Dot(back))),
		CFrame.fromMatrix((a + b) / 2 + shift, right, up, back),
		color,
		material
	)
	local w2 = Instance.new("WedgePart")
	base(
		w2,
		Vector3.new(thickness, height, math.abs(ac:Dot(back))),
		CFrame.fromMatrix((a + c) / 2 + shift, -right, up, -back),
		color,
		material
	)
	w1.Parent = parent
	w2.Parent = parent
	return w1, w2
end

function Geometry.quad(
	parent: Instance?,
	a: Vector3,
	b: Vector3,
	c: Vector3,
	d: Vector3,
	thickness: number,
	color: Color3,
	material: Enum.Material,
	outward: Vector3?
)
	Geometry.triangle(parent, a, b, c, thickness, color, material, outward)
	Geometry.triangle(parent, a, c, d, thickness, color, material, outward)
end

-- Faceted frustum (e.g. an octagonal mill body) or cone (r1 = 0).
function Geometry.frustum(
	parent: Instance?,
	center: Vector3,
	r0: number,
	r1: number,
	height: number,
	sides: number,
	color: Color3,
	material: Enum.Material,
	thickness: number?,
	rotation: number?
)
	local t = thickness or 0.6
	local rot = rotation or 0
	local top = center + Vector3.new(0, height, 0)
	for i = 0, sides - 1 do
		local a0 = rot + i / sides * math.pi * 2
		local a1 = rot + (i + 1) / sides * math.pi * 2
		local p0 = center + Vector3.new(math.cos(a0) * r0, 0, math.sin(a0) * r0)
		local p1 = center + Vector3.new(math.cos(a1) * r0, 0, math.sin(a1) * r0)
		local mid = (a0 + a1) / 2
		local outward = Vector3.new(math.cos(mid), 0, math.sin(mid))
		if r1 <= 0.01 then
			Geometry.triangle(parent, p0, p1, top, t, color, material, outward)
		else
			local q0 = top + Vector3.new(math.cos(a0) * r1, 0, math.sin(a0) * r1)
			local q1 = top + Vector3.new(math.cos(a1) * r1, 0, math.sin(a1) * r1)
			Geometry.quad(parent, p0, p1, q1, q0, t, color, material, outward)
		end
	end
end

return Geometry
