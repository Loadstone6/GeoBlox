--!strict
-- HUD: frame rate, streaming stats, time/weather, key help, toasts and the
-- data attribution required by the Open Government Licence and the ODbL.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Manifest =
	require(ReplicatedStorage:WaitForChild("GeoBlox"):WaitForChild("Generated"):WaitForChild("Manifest")) :: any

local Hud = {}

local HELP =
	"[W/S] drive  [A/D] steer  [Shift] handbrake  [Space] get out  [C] new car  [L] lights  [F] fly  [1-6] viewpoints  [T] +1h  [Y] pause  [R] weather  [H] photo"

local function label(parent: Instance, pos: UDim2, textSize: number): TextLabel
	local l = Instance.new("TextLabel")
	l.BackgroundColor3 = Color3.new(0, 0, 0)
	l.BackgroundTransparency = 0.55
	l.BorderSizePixel = 0
	l.TextColor3 = Color3.fromRGB(240, 240, 236)
	l.Font = Enum.Font.GothamMedium
	l.TextSize = textSize
	l.TextXAlignment = Enum.TextXAlignment.Left
	l.Position = pos
	l.AutomaticSize = Enum.AutomaticSize.XY
	l.Size = UDim2.fromOffset(0, 0)
	local pad = Instance.new("UIPadding")
	pad.PaddingLeft = UDim.new(0, 8)
	pad.PaddingRight = UDim.new(0, 8)
	pad.PaddingTop = UDim.new(0, 4)
	pad.PaddingBottom = UDim.new(0, 4)
	pad.Parent = l
	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 6)
	corner.Parent = l
	l.Parent = parent
	return l
end

function Hud.start(stats: () -> string): (() -> (), (string) -> ())
	local gui = Instance.new("ScreenGui")
	gui.Name = "GeoBloxHud"
	gui.ResetOnSpawn = false
	gui.IgnoreGuiInset = true
	gui.Parent = Players.LocalPlayer:WaitForChild("PlayerGui")

	local info = label(gui, UDim2.fromOffset(12, 56), 14)
	label(gui, UDim2.new(0, 12, 1, -64), 13).Text = HELP
	local credit = label(gui, UDim2.new(0, 12, 1, -36), 11)
	credit.Text = Manifest.attribution
	credit.TextTransparency = 0.15
	local toast = label(gui, UDim2.new(0.5, 0, 0, 56), 18)
	toast.AnchorPoint = Vector2.new(0.5, 0)
	toast.Visible = false

	local frames, acc = 0, 0
	RunService.RenderStepped:Connect(function(dt)
		frames += 1
		acc += dt
		if acc < 0.5 then
			return
		end
		local fps = frames / acc
		frames, acc = 0, 0
		info.Text = string.format(
			"GeoBlox · Westminster, rebuilt from LiDAR + OpenStreetMap\nFPS %d   %s",
			math.floor(fps + 0.5),
			stats()
		)
	end)

	local token = 0
	local function show(text: string)
		token += 1
		local mine = token
		toast.Text = text
		toast.Visible = gui.Enabled
		task.delay(2.2, function()
			if mine == token then
				toast.Visible = false
			end
		end)
	end
	return function()
		gui.Enabled = not gui.Enabled
	end, show
end

return Hud
