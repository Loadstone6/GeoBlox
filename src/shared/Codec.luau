--!strict
-- Decoding of pipeline payloads: base64 -> zstd -> binary buffer.
-- Uses EncodingService (native) when available; base64 also has a pure-Luau
-- path so tests can run outside Roblox.

local Codec = {}

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local DECODE = table.create(256, -1) :: { number }
for i = 1, #B64 do
	DECODE[string.byte(B64, i) + 1] = i - 1
end

-- Pure Luau base64 decoder (fallback / tests).
function Codec.base64(text: string): buffer
	local len = #text
	local pad = 0
	if string.byte(text, len) == 61 then
		pad += 1
	end
	if string.byte(text, len - 1) == 61 then
		pad += 1
	end
	local outLen = (len // 4) * 3 - pad
	local out = buffer.create(outLen)
	local o = 0
	for i = 1, len, 4 do
		local a = DECODE[string.byte(text, i) + 1]
		local b = DECODE[string.byte(text, i + 1) + 1]
		local c = DECODE[(string.byte(text, i + 2) or 61) + 1]
		local d = DECODE[(string.byte(text, i + 3) or 61) + 1]
		local n = bit32.lshift(a, 18) + bit32.lshift(b, 12) + bit32.lshift(math.max(c, 0), 6) + math.max(d, 0)
		if o < outLen then
			buffer.writeu8(out, o, bit32.rshift(n, 16))
		end
		if o + 1 < outLen then
			buffer.writeu8(out, o + 1, bit32.band(bit32.rshift(n, 8), 255))
		end
		if o + 2 < outLen then
			buffer.writeu8(out, o + 2, bit32.band(n, 255))
		end
		o += 3
	end
	return out
end

local encodingService: any = nil
do
	local ok, svc = pcall(function()
		return (game :: any):GetService("EncodingService")
	end)
	if ok then
		encodingService = svc
	end
end

function Codec.available(): boolean
	return encodingService ~= nil
end

-- Decode a pipeline payload (zstd-compressed, base64 text) into a buffer.
function Codec.decode(payload: string): buffer
	if encodingService == nil then
		error("GeoBlox needs EncodingService (zstd) — update Roblox Studio")
	end
	local raw = encodingService:Base64Decode(buffer.fromstring(payload))
	return encodingService:DecompressBuffer(raw, (Enum :: any).CompressionAlgorithm.Zstd)
end

function Codec.magic(b: buffer): string
	return buffer.readstring(b, 0, 4)
end

return Codec
