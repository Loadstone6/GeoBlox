--!nocheck
-- Loads Roblox-style ModuleScripts (require(script.Parent.X)) from disk so the
-- pure world-generation modules can run under Lune.

local fs = require("@lune/fs")
local luau = require("@lune/luau")
local roblox = require("@lune/roblox")

local Harness = {}

local cache = {}
local loadPath

local function node(path)
	return setmetatable({ __path = path }, {
		__index = function(self, key)
			if key == "Parent" then
				return node(string.match(rawget(self, "__path"), "^(.*)/[^/]+$"))
			end
			return node(rawget(self, "__path") .. "/" .. key)
		end,
	})
end

local function fakeRequire(target)
	if type(target) == "table" and rawget(target, "__path") then
		return loadPath(rawget(target, "__path"))
	end
	error("harness: unsupported require target " .. tostring(target))
end

function loadPath(path)
	if cache[path] ~= nil then
		return cache[path]
	end
	local file = path .. ".luau"
	local source = fs.readFile(file)
	local chunk = luau.load(source, {
		debugName = file,
		environment = {
			script = node(path),
			require = fakeRequire,
			Enum = roblox.Enum,
			Vector3 = roblox.Vector3,
			Region3 = roblox.Region3,
			Color3 = roblox.Color3,
		},
	})
	local result = chunk()
	cache[path] = result
	return result
end

function Harness.require(path: string)
	return loadPath(path)
end

return Harness
