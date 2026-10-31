/** API 载荷 AES-256-GCM（与 api/payload_crypto.py 派生密钥一致） */
const PAYLOAD_SALT_PREFIX = "zfqa-payload-v1";

async function derivePayloadKey(accessToken, adminTokenHint) {
  const hint = (adminTokenHint || accessToken || "").slice(0, 8);
  const saltStr = PAYLOAD_SALT_PREFIX + hint;
  const enc = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(accessToken),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      salt: enc.encode(saltStr),
      iterations: 12000,
      hash: "SHA-256",
    },
    baseKey,
    256
  );
  return crypto.subtle.importKey("raw", bits, "AES-GCM", false, ["encrypt", "decrypt"]);
}

function b64urlEncode(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  bytes.forEach((b) => { s += String.fromCharCode(b); });
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(str) {
  let s = str.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function encryptPayload(obj, accessToken, adminHint) {
  const key = await derivePayloadKey(accessToken, adminHint);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plain = new TextEncoder().encode(JSON.stringify(obj));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain);
  return {
    enc: "v1",
    nonce: b64urlEncode(iv),
    payload: b64urlEncode(ct),
  };
}

async function decryptPayload(envelope, accessToken, adminHint) {
  if (!envelope || envelope.enc !== "v1") return envelope;
  const key = await derivePayloadKey(accessToken, adminHint);
  const iv = b64urlDecode(envelope.nonce);
  const ct = b64urlDecode(envelope.payload);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct);
  return JSON.parse(new TextDecoder().decode(plain));
}

window.PayloadCrypto = { encryptPayload, decryptPayload };
