--!strict
-- Animated landmarks, on parts baked into the place (persistent models):
--   London Eye: the wheel turns once every 30 minutes about its measured
--   axle. Capsules ride the rim and stay level.
--   Elizabeth Tower: hour and minute hands show London time (GMT/BST).

local CollectionService = game:GetService("CollectionService")
local RunService = game:GetService("RunService")

local Landmarks = {}

local function findModel(name: string, timeout: number): Model?
	local deadline = os.clock() + timeout
	while os.clock() < deadline do
		local city = workspace:FindFirstChild("City")
		if city then
			for _, chunk in city:GetChildren() do
				local m = chunk:FindFirstChild(name)
				if m and m:IsA("Model") then
					return m
				end
			end
		end
		task.wait(0.5)
	end
	return nil
end

function Landmarks.londonEye()
	local model = findModel("LondonEye", 60)
	if not model then
		return
	end
	local hub = model:GetAttribute("hub") :: Vector3?
	local plane = model:GetAttribute("plane") :: Vector3?
	local period = (model:GetAttribute("period") :: number?) or 1800
	if not hub or not plane then
		return
	end
	-- Same frame as the pipeline: X in the wheel plane, Y up, Z = X x Y (axle).
	local x = plane.Unit
	local frame = CFrame.fromMatrix(hub, x, Vector3.yAxis, x:Cross(Vector3.yAxis))
	local wheel: { BasePart } = {}
	local wheelOffset: { CFrame } = {}
	local caps: { BasePart } = {}
	local capLocal: { Vector3 } = {}
	local capRot: { CFrame } = {}
	for _, d in model:GetDescendants() do
		if d:IsA("BasePart") then
			if d:HasTag("GeoBloxEyeWheel") then
				table.insert(wheel, d)
				table.insert(wheelOffset, frame:ToObjectSpace(d.CFrame))
			elseif d:HasTag("GeoBloxEyeCapsule") then
				table.insert(caps, d)
				table.insert(capLocal, frame:PointToObjectSpace(d.Position))
				table.insert(capRot, d.CFrame.Rotation)
			end
		end
	end
	local parts = table.create(#wheel + #caps) :: { BasePart }
	for _, p in wheel do
		table.insert(parts, p)
	end
	for _, p in caps do
		table.insert(parts, p)
	end
	local cframes = table.create(#parts) :: { CFrame }
	local last = 0
	RunService.Heartbeat:Connect(function()
		local now = os.clock()
		if now - last < 1 / 30 then
			return
		end
		last = now
		local ang = (workspace:GetServerTimeNow() % period) / period * math.pi * 2
		local rot = frame * CFrame.Angles(0, 0, -ang)
		for i, off in wheelOffset do
			cframes[i] = rot * off
		end
		local n = #wheel
		for i, lp in capLocal do
			cframes[n + i] = CFrame.new(rot:PointToWorldSpace(lp)) * capRot[i]
		end
		workspace:BulkMoveTo(parts, cframes, Enum.BulkMoveMode.FireCFrameChanged)
	end)
end

-- UK civil time: GMT, or BST (UTC+1) from 01:00 UTC on the last Sunday of
-- March to 01:00 UTC on the last Sunday of October.
local function lastSunday(year: number, month: number): number
	local t = DateTime.fromUniversalTime(year, month + 1, 1, 1).UnixTimestamp - 86400
	local d = os.date("!*t", t) :: any
	return t - ((d.wday - 1) * 86400)
end

function Landmarks.londonTime(): number
	local now = os.time()
	local y = (os.date("!*t", now) :: any).year
	local bst = now >= lastSunday(y, 3) and now < lastSunday(y, 10)
	return now + (if bst then 3600 else 0)
end

function Landmarks.bigBen()
	local function setHands()
		local t = Landmarks.londonTime() % 86400
		local minutes = (t / 60) % 60
		local hours = (t / 3600) % 12
		for _, dial in CollectionService:GetTagged("GeoBloxClockDial") do
			local c = dial:GetAttribute("center") :: Vector3?
			local nrm = dial:GetAttribute("normal") :: Vector3?
			if not c or not nrm then
				continue
			end
			local n = nrm.Unit
			local right = (-n):Cross(Vector3.yAxis).Unit
			for _, hand in dial:GetDescendants() do
				if not hand:IsA("BasePart") then
					continue
				end
				local hour = hand:HasTag("GeoBloxHourHand")
				if not hour and not hand:HasTag("GeoBloxMinuteHand") then
					continue
				end
				local turns = if hour then hours / 12 else minutes / 60
				local a = turns * math.pi * 2
				local dir = Vector3.yAxis * math.cos(a) + right * math.sin(a)
				local len = hand.Size.Y
				local out = (hand.Position - c):Dot(n)
				hand.CFrame = CFrame.fromMatrix(c + n * out + dir * (len / 2 - 1.3), dir:Cross(n), dir, n)
			end
		end
	end
	task.spawn(function()
		while true do
			setHands()
			task.wait(5)
		end
	end)
end

return Landmarks
