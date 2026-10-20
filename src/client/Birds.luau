--!strict
-- A starling flock (boids) that roams over the fields near the camera, plus a
-- pair of gulls gliding in wide circles. All parts move in one BulkMoveTo call.

local RunService = game:GetService("RunService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Geometry = require(Shared.Geometry)
local Rng = require(Shared.Rng)

local V3 = Vector3.new
local rgb = Color3.fromRGB

type Bird = {
	pos: Vector3,
	vel: Vector3,
	phase: number,
	flapRate: number,
	span: number,
	body: BasePart,
	wingL: BasePart,
	wingR: BasePart,
	glide: boolean,
}

local Birds = {}

local FLOCK = 36
local MAX_SPEED = 46

local function makeBird(parent: Instance, color: Color3, span: number): (BasePart, BasePart, BasePart)
	local body = Geometry.block(parent, V3(0.45, 0.35, 1.2) * span, CFrame.new(), color)
	local wingL = Geometry.block(parent, V3(1.3, 0.08, 0.55) * span, CFrame.new(), color)
	local wingR = Geometry.block(parent, V3(1.3, 0.08, 0.55) * span, CFrame.new(), color)
	for _, p in { body, wingL, wingR } do
		p.CanCollide = false
		p.CanQuery = false
		p.CastShadow = false
	end
	return body, wingL, wingR
end

function Birds.start()
	local folder = Instance.new("Folder")
	folder.Name = "GeoBloxBirds"
	folder.Parent = workspace
	local rng = Rng.new(Config.SEED + 5)

	local birds: { Bird } = {}
	local home = V3(Config.SPAWN_S + 200, Config.FIELD_Y + 80, 250)
	for i = 1, FLOCK + 2 do
		local glide = i > FLOCK
		local span = if glide then 3 else 1
		local color = if glide then rgb(236, 236, 232) else rgb(28, 28, 32)
		local body, wingL, wingR = makeBird(folder, color, span)
		table.insert(birds, {
			pos = home + V3(rng:range(-30, 30), rng:range(-10, 10), rng:range(-30, 30)) + (if glide
				then V3(0, 60, 0)
				else Vector3.zero),
			vel = V3(rng:range(-1, 1), 0, rng:range(-1, 1)) * 20,
			phase = rng:range(0, 6.28),
			flapRate = if glide then 3 else rng:range(13, 17),
			span = span,
			body = body,
			wingL = wingL,
			wingR = wingR,
			glide = glide,
		})
	end

	local attractor = home
	local wanderT = 0
	local parts: { BasePart } = {}
	local cframes: { CFrame } = {}
	for _, b in birds do
		table.insert(parts, b.body)
		table.insert(parts, b.wingL)
		table.insert(parts, b.wingR)
	end

	RunService.Heartbeat:Connect(function(dt)
		dt = math.min(dt, 0.05)
		local camera = workspace.CurrentCamera
		if not camera then
			return
		end
		wanderT += dt
		-- The flock's goal drifts around the camera, over the fields.
		local cam = camera.CFrame.Position
		local goal = V3(
			cam.X + math.sin(wanderT * 0.05) * 320,
			Config.FIELD_Y + 70 + math.sin(wanderT * 0.13) * 30,
			cam.Z + 260 + math.cos(wanderT * 0.07) * 220
		)
		attractor = attractor:Lerp(goal, math.min(1, dt * 0.25))

		table.clear(cframes)
		for i, b in birds do
			local steer = Vector3.zero
			if b.glide then
				-- Gulls: slow circles above the flock.
				local center = attractor + V3(0, 70, 0)
				local tangent = V3(-(b.pos.Z - center.Z), 0, b.pos.X - center.X)
				if tangent.Magnitude > 0 then
					steer = tangent.Unit * 16 + (center - b.pos) * 0.05
				end
			else
				local sep, ali, coh = Vector3.zero, Vector3.zero, Vector3.zero
				local n = 0
				for j = 1, FLOCK do
					if j ~= i then
						local o = birds[j]
						local off = o.pos - b.pos
						local dist = off.Magnitude
						if dist < 22 then
							n += 1
							ali += o.vel
							coh += o.pos
							if dist < 4 and dist > 0 then
								sep -= off.Unit * (4 - dist) * 3
							end
						end
					end
				end
				if n > 0 then
					ali = ali / n - b.vel
					coh = coh / n - b.pos
				end
				steer = sep * 2.2 + ali * 0.6 + coh * 0.5 + (attractor - b.pos) * 0.12
			end
			local vel = b.vel + steer * dt
			local speed = vel.Magnitude
			local maxSpeed = if b.glide then 22 else MAX_SPEED
			if speed > maxSpeed then
				vel = vel / speed * maxSpeed
			elseif speed < 8 then
				vel = (if speed > 0 then vel / speed else V3(1, 0, 0)) * 8
			end
			b.vel = vel
			b.pos += vel * dt
			if b.pos.Y < Config.FIELD_Y + 12 then
				b.pos = V3(b.pos.X, Config.FIELD_Y + 12, b.pos.Z)
			end

			b.phase += dt * b.flapRate
			local flap = if b.glide then math.sin(b.phase) * 0.12 else math.sin(b.phase) * 0.9
			local cf = CFrame.lookAt(b.pos, b.pos + vel)
			local half = 0.6 * b.span
			table.insert(cframes, cf)
			table.insert(
				cframes,
				cf * CFrame.new(-0.2 * b.span, 0, 0) * CFrame.Angles(0, 0, flap) * CFrame.new(-half, 0, 0)
			)
			table.insert(
				cframes,
				cf * CFrame.new(0.2 * b.span, 0, 0) * CFrame.Angles(0, 0, -flap) * CFrame.new(half, 0, 0)
			)
		end
		workspace:BulkMoveTo(parts, cframes, Enum.BulkMoveMode.FireCFrameChanged)
	end)
end

return Birds
