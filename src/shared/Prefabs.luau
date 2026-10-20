--!strict
-- Prefab models built from parts: London street furniture sized from real
-- dimensions, and trees shaped by their LiDAR height and crown radius. These
-- are the "schematics" the world compiler places, like the block models a
-- Minecraft converter uses. tools/bake.luau calls this in Lune to bake them
-- into the place; in Roblox the same code can spawn more at runtime.
--
-- Prefabs.new(api) takes the engine globals (Instance, Vector3, CFrame,
-- Color3, Enum) so it runs in both environments.

local Prefabs = {}

local MPS = 0.3 -- metres per stud
local S = 1 / MPS

type Builder = (any, any, { any }) -> ()

function Prefabs.new(api: any)
	local Instance, V3, CF, Color3, Enum = api.Instance, api.Vector3.new, api.CFrame, api.Color3, api.Enum
	local M = Enum.Material
	local rgb = Color3.fromRGB

	local BLACK = rgb(24, 24, 26)
	local IRON = rgb(32, 34, 34)
	local PO_RED = rgb(176, 18, 22)
	local K6_RED = rgb(186, 24, 26)
	local GALV = rgb(160, 162, 160)
	local GOLD = rgb(190, 152, 70)

	local function part(
		parent: any,
		size: any,
		cf: any,
		color: any,
		material: any?,
		shape: any?,
		collide: boolean?,
		tag: string?
	): any
		local p = Instance.new("Part")
		p.Anchored = true
		p.CanTouch = false
		p.CanCollide = collide == true
		p.CanQuery = collide == true
		if shape then
			p.Shape = shape
		end
		p.Size = size
		p.CFrame = cf
		p.Color = color
		p.Material = material or M.SmoothPlastic
		p.TopSurface = Enum.SurfaceType.Smooth
		p.BottomSurface = Enum.SurfaceType.Smooth
		if tag then
			p:AddTag(tag)
		end
		p.Parent = parent
		return p
	end

	-- Vertical cylinder: height h, diameter d, base centre at cf (studs).
	local function cyl(parent: any, h: number, d: number, cf: any, color: any, material: any?, collide: boolean?): any
		return part(
			parent,
			V3(h, d, d),
			cf * CF.new(0, h / 2, 0) * CF.Angles(0, 0, math.pi / 2),
			color,
			material,
			Enum.PartType.Cylinder,
			collide
		)
	end

	local function ball(parent: any, d: number, cf: any, color: any, material: any?, tag: string?): any
		return part(parent, V3(d, d, d), cf, color, material, Enum.PartType.Ball, false, tag)
	end

	-- Deterministic random numbers (identical in Roblox and Lune).
	local function rng(seed: number): () -> number
		local s = math.floor(seed) % 2147483647
		if s <= 0 then
			s += 2147483646
		end
		return function(): number
			s = (s * 16807) % 2147483647
			return (s - 1) / 2147483646
		end
	end

	local builders: { [string]: Builder } = {}

	-- Westminster lamp column (black cast iron, lantern); variant 1: modern column.
	builders.lamp = function(m, cf, args)
		if args[1] == 1 then
			cyl(m, 6.0 * S, 0.16 * S, cf, rgb(70, 72, 72), M.Metal, true)
			part(m, V3(0.12 * S, 0.12 * S, 1.2 * S), cf * CF.new(0, 6.0 * S, -0.55 * S), rgb(70, 72, 72), M.Metal)
			local head = part(
				m,
				V3(0.35 * S, 0.12 * S, 0.7 * S),
				cf * CF.new(0, 5.95 * S, -1.1 * S),
				rgb(255, 238, 210),
				M.SmoothPlastic,
				nil,
				false,
				"GeoBloxLantern"
			)
			head.CastShadow = false
			return
		end
		cyl(m, 0.5 * S, 0.34 * S, cf, BLACK, M.Metal, true)
		cyl(m, 4.2 * S, 0.15 * S, cf * CF.new(0, 0.5 * S, 0), BLACK, M.Metal, true)
		part(m, V3(0.5 * S, 0.06 * S, 0.5 * S), cf * CF.new(0, 4.6 * S, 0), BLACK, M.Metal)
		local glass = part(
			m,
			V3(0.42 * S, 0.62 * S, 0.42 * S),
			cf * CF.new(0, 4.95 * S, 0),
			rgb(255, 236, 196),
			M.Glass,
			nil,
			false,
			"GeoBloxLantern"
		)
		glass.Transparency = 0.3
		glass.CastShadow = false
		part(m, V3(0.56 * S, 0.1 * S, 0.56 * S), cf * CF.new(0, 5.3 * S, 0), BLACK, M.Metal)
		cyl(m, 0.25 * S, 0.12 * S, cf * CF.new(0, 5.35 * S, 0), GOLD, M.Metal)
	end
	builders.lamp_embankment = builders.lamp

	builders.bench = function(m, cf, _)
		local w = 1.8 * S
		for _, x in { -0.8, 0.8 } do
			part(m, V3(0.06 * S, 0.45 * S, 0.5 * S), cf * CF.new(x * S, 0.22 * S, 0), IRON, M.Metal, nil, true)
			part(m, V3(0.06 * S, 0.5 * S, 0.06 * S), cf * CF.new(x * S, 0.7 * S, 0.24 * S), IRON, M.Metal)
		end
		part(m, V3(w, 0.05 * S, 0.45 * S), cf * CF.new(0, 0.45 * S, 0), rgb(112, 80, 54), M.WoodPlanks, nil, true)
		part(
			m,
			V3(w, 0.4 * S, 0.05 * S),
			cf * CF.new(0, 0.73 * S, 0.25 * S) * CF.Angles(-0.12, 0, 0),
			rgb(112, 80, 54),
			M.WoodPlanks
		)
	end

	builders.bin = function(m, cf, _)
		cyl(m, 1.0 * S, 0.5 * S, cf, BLACK, M.Metal, true)
		cyl(m, 0.08 * S, 0.52 * S, cf * CF.new(0, 0.82 * S, 0), GOLD, M.Metal)
	end

	builders.bollard = function(m, cf, _)
		cyl(m, 0.95 * S, 0.17 * S, cf, BLACK, M.Metal, true)
		cyl(m, 0.06 * S, 0.21 * S, cf * CF.new(0, 0.75 * S, 0), BLACK, M.Metal)
		ball(m, 0.17 * S, cf * CF.new(0, 0.95 * S, 0), BLACK, M.Metal)
	end

	-- Pillar box (red, black base, domed cap).
	builders.post_box = function(m, cf, _)
		cyl(m, 0.12 * S, 0.62 * S, cf, BLACK, M.Metal, true)
		cyl(m, 1.35 * S, 0.55 * S, cf * CF.new(0, 0.12 * S, 0), PO_RED, M.Metal, true)
		cyl(m, 0.08 * S, 0.62 * S, cf * CF.new(0, 1.42 * S, 0), PO_RED, M.Metal)
		ball(m, 0.56 * S, cf * CF.new(0, 1.52 * S, 0), PO_RED, M.Metal)
		part(m, V3(0.3 * S, 0.05 * S, 0.06 * S), cf * CF.new(0, 1.15 * S, -0.27 * S), BLACK)
	end

	-- K6 telephone kiosk (Giles Gilbert Scott, 1935): glazing as one recessed
	-- panel per side behind a red frame.
	builders.phone_k6 = function(m, cf, _)
		local w, h = 0.91 * S, 2.5 * S
		part(m, V3(w, h, w), cf * CF.new(0, h / 2, 0), K6_RED, M.Metal, nil, true)
		for face = 0, 3 do
			local r = cf * CF.Angles(0, face * math.pi / 2, 0)
			if face ~= 3 then
				part(
					m,
					V3(0.66 * S, 1.6 * S, 0.02 * S),
					r * CF.new(0, 1.3 * S, -w / 2 - 0.01),
					rgb(52, 62, 70),
					M.Glass
				)
				for k = 1, 2 do
					part(
						m,
						V3(0.66 * S, 0.03 * S, 0.03 * S),
						r * CF.new(0, (0.5 + k * 0.53) * S, -w / 2 - 0.03),
						K6_RED,
						M.Metal
					)
				end
			end
			local sign =
				part(m, V3(0.62 * S, 0.12 * S, 0.02 * S), r * CF.new(0, 2.25 * S, -w / 2 - 0.02), rgb(240, 236, 220))
			sign.Material = M.Neon
			sign.Transparency = 0.25
		end
		part(m, V3(w * 1.06, 0.12 * S, w * 1.06), cf * CF.new(0, h + 0.06 * S, 0), K6_RED, M.Metal)
		part(
			m,
			V3(0.3 * S, w, w),
			cf * CF.new(0, h + 0.12 * S, 0) * CF.Angles(0, 0, math.pi / 2),
			K6_RED,
			M.Metal,
			Enum.PartType.Cylinder
		)
	end

	builders.bus_stop = function(m, cf, _)
		cyl(m, 2.9 * S, 0.1 * S, cf, rgb(40, 40, 42), M.Metal, true)
		-- Roundel facing the road (cylinder axis along Z).
		part(
			m,
			V3(0.04 * S, 0.5 * S, 0.5 * S),
			cf * CF.new(0.3 * S, 2.6 * S, 0) * CF.Angles(0, math.pi / 2, 0),
			rgb(220, 30, 34),
			M.SmoothPlastic,
			Enum.PartType.Cylinder
		)
		part(m, V3(0.56 * S, 0.1 * S, 0.05 * S), cf * CF.new(0.3 * S, 2.6 * S, 0), rgb(18, 40, 120))
		part(m, V3(0.3 * S, 0.5 * S, 0.06 * S), cf * CF.new(0, 1.5 * S, 0.06 * S), rgb(236, 236, 230))
	end

	builders.shelter = function(m, cf, args)
		builders.bus_stop(m, cf * CF.new(-2.2 * S, 0, 0), args)
		local w, d, h = 3.6 * S, 1.3 * S, 2.5 * S
		part(m, V3(w, 0.06 * S, d), cf * CF.new(0, h, 0.2 * S), rgb(60, 64, 66), M.Metal)
		local back = part(m, V3(w, h, 0.03 * S), cf * CF.new(0, h / 2, 0.8 * S), rgb(170, 190, 200), M.Glass, nil, true)
		back.Transparency = 0.5
		for _, x in { -w / 2, w / 2 } do
			part(m, V3(0.06 * S, h, 0.06 * S), cf * CF.new(x, h / 2, 0.8 * S), rgb(60, 64, 66), M.Metal, nil, true)
		end
		part(m, V3(w * 0.6, 0.06 * S, 0.3 * S), cf * CF.new(0, 0.5 * S, 0.62 * S), rgb(60, 64, 66), M.Metal)
		local ad = part(
			m,
			V3(1.2 * S, 1.8 * S, 0.1 * S),
			cf * CF.new(w / 2 - 0.6 * S, 1.2 * S, 0.8 * S),
			rgb(240, 238, 230),
			M.SmoothPlastic,
			nil,
			false,
			"GeoBloxShopfront"
		)
		ad.Transparency = 0.2
	end

	-- Traffic signal: three-aspect head with backboard; lamps tagged for the
	-- client's signal cycle.
	builders.signal = function(m, cf, _)
		cyl(m, 3.1 * S, 0.13 * S, cf, BLACK, M.Metal, true)
		local head = cf * CF.new(0, 2.75 * S, -0.12 * S)
		part(m, V3(0.42 * S, 1.05 * S, 0.04 * S), head * CF.new(0, 0, 0.1 * S), rgb(250, 250, 250))
		part(m, V3(0.3 * S, 0.9 * S, 0.22 * S), head, BLACK, M.Metal)
		local tags = { "GeoBloxSignalRed", "GeoBloxSignalAmber", "GeoBloxSignalGreen" }
		local cols = { rgb(255, 40, 30), rgb(255, 160, 20), rgb(40, 255, 110) }
		for i = 1, 3 do
			local l = part(
				m,
				V3(0.04 * S, 0.18 * S, 0.18 * S),
				head * CF.new(0, (2 - i) * 0.27 * S, -0.12 * S) * CF.Angles(0, math.pi / 2, 0),
				cols[i],
				M.Neon,
				Enum.PartType.Cylinder,
				false,
				tags[i]
			)
			l.CastShadow = false
		end
		part(m, V3(0.28 * S, 0.5 * S, 0.18 * S), cf * CF.new(0, 2.0 * S, 0.14 * S), BLACK, M.Metal)
	end

	-- Belisha beacon (black/white pole, flashing amber globe).
	builders.belisha = function(m, cf, _)
		for k = 0, 3 do
			cyl(
				m,
				0.6 * S,
				0.12 * S,
				cf * CF.new(0, k * 0.6 * S, 0),
				if k % 2 == 0 then BLACK else rgb(240, 240, 236),
				M.SmoothPlastic,
				k == 0
			)
		end
		local globe = ball(m, 0.36 * S, cf * CF.new(0, 2.6 * S, 0), rgb(255, 170, 30), M.Neon, "GeoBloxBeacon")
		globe.CastShadow = false
	end

	-- Sheffield cycle stand.
	builders.cycle_stand = function(m, cf, _)
		for _, x in { -0.35, 0.35 } do
			part(m, V3(0.06 * S, 0.8 * S, 0.06 * S), cf * CF.new(x * S, 0.4 * S, 0), GALV, M.Metal, nil, true)
		end
		part(m, V3(0.76 * S, 0.06 * S, 0.06 * S), cf * CF.new(0, 0.8 * S, 0), GALV, M.Metal)
	end

	-- Cycle-hire docking station with a few red bikes.
	builders.cycle_hire = function(m, cf, _)
		part(m, V3(0.4 * S, 2.0 * S, 0.3 * S), cf * CF.new(-2.6 * S, 1.0 * S, 0), rgb(70, 72, 76), M.Metal, nil, true)
		part(m, V3(4.6 * S, 0.12 * S, 0.12 * S), cf * CF.new(0, 0.85 * S, 0), rgb(70, 72, 76), M.Metal, nil, true)
		for k = 0, 2 do
			local bike = cf * CF.new((-1.6 + k * 1.5) * S, 0, -0.9 * S) * CF.Angles(0, math.pi / 2, 0)
			for _, wx in { -0.5, 0.5 } do
				part(
					m,
					V3(0.05 * S, 0.66 * S, 0.66 * S),
					bike * CF.new(wx * S, 0.33 * S, 0) * CF.Angles(0, math.pi / 2, 0),
					BLACK,
					M.Rubber,
					Enum.PartType.Cylinder
				)
			end
			part(
				m,
				V3(0.95 * S, 0.08 * S, 0.06 * S),
				bike * CF.new(0, 0.62 * S, 0) * CF.Angles(0, 0, 0.2),
				rgb(200, 30, 30),
				M.Metal
			)
		end
	end

	-- Memorial / statue on a granite plinth.
	builders.statue = function(m, cf, args)
		local variant = args[1] or 0
		local granite = rgb(124, 122, 118)
		local bronze = rgb(64, 58, 44)
		part(m, V3(2.2 * S, 0.4 * S, 2.2 * S), cf * CF.new(0, 0.2 * S, 0), granite, M.Granite, nil, true)
		part(m, V3(1.6 * S, 2.0 * S, 1.6 * S), cf * CF.new(0, 1.4 * S, 0), granite, M.Granite, nil, true)
		part(m, V3(1.9 * S, 0.25 * S, 1.9 * S), cf * CF.new(0, 2.5 * S, 0), granite, M.Granite)
		local top = cf * CF.new(0, 2.62 * S, 0)
		local h = (2.3 + (variant % 3) * 0.3) * S
		part(m, V3(0.55 * S, h * 0.45, 0.4 * S), top * CF.new(0, h * 0.22, 0), bronze, M.Metal)
		part(m, V3(0.7 * S, h * 0.38, 0.45 * S), top * CF.new(0, h * 0.62, 0), bronze, M.Metal)
		ball(m, 0.32 * S, top * CF.new(0, h * 0.88, 0), bronze, M.Metal)
		part(
			m,
			V3(0.18 * S, h * 0.32, 0.18 * S),
			top * CF.new(0.42 * S, h * 0.6, 0) * CF.Angles(0, 0, 0.25),
			bronze,
			M.Metal
		)
	end

	-- Flagpole with a Union flag.
	builders.flagpole = function(m, cf, _)
		cyl(m, 9 * S, 0.14 * S, cf, rgb(240, 240, 236), M.SmoothPlastic, true)
		local f = cf * CF.new(1.1 * S, 8.2 * S, 0)
		part(m, V3(2.0 * S, 1.0 * S, 0.02 * S), f, rgb(18, 34, 110), M.Fabric)
		part(m, V3(2.0 * S, 0.3 * S, 0.03 * S), f, rgb(240, 240, 240), M.Fabric)
		part(m, V3(0.36 * S, 1.0 * S, 0.03 * S), f, rgb(240, 240, 240), M.Fabric)
		part(m, V3(2.0 * S, 0.16 * S, 0.04 * S), f, rgb(200, 20, 40), M.Fabric)
		part(m, V3(0.2 * S, 1.0 * S, 0.04 * S), f, rgb(200, 20, 40), M.Fabric)
	end

	-- "Legible London" wayfinding monolith.
	builders.info = function(m, cf, _)
		part(m, V3(0.65 * S, 2.4 * S, 0.16 * S), cf * CF.new(0, 1.2 * S, 0), rgb(46, 50, 56), M.Metal, nil, true)
		part(m, V3(0.66 * S, 0.22 * S, 0.17 * S), cf * CF.new(0, 2.2 * S, 0), rgb(240, 200, 40), M.SmoothPlastic)
		local map = part(m, V3(0.5 * S, 1.2 * S, 0.02 * S), cf * CF.new(0, 1.3 * S, -0.09 * S), rgb(236, 236, 230))
		map.Material = M.Neon
		map.Transparency = 0.4
	end

	builders.phone_modern = function(m, cf, _)
		part(m, V3(0.9 * S, 2.3 * S, 0.5 * S), cf * CF.new(0, 1.15 * S, 0), rgb(54, 58, 64), M.Metal, nil, true)
		local screen = part(m, V3(0.7 * S, 1.2 * S, 0.02 * S), cf * CF.new(0, 1.3 * S, -0.26 * S), rgb(200, 220, 236))
		screen.Material = M.Neon
		screen.Transparency = 0.45
	end

	-- Trees: trunk plus a cluster of leaf balls sized to the measured crown.
	local LEAF = {
		plane = { rgb(88, 112, 52), rgb(100, 122, 58), rgb(78, 104, 48) },
		broadleaf = { rgb(72, 102, 46), rgb(86, 114, 50), rgb(64, 92, 42) },
		conifer = { rgb(44, 70, 44), rgb(52, 78, 48), rgb(40, 64, 40) },
		ornamental = { rgb(104, 128, 60), rgb(116, 138, 66), rgb(96, 120, 56) },
	}
	local BARK = {
		plane = rgb(150, 140, 112),
		broadleaf = rgb(96, 82, 66),
		conifer = rgb(88, 70, 56),
		ornamental = rgb(110, 92, 74),
	}

	local function tree(kind: string)
		return function(m: any, cf: any, args: { any })
			local h, crown, seed = args[1] :: number, args[2] :: number, args[3] :: number
			local rand = rng(seed + 1)
			local hs = h * S
			local r = math.max(1.2, crown) * S
			local pal = LEAF[kind]
			local trunkD = math.clamp(h * 0.022, 0.22, 0.8) * S
			local yaw = rand() * math.pi * 2
			local base: any = cf * CF.Angles(0, yaw, 0)
			if kind == "conifer" then
				cyl(m, hs * 0.5, trunkD, base, BARK[kind], M.Wood, true)
				for k = 0, 2 do
					local d = r * (1.7 - k * 0.45)
					ball(m, d, base * CF.new(0, hs * (0.38 + k * 0.2), 0), pal[k + 1], M.LeafyGrass)
				end
				return
			end
			local trunkH = hs * (if kind == "plane" then 0.42 else 0.38)
			cyl(m, trunkH, trunkD, base, BARK[kind], M.Wood, true)
			local mid = hs * 0.64
			local n = if kind == "ornamental" then 2 else (if kind == "plane" then 5 else 4)
			ball(m, r * 1.35, base * CF.new(0, mid, 0), pal[1], M.LeafyGrass)
			for k = 1, n - 1 do
				local a = k / (n - 1) * math.pi * 2 + rand() * 0.8
				local off = r * (0.45 + rand() * 0.15)
				local d = r * (0.95 + rand() * 0.2)
				local y = mid + (rand() - 0.4) * hs * 0.16
				ball(m, d, base * CF.new(math.cos(a) * off, y, math.sin(a) * off), pal[k % 3 + 1], M.LeafyGrass)
			end
			if kind ~= "ornamental" then
				for k = 0, 1 do
					local a = yaw + k * math.pi + 0.6
					part(
						m,
						V3(trunkD * 0.45, hs * 0.22, trunkD * 0.45),
						base
							* CF.new(math.cos(a) * r * 0.2, trunkH + hs * 0.08, math.sin(a) * r * 0.2)
							* CF.Angles(math.sin(a) * 0.5, 0, -math.cos(a) * 0.5),
						BARK[kind],
						M.Wood
					)
				end
			end
		end
	end
	builders.tree_plane = tree("plane")
	builders.tree_broadleaf = tree("broadleaf")
	builders.tree_conifer = tree("conifer")
	builders.tree_ornamental = tree("ornamental")

	local api_out = {}

	-- Build prefab `name` at position (studs) with a yaw (radians, clockwise
	-- from north as in the pipeline). Returns the Model (not parented).
	function api_out.build(name: string, x: number, y: number, z: number, yaw: number, args: { any }): any?
		local builder = builders[name]
		if not builder then
			return nil
		end
		local m = Instance.new("Model")
		m.Name = name
		local cf = CF.new(x, y, z) * CF.Angles(0, -yaw, 0)
		builder(m, cf, args)
		m.WorldPivot = cf
		return m
	end

	function api_out.names(): { string }
		local out = {}
		for k in builders do
			table.insert(out, k)
		end
		table.sort(out)
		return out
	end

	return api_out
end

return Prefabs
