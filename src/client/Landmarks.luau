--!strict
-- London Eye (rotating, measured wheel plane) and Big Ben's clock hands.

local AssetService = game:GetService("AssetService")
local RunService = game:GetService("RunService")

local Landmarks = {}

local rgb = Color3.fromRGB
local V3 = Vector3.new

type Geo = { pos: { Vector3 }, nrm: { Vector3 }, tri: { number } }

local function tube(g: Geo, a: Vector3, b: Vector3, r: number, sides: number)
	local axis = b - a
	if axis.Magnitude < 1e-3 then
		return
	end
	local up = axis.Unit
	local s = if math.abs(up.Y) < 0.9 then up:Cross(Vector3.yAxis).Unit else up:Cross(Vector3.xAxis).Unit
	local f = up:Cross(s).Unit
	local start = #g.pos
	for i = 0, sides - 1 do
		local ang = i / sides * math.pi * 2
		local d = s * math.cos(ang) + f * math.sin(ang)
		table.insert(g.pos, a + d * r)
		table.insert(g.pos, b + d * r)
		table.insert(g.nrm, d)
		table.insert(g.nrm, d)
	end
	for i = 0, sides - 1 do
		local j = (i + 1) % sides
		local a0, a1 = start + i * 2 + 1, start + i * 2 + 2
		local b0, b1 = start + j * 2 + 1, start + j * 2 + 2
		for _, x in { a0, b0, b1, a0, b1, a1 } do
			table.insert(g.tri, x)
		end
	end
end

local function meshPart(g: Geo, color: Color3, material: Enum.Material, at: CFrame): MeshPart?
	local ok, em = pcall(function()
		return AssetService:CreateEditableMesh()
	end)
	if not ok or not em then
		return nil
	end
	local mesh = em :: EditableMesh
	local vid, nid = table.create(#g.pos), table.create(#g.pos)
	for i, p in g.pos do
		vid[i] = mesh:AddVertex(p)
		nid[i] = mesh:AddNormal(g.nrm[i])
	end
	for i = 1, #g.tri, 3 do
		local a, b, c = g.tri[i], g.tri[i + 1], g.tri[i + 2]
		local fid = mesh:AddTriangle(vid[a], vid[b], vid[c])
		mesh:SetFaceNormals(fid, { nid[a], nid[b], nid[c] })
	end
	local ok2, part = pcall(function()
		return AssetService:CreateMeshPartAsync(
			Content.fromObject(mesh),
			{ CollisionFidelity = Enum.CollisionFidelity.Box }
		)
	end)
	if not ok2 or not part then
		return nil
	end
	local mp = part :: MeshPart
	mp.Anchored = true
	mp.CanCollide = false
	mp.CanQuery = false
	mp.CanTouch = false
	mp.Color = color
	mp.Material = material
	mp.CFrame = at
	return mp
end

local function block(parent: Instance, size: Vector3, cf: CFrame, color: Color3, material: Enum.Material?): Part
	local p = Instance.new("Part")
	p.Anchored = true
	p.CanCollide = false
	p.CanTouch = false
	p.CanQuery = false
	p.Size = size
	p.CFrame = cf
	p.Color = color
	p.Material = material or Enum.Material.SmoothPlastic
	p.Parent = parent
	return p
end

-- London Eye ------------------------------------------------------------------

function Landmarks.londonEye(spec: any, parent: Instance)
	if not spec or not spec.hub then
		return
	end
	local S = 1 / 0.3
	local hub = V3(spec.hub[1], spec.hub[2], spec.hub[3])
	local plane = V3(spec.plane[1], spec.plane[2], spec.plane[3]).Unit
	local axle = V3(spec.axle[1], spec.axle[2], spec.axle[3]).Unit
	local R = spec.radius
	local groundY = spec.base_y
	local model = Instance.new("Model")
	model.Name = "LondonEye"
	model.Parent = parent

	-- Wheel in local space: X = plane direction, Y = up, Z = axle.
	local g: Geo = { pos = {}, nrm = {}, tri = {} }
	local N = 64
	local depth = 3.2 * S
	local rings = {
		{ r = R, z = depth },
		{ r = R, z = -depth },
		{ r = R - 4.5 * S, z = 0 },
	}
	local function pt(r: number, ang: number, z: number): Vector3
		return V3(math.cos(ang) * r, math.sin(ang) * r, z)
	end
	for _, ring in rings do
		for i = 0, N - 1 do
			local a0, a1 = i / N * math.pi * 2, (i + 1) / N * math.pi * 2
			tube(g, pt(ring.r, a0, ring.z), pt(ring.r, a1, ring.z), 0.45 * S, 6)
		end
	end
	for i = 0, N - 1 do
		local a = i / N * math.pi * 2
		local o1, o2, inner = pt(R, a, depth), pt(R, a, -depth), pt(R - 4.5 * S, a, 0)
		tube(g, o1, o2, 0.2 * S, 4)
		tube(g, o1, inner, 0.2 * S, 4)
		tube(g, o2, inner, 0.2 * S, 4)
		-- Spoke cables to both ends of the spindle.
		tube(g, pt(R - 4.5 * S, a, 0), V3(0, 0, if i % 2 == 0 then 10 * S else -10 * S), 0.07 * S, 4)
	end
	tube(g, V3(0, 0, -11 * S), V3(0, 0, 11 * S), 1.6 * S, 12)
	-- Right-handed frame: local Z is plane x up; `axle` keeps the landward sign.
	local zAxis = plane:Cross(Vector3.yAxis).Unit
	local frame = CFrame.fromMatrix(hub, plane, Vector3.yAxis, zAxis)
	local wheel = meshPart(g, rgb(232, 234, 236), Enum.Material.Metal, frame)
	if not wheel then
		return
	end
	wheel.Name = "Wheel"
	wheel.Parent = model

	-- A-frame on the landward side of the spindle.
	local landEnd = hub + axle * 11 * S
	for _, side in { -1, 1 } do
		local foot = V3(hub.X, groundY, hub.Z) + axle * 52 * S + plane * side * 24 * S
		local mid = (landEnd + foot) / 2
		local len = (landEnd - foot).Magnitude
		block(model, V3(1.8 * S, 1.8 * S, len), CFrame.lookAt(mid, landEnd), rgb(226, 228, 230), Enum.Material.Metal)
	end
	local tie = (V3(hub.X, groundY, hub.Z) + axle * 52 * S)
	block(
		model,
		V3(1.0 * S, 1.0 * S, (tie - landEnd).Magnitude),
		CFrame.lookAt((tie + landEnd) / 2, landEnd),
		rgb(226, 228, 230),
		Enum.Material.Metal
	)

	-- Capsules (kept level while the wheel turns).
	local capsules: { BasePart } = {}
	for i = 1, spec.capsules or 32 do
		local c = Instance.new("Part")
		c.Shape = Enum.PartType.Ball
		c.Anchored = true
		c.CanCollide = false
		c.CanQuery = false
		c.CanTouch = false
		c.Size = V3(4 * S, 4 * S, 4 * S)
		c.Material = Enum.Material.Glass
		c.Color = rgb(200, 220, 232)
		c.Transparency = 0.15
		c.Reflectance = 0.25
		local sm = Instance.new("SpecialMesh")
		sm.MeshType = Enum.MeshType.Sphere
		sm.Scale = V3(2.0, 1.0, 1.0)
		sm.Parent = c
		c.Parent = model
		table.insert(capsules, c)
	end

	local period = 30 * 60 -- one revolution every 30 minutes, like the real wheel
	local cframes = table.create(#capsules) :: { CFrame }
	RunService.Heartbeat:Connect(function()
		local ang = (os.clock() % period) / period * math.pi * 2
		local rot = frame * CFrame.Angles(0, 0, -ang)
		wheel.CFrame = rot
		for i, _ in capsules do
			local a = -ang + (i - 1) / #capsules * math.pi * 2
			local p = frame:PointToWorldSpace(V3(math.cos(a) * (R + 3.4 * S), math.sin(a) * (R + 3.4 * S), 0))
			cframes[i] = CFrame.fromMatrix(p, zAxis, Vector3.yAxis, -plane)
		end
		workspace:BulkMoveTo(capsules, cframes, Enum.BulkMoveMode.FireCFrameChanged)
	end)
end

-- Big Ben ----------------------------------------------------------------------

-- UK civil time: GMT, or BST (UTC+1) from 01:00 UTC on the last Sunday of March
-- to 01:00 UTC on the last Sunday of October.
local function lastSunday(year: number, month: number): number
	-- 01:00 UTC on the last day of the month, stepped back to Sunday.
	local t = DateTime.fromUniversalTime(year, month + 1, 1, 1).UnixTimestamp - 86400
	local d = os.date("!*t", t) :: any
	return t - ((d.wday - 1) * 86400)
end

function Landmarks.londonTime(): number
	local now = os.time()
	local y = (os.date("!*t", now) :: any).year
	local bst = now >= lastSunday(y, 3) and now < lastSunday(y, 10)
	return now + (if bst then 3600 else 0)
end

Landmarks.dialGlow = {} :: { BasePart }

function Landmarks.bigBen(dials: { any }, parent: Instance)
	if not dials or #dials == 0 then
		return
	end
	local S = 1 / 0.3
	local model = Instance.new("Model")
	model.Name = "BigBenHands"
	model.Parent = parent
	type Hand = { part: BasePart, len: number, center: Vector3, n: Vector3, right: Vector3, kind: string }
	local hands: { Hand } = {}
	for _, d in dials do
		local c = V3(d.center[1], d.center[2], d.center[3])
		local n = V3(d.normal[1], d.normal[2], d.normal[3]).Unit
		local right = (-n):Cross(Vector3.yAxis).Unit
		local glow = Instance.new("Part")
		glow.Shape = Enum.PartType.Cylinder
		glow.Anchored = true
		glow.CanCollide = false
		glow.CanQuery = false
		glow.CanTouch = false
		glow.Size = V3(0.05, d.radius * 2 * 0.95, d.radius * 2 * 0.95)
		glow.CFrame = CFrame.fromMatrix(c + n * 0.1, n, Vector3.yAxis)
		glow.Material = Enum.Material.Neon
		glow.Color = rgb(255, 238, 200)
		glow.Transparency = 1
		glow.Parent = model
		table.insert(Landmarks.dialGlow, glow)
		for _, spec in
			{ { kind = "hour", len = 2.7 * S, w = 0.45 * S }, { kind = "minute", len = 4.2 * S, w = 0.3 * S } }
		do
			local p = Instance.new("Part")
			p.Anchored = true
			p.CanCollide = false
			p.CanQuery = false
			p.CanTouch = false
			p.Size = V3(spec.w, spec.len, 0.15 * S)
			p.Color = rgb(28, 28, 30)
			p.Material = Enum.Material.Metal
			p.Parent = model
			table.insert(hands, {
				part = p :: BasePart,
				len = spec.len,
				center = c + n * (if spec.kind == "hour" then 0.25 else 0.4),
				n = n,
				right = right,
				kind = spec.kind,
			})
		end
	end
	local function update()
		local t = Landmarks.londonTime() % 86400
		local minutes = (t / 60) % 60
		local hours = (t / 3600) % 12
		for _, h in hands do
			local turns = if h.kind == "hour" then hours / 12 else minutes / 60
			local a = turns * math.pi * 2
			local dir = Vector3.yAxis * math.cos(a) + h.right * math.sin(a)
			local side = dir:Cross(h.n)
			h.part.CFrame = CFrame.fromMatrix(h.center + dir * (h.len / 2 - 0.4), side, dir, h.n)
		end
	end
	update()
	task.spawn(function()
		while model.Parent do
			update()
			task.wait(5)
		end
	end)
end

return Landmarks
