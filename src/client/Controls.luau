--!strict
-- Exploration controls for testing the look of the world (no vehicles):
--   F        toggle fly (WASD + E/Space up, Q down, Shift = fast)
--   V        toggle fast walk
--   1-5      teleport: spawn / bridge / windmill / next farm / aerial view
--   T / G / R / Y  +1 hour / next season / next weather / pause time
--   H        hide the HUD (photo mode, adds depth of field)

local Lighting = game:GetService("Lighting")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Config = require(Shared.Config)
local Layout = require(Shared.Layout)
local Sampler = require(Shared.Sampler)

local Sky = require(script.Parent.Sky)

local Controls = {}

local player = Players.LocalPlayer
local flying = false
local fastWalk = false
local farmCursor = 0
local hudToggle: (() -> ())? = nil
local toastFn: ((string) -> ())? = nil

local function getRoot(): (BasePart?, Humanoid?)
	local character = player.Character
	if not character then
		return nil, nil
	end
	return character:FindFirstChild("HumanoidRootPart") :: BasePart?, character:FindFirstChildOfClass("Humanoid")
end

local function setFlying(on: boolean)
	local root, humanoid = getRoot()
	if not root or not humanoid then
		return
	end
	flying = on
	humanoid.PlatformStand = on
	root.Anchored = on
	if not on then
		root.AssemblyLinearVelocity = Vector3.zero
	end
end

local function toast(text: string)
	if toastFn then
		toastFn(text)
	end
end

local function teleport(pos: Vector3, look: Vector3, fly: boolean)
	local root = getRoot()
	if not root then
		return
	end
	if fly ~= flying then
		setFlying(fly)
	end
	root.CFrame = CFrame.lookAt(pos, pos + Vector3.new(look.X, 0, look.Z))
	local camera = workspace.CurrentCamera
	if camera then
		camera.CFrame = CFrame.lookAt(pos - look * 30 + Vector3.new(0, 10, 0), pos + look * 40)
	end
end

function Controls.setHud(toggle: () -> (), toaster: (string) -> ())
	hudToggle = toggle
	toastFn = toaster
end

function Controls.isFlying(): boolean
	return flying
end

function Controls.start(layout: Layout.Layout, sampler: Sampler.Sampler)
	local function onRoad(s: number): Vector3
		local x, z = Layout.toWorld(s, 1, 0)
		return Vector3.new(x, Config.ROAD_Y + 3.5, z)
	end

	local actions: { [Enum.KeyCode]: () -> () } = {
		[Enum.KeyCode.F] = function()
			setFlying(not flying)
			toast(if flying then "Fly mode on" else "Fly mode off")
		end,
		[Enum.KeyCode.V] = function()
			fastWalk = not fastWalk
			local _, humanoid = getRoot()
			if humanoid then
				humanoid.WalkSpeed = if fastWalk then 48 else 16
			end
			toast(if fastWalk then "Fast walk" else "Normal walk")
		end,
		[Enum.KeyCode.One] = function()
			teleport(onRoad(Config.SPAWN_S), Vector3.new(1, 0, 0), false)
			toast("Spawn")
		end,
		[Enum.KeyCode.Two] = function()
			teleport(onRoad(layout.canal.s - 60), Vector3.new(1, 0, 0), false)
			toast("Canal bridge")
		end,
		[Enum.KeyCode.Three] = function()
			local W = layout.windmill
			local x, z = Layout.toWorld(W.s - 70, W.side, W.d - 110)
			local mx, mz = Layout.toWorld(W.s, W.side, W.d)
			local pos = Vector3.new(x, sampler:height(x, z) + 3.5, z)
			teleport(pos, (Vector3.new(mx, pos.Y, mz) - pos).Unit, false)
			toast("Polder mill")
		end,
		[Enum.KeyCode.Four] = function()
			if #layout.farmList == 0 then
				return
			end
			farmCursor = farmCursor % #layout.farmList + 1
			local farm = layout.farmList[farmCursor]
			local pos = onRoad(farm.s - 40)
			local fx, fz = Layout.toWorld(farm.s, farm.side, 80)
			teleport(pos, (Vector3.new(fx, pos.Y, fz) - pos).Unit, false)
			toast(string.format("Farm %d/%d: %s", farmCursor, #layout.farmList, farm.style))
		end,
		[Enum.KeyCode.Five] = function()
			teleport(Vector3.new(Config.SPAWN_S + 300, Config.FIELD_Y + 260, -520), Vector3.new(1, 0, 0.6).Unit, true)
			toast("Aerial view (fly mode)")
		end,
		[Enum.KeyCode.T] = function()
			Sky.addHours(1)
		end,
		[Enum.KeyCode.Y] = function()
			toast(if Sky.togglePause() then "Time paused" else "Time running")
		end,
		[Enum.KeyCode.R] = function()
			toast("Weather: " .. Sky.nextWeather())
		end,
		[Enum.KeyCode.G] = function()
			toast("Season: " .. Sky.nextSeason())
		end,
		[Enum.KeyCode.H] = function()
			if hudToggle then
				hudToggle()
			end
			local dof = Lighting:FindFirstChild("PhotoDepthOfField")
			if dof and dof:IsA("DepthOfFieldEffect") then
				dof.Enabled = not dof.Enabled
			end
		end,
	}

	UserInputService.InputBegan:Connect(function(input, processed)
		if processed then
			return
		end
		local action = actions[input.KeyCode]
		if action then
			action()
		end
	end)

	player.CharacterAdded:Connect(function()
		flying = false
		fastWalk = false
	end)

	RunService.RenderStepped:Connect(function(dt)
		if not flying then
			return
		end
		local root = getRoot()
		local camera = workspace.CurrentCamera
		if not root or not camera then
			return
		end
		local cam = camera.CFrame
		local move = Vector3.zero
		local function down(k: Enum.KeyCode): boolean
			return UserInputService:IsKeyDown(k)
		end
		if down(Enum.KeyCode.W) then
			move += cam.LookVector
		end
		if down(Enum.KeyCode.S) then
			move -= cam.LookVector
		end
		if down(Enum.KeyCode.D) then
			move += cam.RightVector
		end
		if down(Enum.KeyCode.A) then
			move -= cam.RightVector
		end
		if down(Enum.KeyCode.E) or down(Enum.KeyCode.Space) then
			move += Vector3.yAxis
		end
		if down(Enum.KeyCode.Q) then
			move -= Vector3.yAxis
		end
		local speed = if down(Enum.KeyCode.LeftShift) then 320 else 90
		local pos = root.Position
		if move.Magnitude > 0 then
			pos += move.Unit * speed * dt
		end
		local ground = sampler:height(pos.X, pos.Z) + 3
		if pos.Y < ground then
			pos = Vector3.new(pos.X, ground, pos.Z)
		end
		local flat = Vector3.new(cam.LookVector.X, 0, cam.LookVector.Z)
		if flat.Magnitude < 1e-3 then
			flat = Vector3.new(0, 0, -1)
		end
		root.CFrame = CFrame.lookAt(pos, pos + flat)
	end)
end

return Controls
