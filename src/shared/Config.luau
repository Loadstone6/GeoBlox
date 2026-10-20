--!strict
-- Runtime settings. The city itself is baked into the place (Workspace.City)
-- and streamed by the engine; these control effects, landmarks and the car.

local Config = {}

-- Night lighting: share of window bands lit, and real lights near the camera.
Config.LIT_WINDOW_SHARE = 0.38
Config.LAMP_LIGHT_RADIUS = 260
Config.MAX_LAMP_LIGHTS = 40

-- Streaming: request the area ahead of a moving car (studs).
Config.STREAM_AHEAD_SECONDS = 2.5
Config.STREAM_REQUEST_INTERVAL = 0.5

-- Test car (studs; 1 stud = 0.3 m). Tyre forces are scaled to real-world
-- acceleration limits, since Roblox gravity at this scale is about 6 g.
Config.Car = {
	length = 14.4,
	width = 6.0,
	wheelbase = 8.8,
	track = 5.2,
	wheelRadius = 1.07,
	wheelWidth = 0.75,
	restLength = 1.9, -- suspension, mount to wheel centre at full droop
	sag = 0.45, -- static compression
	damping = 0.42, -- damping ratio
	maxSteer = math.rad(34),
	steerAtSpeed = math.rad(7), -- steering lock at top speed
	topSpeed = 105, -- studs/s (~70 mph)
	accel = 13, -- studs/s^2 (about 0.4 g real)
	brake = 32, -- studs/s^2 (about 1 g real)
	grip = 34, -- lateral, studs/s^2 (about 1 g real)
	handbrakeGrip = 9,
	rollResist = 0.6,
}

return Config
