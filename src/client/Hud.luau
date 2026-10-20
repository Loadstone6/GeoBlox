--!strict
-- Minimal debug HUD: frame rate, generation progress, decor/animal counts,
-- time, weather and season, plus the key bindings and short toasts.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Hud = {}

local HELP =
	"[F] fly  [V] fast walk  [1-5] spawn/bridge/mill/farm/aerial  [T] +1h  [Y] pause time  [R] weather  [G] season  [H] photo mode"

local function label(parent: Instance, pos: UDim2, size: UDim2, textSize: number): TextLabel
	local l = Instance.new("TextLabel")
	l.BackgroundColor3 = Color3.new(0, 0, 0)
	l.BackgroundTransparency = 0.55
	l.BorderSizePixel = 0
	l.TextColor3 = Color3.fromRGB(240, 240, 236)
	l.Font = Enum.Font.GothamMedium
	l.TextSize = textSize
	l.TextXAlignment = Enum.TextXAlignment.Left
	l.Position = pos
	l.Size = size
	l.AutomaticSize = Enum.AutomaticSize.XY
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

function Hud.start(stats: () -> (number, number, string, string, string, boolean)): (() -> (), (string) -> ())
	local gui = Instance.new("ScreenGui")
	gui.Name = "GeoBloxHud"
	gui.ResetOnSpawn = false
	gui.IgnoreGuiInset = true
	gui.Parent = Players.LocalPlayer:WaitForChild("PlayerGui")

	local info = label(gui, UDim2.fromOffset(12, 56), UDim2.fromOffset(0, 0), 14)
	local help = label(gui, UDim2.new(0, 12, 1, -40), UDim2.fromOffset(0, 0), 13)
	help.Text = HELP
	local toast = label(gui, UDim2.new(0.5, 0, 0, 56), UDim2.fromOffset(0, 0), 18)
	toast.AnchorPoint = Vector2.new(0.5, 0)
	toast.Visible = false

	local frames, acc = 0, 0
	local fps = 0
	RunService.RenderStepped:Connect(function(dt)
		frames += 1
		acc += dt
		if acc < 0.5 then
			return
		end
		fps = frames / acc
		frames, acc = 0, 0
		local decorParts, animals, timeStr, weather, season, paused = stats()
		local done = ReplicatedStorage:GetAttribute("TerrainDone") or 0
		local total = ReplicatedStorage:GetAttribute("TerrainTotal") or 0
		info.Text = string.format(
			"GeoBlox · practice polder road\nFPS %d   terrain %d/%d chunks   decor parts %d   animals %d\n%s%s   %s   %s",
			math.floor(fps + 0.5),
			done,
			total,
			decorParts,
			animals,
			timeStr,
			if paused then " (paused)" else "",
			weather,
			season
		)
	end)

	local toastToken = 0
	local function showToast(text: string)
		toastToken += 1
		local token = toastToken
		toast.Text = text
		toast.Visible = gui.Enabled
		task.delay(2, function()
			if token == toastToken then
				toast.Visible = false
			end
		end)
	end

	local function toggle()
		gui.Enabled = not gui.Enabled
	end

	return toggle, showToast
end

return Hud
