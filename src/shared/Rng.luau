--!strict
-- Deterministic hashing and random numbers that behave identically on the
-- server, the client and in Lune tests (no dependence on Roblox's Random).

local TWO32 = 4294967296

local function mul32(a: number, b: number): number
	local ah, al = a // 65536, a % 65536
	return ((ah * b % 65536) * 65536 + al * b) % TWO32
end

local function fmix(h: number): number
	h = bit32.bxor(h, bit32.rshift(h, 16))
	h = mul32(h, 0x85EBCA6B)
	h = bit32.bxor(h, bit32.rshift(h, 13))
	h = mul32(h, 0xC2B2AE35)
	return bit32.bxor(h, bit32.rshift(h, 16))
end

export type Rng = {
	state: number,
	next: (self: Rng) -> number,
	range: (self: Rng, a: number, b: number) -> number,
	int: (self: Rng, a: number, b: number) -> number,
	chance: (self: Rng, p: number) -> boolean,
	weighted: (self: Rng, weights: { number }) -> number,
}

local RngMethods = {}
RngMethods.__index = RngMethods

local Rng = {}

-- Hash any number of integers (negative values allowed) into a 32-bit value.
function Rng.hash(...: number): number
	local h = 0x9E3779B9
	for i = 1, select("#", ...) do
		local v = math.floor((select(i, ...))) % TWO32
		h = fmix((bit32.bxor(h, v) + i * 0x632BE5AB) % TWO32)
	end
	return h
end

-- Uniform float in [0, 1) from integer inputs.
function Rng.unit(...: number): number
	return Rng.hash(...) / TWO32
end

function Rng.new(seed: number): Rng
	local self = setmetatable({ state = math.floor(seed) % TWO32 }, RngMethods)
	return (self :: any) :: Rng
end

function RngMethods.next(self: Rng): number
	self.state = (mul32(self.state, 1664525) + 1013904223) % TWO32
	return fmix(self.state) / TWO32
end

function RngMethods.range(self: Rng, a: number, b: number): number
	return a + (b - a) * self:next()
end

function RngMethods.int(self: Rng, a: number, b: number): number
	return math.min(b, a + math.floor(self:next() * (b - a + 1)))
end

function RngMethods.chance(self: Rng, p: number): boolean
	return self:next() < p
end

function RngMethods.weighted(self: Rng, weights: { number }): number
	local total = 0
	for _, w in weights do
		total += w
	end
	local r = self:next() * total
	for i, w in weights do
		r -= w
		if r < 0 then
			return i
		end
	end
	return #weights
end

return Rng
