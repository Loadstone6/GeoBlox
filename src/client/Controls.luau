--!strict
-- Exploration controls (no vehicles):
--   F            fly (WASD, E/Space up, Q down, Shift fast)
--   V            fast walk
--   1-6          viewpoints (Westminster Bridge, Parliament Square, Whitehall,
--                Trafalgar Square, South Bank / London Eye, Embankment)
--   T / Y / R    +1 hour / pause time / next weather
--   H            photo mode (hide HUD, depth of field)
-- Feet are snapped to the visual street surface: the reconstructed pavement
-- sits slightly above the collision terrain, so HipHeight follows a raycast
-- against the street meshes.

local Lighting = game:GetService("Lighting")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local Manifest =
	require(ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Generated"):WaitForChild("Manifest")) :: any

local Sky = require(script.Parent.Sky)
local World = require(script.Parent.World)

local Controls = {}

local player = Players.LocalPlayer
local flying = false
local fastWalk = false
local baseHip: number? = nil
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
	if not r or not h then
		return
	end
	flying = on
	h.PlatformStand = on
	r.Anchored = on
	if not on then
		r.AssemblyLinearVelocity = Vector3.zero
	end
end

local function teleport(name: string)
	local r = root()
	local p = Manifest.viewpoints[name]
	if not r or not p then
		return
	end
	local pos = Vector3.new(p[1], p[2] + 4, p[3])
	r.CFrame = CFrame.new(pos) * r.CFrame.Rotation
	say(name)
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
		elseif k == Enum.KeyCode.V then
			fastWalk = not fastWalk
			local _, h = root()
			if h then
				h.WalkSpeed = if fastWalk then 50 else 16
			end
			say(if fastWalk then "Fast walk" else "Normal walk")
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
		fastWalk = false
		baseHip = nil
	end)

	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Include
	local terrainParams = RaycastParams.new()
	terrainParams.FilterType = Enum.RaycastFilterType.Include
	terrainParams.FilterDescendantsInstances = { workspace.Terrain }
	local lastFilter = 0

	RunService.RenderStepped:Connect(function(dt: number)
		local r, h = root()
		if not r or not h then
			return
		end
		if flying then
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
			return
		end
		-- Ground snap: lift the character so its feet sit on the street mesh.
		if baseHip == nil then
			baseHip = h.HipHeight
		end
		local now = os.clock()
		if now - lastFilter > 1 then
			lastFilter = now
			params.FilterDescendantsInstances = World.groundParts :: any
		end
		local hit = workspace:Raycast(r.Position, Vector3.new(0, -12, 0), params)
		local terrainHit = workspace:Raycast(r.Position, Vector3.new(0, -12, 0), terrainParams)
		local base = baseHip :: number
		local target = base
		if hit and terrainHit then
			local lift = hit.Position.Y - terrainHit.Position.Y
			if lift > 0 and lift < 1.5 then
				target = base + lift
			end
		end
		h.HipHeight += (target - h.HipHeight) * math.min(1, dt * 12)
	end)
end

return Controls
