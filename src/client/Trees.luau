--!strict
--!native
-- Trees from LiDAR crown records: one batched EditableMesh per ~40 trees.
-- Crowns are clusters of noise-displaced icospheres sized to the measured
-- crown radius and height; vertex colours darken the underside and the inner
-- blobs (cheap self-occlusion); LeafyGrass gives leaf texture through UVs.

local AssetService = game:GetService("AssetService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared")
local ChunkFormat = require(Shared:WaitForChild("ChunkFormat"))

local Trees = {}

local SPM = ChunkFormat.STUDS_PER_METRE

type Geo = { pos: { Vector3 }, nrm: { Vector3 }, col: { Color3 }, uv: { Vector2 }, tri: { number } }

-- Unit icosphere (subdivision s), shared.
local function icosphere(sub: number): ({ Vector3 }, { number })
	local t = (1 + math.sqrt(5)) / 2
	local v: { Vector3 } = {}
	for _, p in
		{
			{ -1, t, 0 },
			{ 1, t, 0 },
			{ -1, -t, 0 },
			{ 1, -t, 0 },
			{ 0, -1, t },
			{ 0, 1, t },
			{ 0, -1, -t },
			{ 0, 1, -t },
			{ t, 0, -1 },
			{ t, 0, 1 },
			{ -t, 0, -1 },
			{ -t, 0, 1 },
		}
	do
		table.insert(v, Vector3.new(p[1], p[2], p[3]).Unit)
	end
	local f = {
		1,
		12,
		6,
		1,
		6,
		2,
		1,
		2,
		8,
		1,
		8,
		11,
		1,
		11,
		12,
		2,
		6,
		10,
		6,
		12,
		5,
		12,
		11,
		3,
		11,
		8,
		7,
		8,
		2,
		9,
		4,
		10,
		5,
		4,
		5,
		3,
		4,
		3,
		7,
		4,
		7,
		9,
		4,
		9,
		10,
		5,
		10,
		6,
		3,
		5,
		12,
		7,
		3,
		11,
		9,
		7,
		8,
		10,
		9,
		2,
	}
	for _ = 1, sub do
		local cache: { [number]: number } = {}
		local function mid(a: number, b: number): number
			local key = if a < b then a * 4096 + b else b * 4096 + a
			local hit = cache[key]
			if hit then
				return hit
			end
			table.insert(v, ((v[a] + v[b]) / 2).Unit)
			cache[key] = #v
			return #v
		end
		local nf = {}
		for i = 1, #f, 3 do
			local a, b, c = f[i], f[i + 1], f[i + 2]
			local ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
			for _, x in { a, ab, ca, b, bc, ab, c, ca, bc, ab, bc, ca } do
				table.insert(nf, x)
			end
		end
		f = nf
	end
	return v, f
end

local ICO1_V, ICO1_F = icosphere(1)
local ICO0_V, ICO0_F = icosphere(0)

local LEAF = {
	[0] = { Color3.fromRGB(88, 112, 52), Color3.fromRGB(100, 122, 58), Color3.fromRGB(78, 104, 48) }, -- London plane
	[1] = { Color3.fromRGB(72, 102, 46), Color3.fromRGB(86, 114, 50), Color3.fromRGB(64, 92, 42) },
	[2] = { Color3.fromRGB(44, 70, 44), Color3.fromRGB(52, 78, 48), Color3.fromRGB(40, 64, 40) },
	[3] = { Color3.fromRGB(104, 128, 60), Color3.fromRGB(116, 138, 66), Color3.fromRGB(96, 120, 56) },
}
local BARK = {
	[0] = Color3.fromRGB(150, 140, 112), -- plane: pale mottled
	[1] = Color3.fromRGB(86, 74, 62),
	[2] = Color3.fromRGB(80, 62, 50),
	[3] = Color3.fromRGB(96, 84, 70),
}

local function blob(
	g: Geo,
	center: Vector3,
	rx: number,
	ry: number,
	color: Color3,
	inner: number,
	rng: Random,
	verts: { Vector3 },
	faces: { number },
	jitter: number
)
	local base = #g.pos
	local phase = rng:NextNumber() * 10
	for _, d in verts do
		-- Low-frequency lumps so crowns are not perfect ellipsoids.
		local n = 1
			+ jitter * math.sin(d.X * 3.1 + phase) * math.cos(d.Z * 2.7 - phase)
			+ jitter * 0.6 * math.sin(d.Y * 4.3 + phase * 2)
		local p = center + Vector3.new(d.X * rx * n, d.Y * ry * n, d.Z * rx * n)
		table.insert(g.pos, p)
		table.insert(g.nrm, d)
		local shade = (0.62 + 0.38 * (d.Y * 0.5 + 0.5)) * inner
		table.insert(g.col, Color3.new(color.R * shade, color.G * shade, color.B * shade))
		table.insert(g.uv, Vector2.new(math.atan2(d.Z, d.X) / math.pi * 3, d.Y * 2.5))
	end
	for _, fi in faces do
		table.insert(g.tri, base + fi)
	end
end

local function trunk(g: Geo, base: Vector3, top: Vector3, r0: number, r1: number, color: Color3)
	local sides = 6
	local start = #g.pos
	local axis = top - base
	local up = axis.Unit
	local side = if math.abs(up.Y) < 0.95 then up:Cross(Vector3.yAxis).Unit else Vector3.xAxis
	local fwd = up:Cross(side).Unit
	for i = 0, sides - 1 do
		local a = i / sides * math.pi * 2
		local dir = side * math.cos(a) + fwd * math.sin(a)
		table.insert(g.pos, base + dir * r0)
		table.insert(g.pos, top + dir * r1)
		table.insert(g.nrm, dir)
		table.insert(g.nrm, dir)
		table.insert(g.col, Color3.new(color.R * 0.7, color.G * 0.7, color.B * 0.7))
		table.insert(g.col, color)
		table.insert(g.uv, Vector2.new(i / sides * 2, 0))
		table.insert(g.uv, Vector2.new(i / sides * 2, axis.Magnitude / 4))
	end
	for i = 0, sides - 1 do
		local j = (i + 1) % sides
		local a0, a1 = start + i * 2 + 1, start + i * 2 + 2
		local b0, b1 = start + j * 2 + 1, start + j * 2 + 2
		-- Outward-facing (counter-clockwise seen from outside).
		for _, x in { a0, b0, b1, a0, b1, a1 } do
			table.insert(g.tri, x)
		end
	end
end

-- Geometry for one tree at chunk-relative position (studs).
local function tree(leaves: Geo, wood: Geo, t: ChunkFormat.TreeRec, lod: number)
	local rng = Random.new(t.seed)
	local h = t.height * SPM
	local r = math.max(1.2, t.crown) * SPM
	local base = Vector3.new(t.x, t.z - 0.5, t.y)
	local palette = LEAF[t.kind] or LEAF[1]
	local crownBase = if t.kind == 2 then 0.25 else 0.38
	local crownMid = base + Vector3.new(0, h * (crownBase + (1 - crownBase) * 0.5), 0)
	local crownHalfH = h * (1 - crownBase) * 0.5
	if lod >= 1 then
		blob(leaves, crownMid, r * 0.95, crownHalfH, palette[1], 1, rng, ICO0_V, ICO0_F, 0.08)
		return
	end
	local trunkTop = base + Vector3.new(0, h * (crownBase + 0.12), 0)
	trunk(
		wood,
		base,
		trunkTop,
		math.clamp(h * 0.022, 0.6, 2.2),
		math.clamp(h * 0.014, 0.4, 1.4),
		BARK[t.kind] or BARK[1]
	)
	if t.kind == 2 then
		for k = 0, 3 do
			local f = k / 3
			blob(
				leaves,
				base + Vector3.new(0, h * (0.35 + 0.55 * f), 0),
				r * (1 - 0.7 * f),
				crownHalfH * 0.42,
				palette[1 + k % 3],
				0.9 + 0.1 * f,
				rng,
				ICO1_V,
				ICO1_F,
				0.12
			)
		end
		return
	end
	-- Main limbs towards the blob centres, then blobs.
	local n = if t.kind == 3 then 2 else rng:NextInteger(4, 6)
	for k = 1, n do
		local a = (k / n) * math.pi * 2 + rng:NextNumber() * 0.8
		local spread = r * (if k == 1 then 0.1 else 0.45)
		local c = crownMid
			+ Vector3.new(math.cos(a) * spread, (rng:NextNumber() - 0.35) * crownHalfH * 0.6, math.sin(a) * spread)
		if k > 1 then
			trunk(wood, trunkTop, trunkTop:Lerp(c, 0.7), math.clamp(h * 0.01, 0.3, 0.9), 0.25, BARK[t.kind] or BARK[1])
		end
		local inner = if k == 1 then 0.82 else 1.0
		blob(
			leaves,
			c,
			r * (0.6 + 0.15 * rng:NextNumber()),
			crownHalfH * (0.62 + 0.15 * rng:NextNumber()),
			palette[1 + k % 3],
			inner,
			rng,
			ICO1_V,
			ICO1_F,
			0.14
		)
	end
end

local function toMesh(g: Geo, material: Enum.Material, origin: Vector3, shadow: boolean): MeshPart?
	if #g.tri == 0 then
		return nil
	end
	local ok, em = pcall(function()
		return AssetService:CreateEditableMesh()
	end)
	if not ok or em == nil then
		return nil
	end
	local mesh = em :: EditableMesh
	local lo, hi = Vector3.new(math.huge, math.huge, math.huge), Vector3.new(-math.huge, -math.huge, -math.huge)
	for _, p in g.pos do
		lo = lo:Min(p)
		hi = hi:Max(p)
	end
	local c = (lo + hi) / 2
	local vid, nid, cid, uid = table.create(#g.pos), table.create(#g.pos), table.create(#g.pos), table.create(#g.pos)
	for i, p in g.pos do
		vid[i] = mesh:AddVertex(p - c)
		nid[i] = mesh:AddNormal(g.nrm[i])
		cid[i] = mesh:AddColor(g.col[i], 1)
		uid[i] = mesh:AddUV(g.uv[i])
	end
	for i = 1, #g.tri, 3 do
		local a, b, d = g.tri[i], g.tri[i + 1], g.tri[i + 2]
		local f = mesh:AddTriangle(vid[a], vid[b], vid[d])
		mesh:SetFaceNormals(f, { nid[a], nid[b], nid[d] })
		mesh:SetFaceColors(f, { cid[a], cid[b], cid[d] })
		mesh:SetFaceUVs(f, { uid[a], uid[b], uid[d] })
	end
	local ok2, part = pcall(function()
		return AssetService:CreateMeshPartAsync(
			Content.fromObject(mesh),
			{ CollisionFidelity = Enum.CollisionFidelity.Box }
		)
	end)
	if not ok2 or part == nil then
		mesh:Destroy()
		return nil
	end
	local mp = part :: MeshPart
	mp.Anchored = true
	mp.CanCollide = false
	mp.CanQuery = false
	mp.CanTouch = false
	mp.Material = material
	mp.Color = Color3.new(1, 1, 1)
	mp.CastShadow = shadow
	mp.CFrame = CFrame.new(origin + c)
	return mp
end

-- Build all trees of a chunk into `parent`; yields. Returns the triangle count.
function Trees.build(chunk: ChunkFormat.Chunk, lod: number, parent: Instance): number
	local tris = 0
	local origin = Vector3.new(chunk.ax, chunk.ay, chunk.az)
	local batch = if lod == 0 then 36 else 400
	for start = 1, #chunk.trees, batch do
		local leaves: Geo = { pos = {}, nrm = {}, col = {}, uv = {}, tri = {} }
		local wood: Geo = { pos = {}, nrm = {}, col = {}, uv = {}, tri = {} }
		for i = start, math.min(#chunk.trees, start + batch - 1) do
			tree(leaves, wood, chunk.trees[i], lod)
		end
		local lp = toMesh(leaves, Enum.Material.LeafyGrass, origin, true)
		if lp then
			lp.Name = "TreeCrowns"
			lp.Parent = parent
			tris += #leaves.tri // 3
		end
		local wp = toMesh(wood, Enum.Material.Wood, origin, true)
		if wp then
			wp.Name = "TreeTrunks"
			wp.Parent = parent
			tris += #wood.tri // 3
		end
	end
	return tris
end

return Trees
