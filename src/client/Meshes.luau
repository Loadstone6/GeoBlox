--!strict
--!native
-- Builds MeshParts from decoded chunk geometry via EditableMesh.

local AssetService = game:GetService("AssetService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared")
local ChunkFormat = require(Shared:WaitForChild("ChunkFormat"))
local Config = require(Shared:WaitForChild("Config"))

local Groups = require(script.Parent.Groups)

local Meshes = {}

Meshes.failures = 0
Meshes.built = 0
Meshes.triangles = 0
Meshes.lastError = ""

local readi16, readi8, readu8, readu16 = buffer.readi16, buffer.readi8, buffer.readu8, buffer.readu16

-- Build one MeshPart. Yields (CreateMeshPartAsync). Returns nil on failure.
function Meshes.build(chunk: ChunkFormat.Chunk, ref: ChunkFormat.MeshRef, groupName: string): MeshPart?
	local b = chunk.buf
	local nv, nt = ref.nverts, ref.ntris
	local base = ref.vertexOffset

	-- Bounding box (studs, chunk-relative) so the mesh is centred on its part.
	local minX, minY, minZ = math.huge, math.huge, math.huge
	local maxX, maxY, maxZ = -math.huge, -math.huge, -math.huge
	for i = 0, nv - 1 do
		local o = base + i * 16
		local x, y, z = readi16(b, o) / 32, readi16(b, o + 2) / 32, readi16(b, o + 4) / 32
		if x < minX then
			minX = x
		end
		if x > maxX then
			maxX = x
		end
		if y < minY then
			minY = y
		end
		if y > maxY then
			maxY = y
		end
		if z < minZ then
			minZ = z
		end
		if z > maxZ then
			maxZ = z
		end
	end
	local cx, cy, cz = (minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2

	local ok, em = pcall(function()
		return AssetService:CreateEditableMesh()
	end)
	if not ok or em == nil then
		Meshes.failures += 1
		Meshes.lastError = tostring(em)
		return nil
	end
	local mesh = em :: EditableMesh

	local vids = table.create(nv) :: { number }
	local nids = table.create(nv) :: { number }
	local cids = table.create(nv) :: { number }
	local uids = table.create(nv) :: { number }
	local fromRGB = Color3.fromRGB
	for i = 0, nv - 1 do
		local o = base + i * 16
		local x, y, z = readi16(b, o) / 32 - cx, readi16(b, o + 2) / 32 - cy, readi16(b, o + 4) / 32 - cz
		vids[i + 1] = mesh:AddVertex(Vector3.new(x, y, z))
		nids[i + 1] =
			mesh:AddNormal(Vector3.new(readi8(b, o + 6) / 127, readi8(b, o + 7) / 127, readi8(b, o + 8) / 127))
		cids[i + 1] = mesh:AddColor(fromRGB(readu8(b, o + 9), readu8(b, o + 10), readu8(b, o + 11)), 1)
		uids[i + 1] = mesh:AddUV(Vector2.new(readi16(b, o + 12) / 64, -readi16(b, o + 14) / 64))
	end
	local io = ref.indexOffset
	local flip = Config.FLIP_WINDING
	for t = 0, nt - 1 do
		local o = io + t * 6
		local a = readu16(b, o) + 1
		local c1 = readu16(b, o + 2) + 1
		local c2 = readu16(b, o + 4) + 1
		if flip then
			c1, c2 = c2, c1
		end
		local fid = mesh:AddTriangle(vids[a], vids[c1], vids[c2])
		mesh:SetFaceNormals(fid, { nids[a], nids[c1], nids[c2] })
		mesh:SetFaceColors(fid, { cids[a], cids[c1], cids[c2] })
		mesh:SetFaceUVs(fid, { uids[a], uids[c1], uids[c2] })
	end

	local style = Groups[groupName]
	local ok2, part = pcall(function()
		return AssetService:CreateMeshPartAsync(Content.fromObject(mesh), {
			CollisionFidelity = Enum.CollisionFidelity.Box,
			RenderFidelity = Enum.RenderFidelity.Precise,
		})
	end)
	if not ok2 or part == nil then
		Meshes.failures += 1
		Meshes.lastError = tostring(part)
		mesh:Destroy()
		return nil
	end
	local mp = part :: MeshPart
	mp.Name = groupName
	mp.Anchored = true
	mp.CanCollide = false
	mp.CanQuery = false
	mp.CanTouch = false
	mp.CastShadow = if style then style.shadow else true
	mp.Material = if style then style.material else Enum.Material.SmoothPlastic
	mp.Reflectance = if style then style.reflectance else 0
	mp.Color = Color3.new(1, 1, 1)
	mp.CFrame = CFrame.new(chunk.ax + cx, chunk.ay + cy, chunk.az + cz)
	if style and style.night then
		mp.Transparency = 1
	end
	Meshes.built += 1
	Meshes.triangles += nt
	return mp
end

return Meshes
