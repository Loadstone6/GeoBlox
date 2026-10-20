--!strict
-- Test cars: one per player, spawned on the road and handed to the driver's
-- client for physics (network ownership). A parked car is anchored, so it
-- never sinks onto its chassis while nobody drives it.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Shared")
local Car = require(Shared:WaitForChild("Car"))

local Cars = {}

local folder = Instance.new("Folder")
folder.Name = "Cars"
folder.Parent = workspace

local request = Instance.new("RemoteEvent")
request.Name = "CarRequest"
request.Parent = ReplicatedStorage:WaitForChild("GeoBlox")

local owned: { [Player]: Model } = {}

local function groundBelow(p: Vector3): Vector3?
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	local ignore: { Instance } = { folder }
	for _, pl in Players:GetPlayers() do
		if pl.Character then
			table.insert(ignore, pl.Character)
		end
	end
	params.FilterDescendantsInstances = ignore
	local hit = workspace:Raycast(p + Vector3.new(0, 40, 0), Vector3.new(0, -120, 0), params)
	return if hit then hit.Position else nil
end

function Cars.spawn(player: Player, position: Vector3, look: Vector3): Model?
	local old = owned[player]
	if old then
		old:Destroy()
		owned[player] = nil
	end
	local ground = groundBelow(position) or position
	local flat = Vector3.new(look.X, 0, look.Z)
	if flat.Magnitude < 1e-3 then
		flat = Vector3.new(0, 0, -1)
	end
	local centre = ground + Vector3.new(0, Car.restHeight() + 0.3, 0)
	local model = Car.build(CFrame.lookAt(centre, centre + flat.Unit), player.UserId % 7 + 1)
	model.Name = player.Name .. "Car"
	model.Parent = folder
	owned[player] = model
	local chassis = model:FindFirstChild("Chassis") :: BasePart
	local seat = model:FindFirstChild("DriveSeat") :: VehicleSeat
	chassis.Anchored = true
	seat:GetPropertyChangedSignal("Occupant"):Connect(function()
		local occ = seat.Occupant
		if occ then
			local driver = Players:GetPlayerFromCharacter(occ.Parent)
			chassis.Anchored = false
			if driver then
				chassis:SetNetworkOwner(driver)
			end
		else
			chassis.AssemblyLinearVelocity = Vector3.zero
			chassis.AssemblyAngularVelocity = Vector3.zero
			chassis.Anchored = true
		end
	end)
	local character = player.Character
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	if character and humanoid and humanoid.Health > 0 then
		local root = character:FindFirstChild("HumanoidRootPart") :: BasePart?
		if root then
			root.CFrame = seat.CFrame + Vector3.new(0, 3, 0)
		end
		task.defer(function()
			if seat.Parent then
				seat:Sit(humanoid)
			end
		end)
	end
	return model
end

function Cars.start()
	request.OnServerEvent:Connect(function(player: Player, position: any, look: any)
		if typeof(position) ~= "Vector3" or typeof(look) ~= "Vector3" then
			return
		end
		Cars.spawn(player, position, look)
	end)
	Players.PlayerRemoving:Connect(function(player)
		local m = owned[player]
		if m then
			m:Destroy()
			owned[player] = nil
		end
	end)
end

return Cars
