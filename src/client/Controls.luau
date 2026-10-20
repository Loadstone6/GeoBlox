--!strict
-- Controls:
--   Car          W/S or arrows (or gamepad / touch) to drive, A/D to steer,
--                LeftShift handbrake, Space to get out, C for a new car here,
--                L headlights
--   F            fly (on foot; WASD, E/Space up, Q down, Shift fast)
--   1-6          viewpoints (Westminster Bridge, Parliament Square, Whitehall,
--                Trafalgar Square, South Bank / London Eye, Embankment)
--   T / Y / R    +1 hour / pause time / next weather
--   H            photo mode (hide HUD, depth of field)
-- Streaming: viewpoint jumps request the destination before moving, and a
-- moving car keeps requesting the area ahead of it.

local Lighting = game:GetService("Lighting")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local GeoBlox = ReplicatedStorage:WaitForChild("GeoBlox")
local Manifest = require(GeoBlox:WaitForChild("Generated"):WaitForChild("Manifest")) :: any
local Config = require(GeoBlox:WaitForChild("Shared"):WaitForChild("Config"))

local Drive = require(script.Parent.Drive)
local Sky = require(script.Parent.Sky)

local Controls = {}

local player = Players.LocalPlayer
local flying = false
local hudToggle: (() -> ())? = nil
local toast: ((string) -> ())? = nil

local VIEWS = {
	"Westminster Bridge",
	"Parliament Square",
	"Whitehall",
	"Trafalgar Square",
	"South Bank (London Eye)",
	"Victoria Embankment",
}

local function root(): (BasePart?, Humanoid?)
	local c = player.Character
	if not c then
		return nil, nil
	end
	return c:FindFirstChild("HumanoidRootPart") :: BasePart?, c:FindFirstChildOfClass("Humanoid")
end

local function say(text: string)
	if toast then
		toast(text)
	end
end

local function setFlying(on: boolean)
	local r, h = root()
	if not r or not h or h.SeatPart then
		return
	end
	flying = on
	h.PlatformStand = on
	r.Anchored = on
	if not on then
		r.AssemblyLinearVelocity = Vector3.zero
	end
end

local function streamAround(pos: Vector3)
	pcall(function()
		player:RequestStreamAroundAsync(pos, 8)
	end)
end

local function teleport(name: string)
	local r = root()
	local p = Manifest.viewpoints[name]
	if not r or not p then
		return
	end
	local pos = Vector3.new(p[1], p[2] + 4, p[3])
	say(name)
	task.spawn(function()
		streamAround(pos)
		local car = Drive.car
		if car and car.PrimaryPart then
			Drive.respawn(pos, car.PrimaryPart.CFrame.LookVector)
		else
			r.CFrame = CFrame.new(pos) * r.CFrame.Rotation
		end
	end)
end

function Controls.setHud(toggle: () -> (), toaster: (string) -> ())
	hudToggle = toggle
	toast = toaster
end

function Controls.start()
	local keys = {
		Enum.KeyCode.One,
		Enum.KeyCode.Two,
		Enum.KeyCode.Three,
		Enum.KeyCode.Four,
		Enum.KeyCode.Five,
		Enum.KeyCode.Six,
	}
	UserInputService.InputBegan:Connect(function(input, processed)
		if processed then
			return
		end
		local k = input.KeyCode
		for i, key in keys do
			if k == key and VIEWS[i] then
				teleport(VIEWS[i])
				return
			end
		end
		if k == Enum.KeyCode.F then
			setFlying(not flying)
			say(if flying then "Fly mode" else "Walking")
		elseif k == Enum.KeyCode.C then
			local r = root()
			local cam = workspace.CurrentCamera
			if r and cam then
				local base = if Drive.car and Drive.car.PrimaryPart then Drive.car.PrimaryPart.Position else r.Position
				Drive.respawn(base, cam.CFrame.LookVector)
				say("New car")
			end
		elseif k == Enum.KeyCode.L then
			Drive.headlights = not Drive.headlights
			say(if Drive.headlights then "Headlights on" else "Headlights off")
		elseif k == Enum.KeyCode.T then
			Sky.addHours(1)
		elseif k == Enum.KeyCode.Y then
			say(if Sky.togglePause() then "Time paused" else "Time running")
		elseif k == Enum.KeyCode.R then
			say("Weather: " .. Sky.nextWeather())
		elseif k == Enum.KeyCode.H then
			if hudToggle then
				hudToggle()
			end
			local dof = Lighting:FindFirstChild("PhotoDepthOfField")
			if dof and dof:IsA("DepthOfFieldEffect") then
				dof.Enabled = not dof.Enabled
			end
		end
	end)

	player.CharacterAdded:Connect(function()
		flying = false
	end)

	-- Keep the engine streaming the road ahead of a moving car.
	local lastStream = 0
	RunService.Heartbeat:Connect(function()
		local now = os.clock()
		local car = Drive.car
		if not car or not car.PrimaryPart or now - lastStream < Config.STREAM_REQUEST_INTERVAL then
			return
		end
		lastStream = now
		local p = car.PrimaryPart
		local ahead = p.Position + p.AssemblyLinearVelocity * Config.STREAM_AHEAD_SECONDS
		task.spawn(streamAround, ahead)
	end)

	RunService.RenderStepped:Connect(function(dt: number)
		if not flying then
			return
		end
		local r = root()
		if not r then
			return
		end
		local cam = workspace.CurrentCamera
		local move = Vector3.zero
		local function down(key: Enum.KeyCode): boolean
			return UserInputService:IsKeyDown(key)
		end
		if down(Enum.KeyCode.W) then
			move += cam.CFrame.LookVector
		end
		if down(Enum.KeyCode.S) then
			move -= cam.CFrame.LookVector
		end
		if down(Enum.KeyCode.D) then
			move += cam.CFrame.RightVector
		end
		if down(Enum.KeyCode.A) then
			move -= cam.CFrame.RightVector
		end
		if down(Enum.KeyCode.E) or down(Enum.KeyCode.Space) then
			move += Vector3.yAxis
		end
		if down(Enum.KeyCode.Q) then
			move -= Vector3.yAxis
		end
		local speed = if down(Enum.KeyCode.LeftShift) then 360 else 90
		local pos = r.Position + (if move.Magnitude > 0 then move.Unit * speed * dt else Vector3.zero)
		local flat = Vector3.new(cam.CFrame.LookVector.X, 0, cam.CFrame.LookVector.Z)
		r.CFrame = CFrame.lookAt(pos, pos + (if flat.Magnitude > 1e-3 then flat else Vector3.new(0, 0, -1)))
	end)
end

return Controls
