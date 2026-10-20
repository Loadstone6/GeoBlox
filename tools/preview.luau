--!nocheck
-- Renders a top-down preview of the generated world (1 px = 1 voxel column)
-- to build/preview.ppm. Usage: lune run tools/preview [scale]

local fs = require("@lune/fs")
local process = require("@lune/process")
local Harness = require("../tests/harness")

local Config = Harness.require("src/shared/Config")
local Layout = Harness.require("src/shared/Layout")
local Sampler = Harness.require("src/shared/Sampler")
local C = Harness.require("src/shared/SurfaceClass")

local STEP = tonumber(process.args[1]) or 4
local L = Layout.build(Config.SEED)
local smp = Sampler.new(L)

local COLORS = {
	[C.Pasture] = { 92, 130, 52 },
	[C.Maize] = { 128, 112, 76 },
	[C.Wheat] = { 112, 138, 62 },
	[C.Potato] = { 120, 96, 68 },
	[C.Tulip] = { 206, 38, 52 },
	[C.Daffodil] = { 236, 198, 44 },
	[C.Hyacinth] = { 118, 92, 196 },
	[C.Plough] = { 112, 86, 60 },
	[C.Road] = { 64, 64, 66 },
	[C.Verge] = { 100, 136, 58 },
	[C.DikeSlope] = { 96, 132, 56 },
	[C.Bank] = { 86, 120, 50 },
	[C.BankWet] = { 74, 64, 48 },
	[C.Yard] = { 156, 154, 146 },
	[C.Drive] = { 146, 82, 62 },
	[C.Lawn] = { 100, 140, 60 },
	[C.Track] = { 136, 130, 118 },
	[C.Mound] = { 96, 134, 56 },
	[C.Headland] = { 100, 136, 58 },
}

local W = (Config.X_MAX - Config.X_MIN) // STEP
local H = (Config.Z_MAX - Config.Z_MIN) // STEP
local px = table.create(W * H * 3, 0)
local heights = table.create(W * H, 0)

for j = 0, H - 1 do
	for i = 0, W - 1 do
		local x = Config.X_MIN + (i + 0.5) * STEP
		local z = Config.Z_MAX - (j + 0.5) * STEP -- north (+z) at the top
		local h, cls, water = smp:sample(x, z)
		heights[j * W + i + 1] = h
		local c = COLORS[cls] or { 255, 0, 255 }
		local r, g, b = c[1], c[2], c[3]
		if water > 0 and h < water then
			r, g, b = 52, 76, 84
		end
		local o = (j * W + i) * 3
		px[o + 1], px[o + 2], px[o + 3] = r, g, b
	end
end

-- Hillshade from the north-west.
for j = 1, H - 2 do
	for i = 1, W - 2 do
		local dzdx = heights[j * W + i + 2] - heights[j * W + i]
		local dzdy = heights[(j + 1) * W + i + 1] - heights[(j - 1) * W + i + 1]
		local shade = math.clamp(1 + (-dzdx + dzdy) * 0.08, 0.6, 1.3)
		local o = (j * W + i) * 3
		for k = 1, 3 do
			px[o + k] = math.clamp(math.floor(px[o + k] * shade), 0, 255)
		end
	end
end

local function dot(x, z, radius, color)
	local ci = (x - Config.X_MIN) / STEP
	local cj = (Config.Z_MAX - z) / STEP
	local r = math.max(0.5, radius / STEP)
	for j = math.floor(cj - r), math.ceil(cj + r) do
		for i = math.floor(ci - r), math.ceil(ci + r) do
			if i >= 0 and i < W and j >= 0 and j < H and (i - ci) ^ 2 + (j - cj) ^ 2 <= r * r then
				local o = (j * W + i) * 3
				px[o + 1], px[o + 2], px[o + 3] = color[1], color[2], color[3]
			end
		end
	end
end

local function rect(farm, b, color)
	for ls = b.ls - b.w / 2, b.ls + b.w / 2, STEP / 2 do
		for d = b.d - b.dep / 2, b.d + b.dep / 2, STEP / 2 do
			local x, z = Layout.toWorld(farm.s + ls, farm.side, d)
			dot(x, z, 0.1, color)
		end
	end
end

for _, f in L.farmList do
	for _, b in f.buildings do
		rect(
			f,
			b,
			if b.kind == "steelshed"
					or b.kind == "silo"
					or b.kind == "mestsilo"
				then { 150, 156, 150 }
				else { 170, 64, 46 }
		)
	end
end
for _, t in L.trees do
	dot(
		t.x,
		t.z,
		if t.kind == "willow" then 4 else t.h * 0.12,
		if t.kind == "willow" then { 120, 140, 96 } else { 34, 66, 30 }
	)
end
for _, herd in L.herds do
	local x, z = Layout.toWorld((herd.s0 + herd.s1) / 2, herd.side, (herd.d0 + herd.d1) / 2)
	dot(x, z, 6, if herd.kind == "cow" then { 250, 250, 250 } else { 230, 220, 190 })
end
local mx, mz = Layout.toWorld(L.windmill.s, L.windmill.side, L.windmill.d)
dot(mx, mz, 14, { 90, 70, 50 })

local parts = { string.format("P6\n%d %d\n255\n", W, H) }
local buf = table.create(W * H * 3)
for k = 1, #px do
	buf[k] = string.char(px[k])
end
table.insert(parts, table.concat(buf))
fs.writeDir("build")
fs.writeFile("build/preview.ppm", table.concat(parts))
print(string.format("wrote build/preview.ppm (%dx%d)", W, H))
