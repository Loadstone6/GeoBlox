--!strict
-- Test car built from parts: a London hatchback at true scale (4.3 m long,
-- 1 stud = 0.3 m). Physics runs on the driver's client (src/client/Drive.luau)
-- as raycast suspension. One invisible chassis box carries mass and
-- collisions, and each wheel applies its spring and tyre forces through a
-- VectorForce at its suspension mount. Raycast wheels ride over seams between
-- parts and terrain without snagging, which suits a part-built city.

local Config = require(script.Parent.Config)

local Car = {}

local C = Config.Car
local rgb = Color3.fromRGB

local PAINT = {
	rgb(180, 24, 30),
	rgb(28, 54, 112),
	rgb(236, 236, 232),
	rgb(36, 38, 42),
	rgb(46, 102, 72),
	rgb(196, 160, 60),
	rgb(130, 136, 142),
}

Car.WHEELS = {
	{ name = "FL", x = -C.track / 2, z = -C.wheelbase / 2, front = true },
	{ name = "FR", x = C.track / 2, z = -C.wheelbase / 2, front = true },
	{ name = "RL", x = -C.track / 2, z = C.wheelbase / 2, front = false },
	{ name = "RR", x = C.track / 2, z = C.wheelbase / 2, front = false },
}
Car.MOUNT_Y = -0.3

local function part(
	model: Model,
	name: string,
	size: Vector3,
	at: CFrame,
	chassis: BasePart?,
	color: Color3,
	material: Enum.Material?
): Part
	local p = Instance.new("Part")
	p.Name = name
	p.Size = size
	p.CFrame = at
	p.Color = color
	p.Material = material or Enum.Material.SmoothPlastic
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	p.CanCollide = false
	p.CanQuery = false
	p.CanTouch = false
	p.Massless = true
	p.Parent = model
	if chassis then
		local w = Instance.new("WeldConstraint")
		w.Part0 = chassis
		w.Part1 = p
		w.Parent = p
	end
	return p
end

-- Build a car whose chassis centre sits at `at` (wheels on the ground below).
function Car.build(at: CFrame, paintIndex: number?): Model
	local model = Instance.new("Model")
	model.Name = "GeoBloxCar"
	local paint = PAINT[((paintIndex or math.random(1, #PAINT)) - 1) % #PAINT + 1]

	local chassis = Instance.new("Part")
	chassis.Name = "Chassis"
	chassis.Size = Vector3.new(C.width - 0.4, 1.2, C.length - 1.0)
	chassis.CFrame = at
	chassis.Transparency = 1
	chassis.CanCollide = true
	chassis.CanTouch = false
	chassis.CustomPhysicalProperties = PhysicalProperties.new(2.0, 0.25, 0.0, 1, 1)
	chassis.Parent = model
	model.PrimaryPart = chassis

	local function rel(x: number, y: number, z: number): CFrame
		return at * CFrame.new(x, y, z)
	end
	local w, L = C.width, C.length
	-- Body: lower shell, bonnet and boot slopes, cabin glass, roof.
	part(model, "Body", Vector3.new(w, 1.6, L), rel(0, -0.7, 0), chassis, paint, Enum.Material.SmoothPlastic)
	local bonnet = Instance.new("WedgePart")
	bonnet.Name = "Bonnet"
	bonnet.Size = Vector3.new(w - 0.1, 0.5, 3.6)
	bonnet.CFrame = rel(0, 0.35, -L / 2 + 2.0)
	bonnet.Color = paint
	bonnet.CanCollide = false
	bonnet.CanQuery = false
	bonnet.CanTouch = false
	bonnet.Massless = true
	bonnet.Parent = model
	local wb = Instance.new("WeldConstraint")
	wb.Part0 = chassis
	wb.Part1 = bonnet
	wb.Parent = bonnet
	part(
		model,
		"Cabin",
		Vector3.new(w - 0.5, 1.55, 6.4),
		rel(0, 0.85, 0.9),
		chassis,
		rgb(40, 52, 62),
		Enum.Material.Glass
	).Reflectance =
		0.25
	part(model, "Roof", Vector3.new(w - 0.45, 0.22, 5.4), rel(0, 1.7, 1.1), chassis, paint)
	part(model, "Pillars", Vector3.new(w - 0.42, 1.5, 0.35), rel(0, 0.85, -2.2), chassis, paint)
	part(model, "Tailgate", Vector3.new(w - 0.42, 1.5, 0.3), rel(0, 0.85, 4.0), chassis, paint)
	part(model, "Bumpers", Vector3.new(w + 0.1, 0.5, L + 0.2), rel(0, -1.3, 0), chassis, rgb(30, 30, 32))
	for _, sx in { -1, 1 } do
		local head = part(
			model,
			"Headlight",
			Vector3.new(1.1, 0.4, 0.1),
			rel(sx * (w / 2 - 0.9), -0.3, -L / 2 - 0.02),
			chassis,
			rgb(255, 250, 236),
			Enum.Material.Neon
		)
		local spot = Instance.new("SpotLight")
		spot.Brightness = 3
		spot.Range = 60
		spot.Angle = 55
		spot.Face = Enum.NormalId.Front
		spot.Color = rgb(255, 244, 220)
		spot.Enabled = false
		spot.Parent = head
		part(
			model,
			"TailLight",
			Vector3.new(0.9, 0.5, 0.1),
			rel(sx * (w / 2 - 0.8), -0.2, L / 2 + 0.02),
			chassis,
			rgb(150, 10, 10),
			Enum.Material.Neon
		)
	end
	part(model, "Plate", Vector3.new(1.6, 0.35, 0.08), rel(0, -0.85, -L / 2 - 0.05), chassis, rgb(240, 240, 236))
	part(model, "RearPlate", Vector3.new(1.6, 0.35, 0.08), rel(0, -0.85, L / 2 + 0.05), chassis, rgb(236, 196, 40))

	-- Wheels (visual; the client poses them from the suspension rays).
	for _, wh in Car.WHEELS do
		local wheel = Instance.new("Part")
		wheel.Name = "Wheel" .. wh.name
		wheel.Shape = Enum.PartType.Cylinder
		wheel.Size = Vector3.new(C.wheelWidth, C.wheelRadius * 2, C.wheelRadius * 2)
		wheel.Color = rgb(24, 24, 26)
		wheel.Material = Enum.Material.Rubber
		wheel.CanCollide = false
		wheel.CanQuery = false
		wheel.CanTouch = false
		wheel.Massless = true
		local rest = CFrame.new(wh.x, Car.MOUNT_Y - (C.restLength - C.sag), wh.z)
		wheel.CFrame = at * rest
		wheel.Parent = model
		local hub = Instance.new("Part")
		hub.Name = "Hub"
		hub.Shape = Enum.PartType.Cylinder
		hub.Size = Vector3.new(C.wheelWidth + 0.04, C.wheelRadius * 1.15, C.wheelRadius * 1.15)
		hub.Color = rgb(170, 172, 176)
		hub.Material = Enum.Material.Metal
		hub.CanCollide = false
		hub.CanQuery = false
		hub.CanTouch = false
		hub.Massless = true
		hub.CFrame = wheel.CFrame
		hub.Parent = wheel
		local hw = Instance.new("WeldConstraint")
		hw.Part0 = wheel
		hw.Part1 = hub
		hw.Parent = hub
		local weld = Instance.new("Weld")
		weld.Name = "Pose"
		weld.Part0 = chassis
		weld.Part1 = wheel
		weld.C0 = rest
		weld.Parent = wheel

		local mount = Instance.new("Attachment")
		mount.Name = "Mount" .. wh.name
		mount.Position = Vector3.new(wh.x, Car.MOUNT_Y, wh.z)
		mount.Parent = chassis
		local force = Instance.new("VectorForce")
		force.Name = "Force" .. wh.name
		force.Attachment0 = mount
		force.RelativeTo = Enum.ActuatorRelativeTo.World
		force.ApplyAtCenterOfMass = false
		force.Force = Vector3.zero
		force.Parent = chassis
	end
	local centre = Instance.new("Attachment")
	centre.Name = "Centre"
	centre.Parent = chassis
	local aero = Instance.new("VectorForce")
	aero.Name = "Aero"
	aero.Attachment0 = centre
	aero.RelativeTo = Enum.ActuatorRelativeTo.World
	aero.ApplyAtCenterOfMass = true
	aero.Force = Vector3.zero
	aero.Parent = chassis

	local seat = Instance.new("VehicleSeat")
	seat.Name = "DriveSeat"
	seat.Size = Vector3.new(1.6, 0.5, 1.6)
	seat.CFrame = rel(1.25, -0.05, 0.9) -- right-hand drive
	seat.Transparency = 1
	seat.CanCollide = false
	seat.Massless = true
	seat.MaxSpeed = 0
	seat.Torque = 0
	seat.TurnSpeed = 0
	seat.HeadsUpDisplay = false
	seat.Parent = model
	local sw = Instance.new("WeldConstraint")
	sw.Part0 = chassis
	sw.Part1 = seat
	sw.Parent = seat

	model:AddTag("GeoBloxCar")
	model.ModelStreamingMode = Enum.ModelStreamingMode.Atomic
	return model
end

-- Height of the chassis centre above the road when the car is at rest.
function Car.restHeight(): number
	return -(Car.MOUNT_Y - (C.restLength - C.sag)) + C.wheelRadius
end

return Car
