--!strict
-- Driver-side car physics (the server hands the driver network ownership).
--
-- Each wheel casts a ray from its suspension mount. The spring-damper force
-- (stiffness from the static sag, damping ratio from Config) acts along the
-- hit normal. Tyre forces act in the contact plane: lateral force cancels
-- side slip within a few frames, and longitudinal force is drive, brake or
-- rolling resistance. Both are capped by grip times the wheel's share of
-- the weight, scaled by its momentary load (a friction circle per wheel).
-- Forces go to VectorForces at the mounts, which the physics integrates
-- between frames.
--
-- Forces are set once per frame but integrated at 240 Hz, so a plain spring
-- goes unstable below ~25 fps. The suspension force is therefore the
-- backward-Euler solution over the frame time, gravity included (static sag
-- stays exact). Stiffness is limited so omega * dt <= 1.1, and tyre gains
-- scale with dt. Simulated stable from 60 fps down to 8 fps (it sinks onto
-- the chassis instead of bouncing).
--
-- Inputs come from the VehicleSeat (WASD/arrows, gamepad, touch thumbstick);
-- LeftShift or ButtonB is the handbrake.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local Shared = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared")
local Car = require(Shared:WaitForChild("Car"))
local Config = require(Shared:WaitForChild("Config"))

local Drive = {}

local C = Config.Car
local player = Players.LocalPlayer

Drive.speed = 0 -- studs/s along the car, for the HUD
Drive.car = nil :: Model?
Drive.headlights = false

type Wheel = {
	front: boolean,
	x: number,
	z: number,
	force: VectorForce,
	weld: Weld,
	comp: number,
	spin: number,
}

local active: RBXScriptConnection? = nil

local function stop()
	if active then
		active:Disconnect()
		active = nil
	end
	Drive.car = nil
	Drive.speed = 0
end

local function sign(x: number): number
	return if x > 0 then 1 elseif x < 0 then -1 else 0
end

local function control(model: Model, seat: VehicleSeat)
	stop()
	local chassis = model:WaitForChild("Chassis") :: BasePart
	local aero = chassis:WaitForChild("Aero") :: VectorForce
	local wheels: { Wheel } = {}
	for _, def in Car.WHEELS do
		local wheelPart = model:WaitForChild("Wheel" .. def.name) :: BasePart
		table.insert(wheels, {
			front = def.front,
			x = def.x,
			z = def.z,
			force = chassis:WaitForChild("Force" .. def.name) :: VectorForce,
			weld = wheelPart:WaitForChild("Pose") :: Weld,
			comp = C.sag,
			spin = 0,
		})
	end
	local tails: { BasePart } = {}
	local heads: { SpotLight } = {}
	for _, d in model:GetDescendants() do
		if d:IsA("BasePart") and d.Name == "TailLight" then
			table.insert(tails, d)
		elseif d:IsA("SpotLight") then
			table.insert(heads, d)
		end
	end
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	local character = player.Character
	params.FilterDescendantsInstances = { model, character :: Instance } :: { Instance }
	local steer = 0
	Drive.car = model

	active = RunService.PreSimulation:Connect(function(dt: number)
		if not model.Parent or seat.Occupant == nil then
			stop()
			return
		end
		local h = math.clamp(dt, 1 / 240, 1 / 8)
		local mass = chassis.AssemblyMass
		local g = workspace.Gravity
		local share = mass / 4
		local k = math.min(share * g / C.sag, share * (1.1 / h) ^ 2)
		local damp = 2 * C.damping * math.sqrt(k * share)
		local implicit = 1 + h * h * k / share + h * damp / share
		local slip = math.min(1 / 0.06, 0.8 / h) -- per-second tyre gains, capped by frame time
		local stopGain = math.min(1 / 0.08, 0.8 / h)
		local cf = chassis.CFrame
		local up = cf.UpVector
		local fwd = cf.LookVector
		local vel = chassis.AssemblyLinearVelocity
		local speed = vel:Dot(fwd)
		local throttle = seat.ThrottleFloat
		local handbrake = UserInputService:IsKeyDown(Enum.KeyCode.LeftShift)
			or UserInputService:IsGamepadButtonDown(Enum.UserInputType.Gamepad1, Enum.KeyCode.ButtonB)
		local frac = math.clamp(math.abs(speed) / C.topSpeed, 0, 1)
		local lock = C.maxSteer + (C.steerAtSpeed - C.maxSteer) * frac
		steer += (seat.SteerFloat * lock - steer) * math.min(1, h * 7)
		local braking = false
		local maxLen = C.restLength + C.wheelRadius
		for _, w in wheels do
			local mountW = cf:PointToWorldSpace(Vector3.new(w.x, Car.MOUNT_Y, w.z))
			local hit = workspace:Raycast(mountW, -up * maxLen, params)
			local dir = if w.front then (cf * CFrame.Angles(0, -steer, 0)).LookVector else fwd
			local comp = 0
			local force = Vector3.zero
			if hit then
				comp = math.max(0, maxLen - (hit.Position - mountW):Dot(-up))
				-- Backward Euler over the frame: predicted rise speed, then force.
				local rise = (-(comp - w.comp) / h + (h / share) * k * comp - h * g * math.max(up.Y, 0)) / implicit
				local fs = math.clamp(k * comp - (k * h + damp) * rise, 0, 4 * share * g)
				local n = hit.Normal
				local f = dir - n * dir:Dot(n)
				if f.Magnitude > 1e-4 then
					f = f.Unit
					local r = f:Cross(n)
					local v = chassis:GetVelocityAtPosition(hit.Position)
					local vLong, vLat = v:Dot(f), v:Dot(r)
					local load = math.clamp(fs / (share * g), 0, 1.8)
					local grip = (if handbrake and not w.front then C.handbrakeGrip else C.grip) * share * load
					local fLat = math.clamp(-vLat * share * slip, -grip, grip)
					local fLong = 0
					if handbrake and not w.front then
						fLong = -sign(vLong) * math.min(math.abs(vLong) * share * stopGain, C.brake * share)
					elseif throttle ~= 0 and speed * throttle < -1.5 then
						fLong = -sign(vLong) * math.min(math.abs(vLong) * share * stopGain, C.brake * share)
						braking = true
					elseif throttle ~= 0 and not w.front then
						local power = if throttle > 0 then 1 - frac else 0.45 * (1 - math.clamp(-speed / 35, 0, 1))
						fLong = throttle * C.accel * (mass / 2) * power
					elseif math.abs(speed) < 3 then
						fLong = -vLong * share * math.min(1 / 0.12, 0.8 / h) -- hold on hills when stopped
					else
						fLong = -vLong * C.rollResist * share * 0.05
					end
					-- Friction circle.
					local cap = C.brake * share * math.max(load, 0.2)
					local total = math.sqrt(fLat * fLat + fLong * fLong)
					if total > cap and total > 0 then
						fLat *= cap / total
						fLong *= cap / total
					end
					force = n * fs + r * fLat + f * fLong
				else
					force = n * fs
				end
			end
			w.comp = comp
			w.force.Force = force
			w.spin = (w.spin - speed / C.wheelRadius * h) % (math.pi * 2)
			local drop = if hit then C.restLength - comp else C.restLength
			w.weld.C0 = CFrame.new(w.x, Car.MOUNT_Y - drop, w.z)
				* CFrame.Angles(0, if w.front then -steer else 0, 0)
				* CFrame.Angles(w.spin, 0, 0)
		end
		-- Aerodynamic drag sized so drive force balances it at top speed.
		local vm = vel.Magnitude
		aero.Force = if vm > 0.1 then -vel * vm * (C.accel * mass * 0.5 / (C.topSpeed * C.topSpeed)) else Vector3.zero
		for _, t in tails do
			t.Color = if braking then Color3.fromRGB(255, 30, 30) else Color3.fromRGB(150, 10, 10)
		end
		for _, lamp in heads do
			lamp.Enabled = Drive.headlights
		end
		Drive.speed = speed
	end)
end

function Drive.start()
	local function hook(character: Model)
		local humanoid = character:WaitForChild("Humanoid") :: Humanoid
		humanoid.Seated:Connect(function(active: boolean, seatPart: BasePart?)
			if active and seatPart and seatPart:IsA("VehicleSeat") and seatPart.Name == "DriveSeat" then
				local model = seatPart.Parent
				if model and model:IsA("Model") and model:HasTag("GeoBloxCar") then
					control(model, seatPart)
				end
			else
				stop()
			end
		end)
	end
	if player.Character then
		task.spawn(hook, player.Character)
	end
	player.CharacterAdded:Connect(hook)
end

-- Ask the server for a fresh car here, facing `look`.
function Drive.respawn(position: Vector3, look: Vector3)
	local remote = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("CarRequest") :: RemoteEvent
	remote:FireServer(position, look)
end

return Drive
