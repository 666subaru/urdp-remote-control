// Proving the access code without sending it.  The page is plain HTTP on the
// local network, where browsers offer no crypto.subtle, hence SHA-256, HMAC
// and PBKDF2 written out here.  The computer checks the same values
// (urdp/tablet.py: derive_key, proof, seal).

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
const H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
const W = new Uint32Array(64);

// One 64-byte block at *offset* of *bytes* into *state* (8 words).
function compress(state, bytes, offset) {
  for (let i = 0; i < 16; i++) {
    const j = offset + i * 4;
    W[i] = (bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3];
  }
  for (let i = 16; i < 64; i++) {
    const a = W[i - 15], b = W[i - 2];
    const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
    const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
    W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
  }
  let [a, b, c, d, e, f, g, h] = state;
  for (let i = 0; i < 64; i++) {
    const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
    const t1 = (h + S1 + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
    const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
    const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
    h = g; g = f; f = e; e = (d + t1) | 0;
    d = c; c = b; b = a; a = (t1 + t2) | 0;
  }
  state[0] = (state[0] + a) | 0; state[1] = (state[1] + b) | 0;
  state[2] = (state[2] + c) | 0; state[3] = (state[3] + d) | 0;
  state[4] = (state[4] + e) | 0; state[5] = (state[5] + f) | 0;
  state[6] = (state[6] + g) | 0; state[7] = (state[7] + h) | 0;
}

function digestOf(state) {
  const out = new Uint8Array(32);
  for (let i = 0; i < 8; i++) {
    out[i * 4] = state[i] >>> 24; out[i * 4 + 1] = state[i] >>> 16;
    out[i * 4 + 2] = state[i] >>> 8; out[i * 4 + 3] = state[i];
  }
  return out;
}

// SHA-256 of *data*, continuing from *state* after *done* bytes (a whole
// number of blocks) were already hashed into it.
function finish(data, state = H0, done = 0) {
  const s = Int32Array.from(state);
  const total = done + data.length;
  const padded = new Uint8Array(Math.ceil((data.length + 9) / 64) * 64);
  padded.set(data);
  padded[data.length] = 0x80;
  const bits = total * 8;
  const end = padded.length;
  padded[end - 5] = bits / 2 ** 32;
  padded[end - 4] = bits >>> 24; padded[end - 3] = bits >>> 16;
  padded[end - 2] = bits >>> 8; padded[end - 1] = bits;
  for (let i = 0; i < padded.length; i += 64) compress(s, padded, i);
  return digestOf(s);
}

export function sha256(data) { return finish(data); }

const encoder = new TextEncoder();
const bytesOf = (v) => (typeof v === 'string' ? encoder.encode(v) : v);

// HMAC-SHA256 with the key's two padded blocks hashed once, for PBKDF2.
function hmacKey(key) {
  key = bytesOf(key);
  if (key.length > 64) key = sha256(key);
  const inner = new Uint8Array(64).fill(0x36), outer = new Uint8Array(64).fill(0x5c);
  for (let i = 0; i < key.length; i++) { inner[i] ^= key[i]; outer[i] ^= key[i]; }
  const si = Int32Array.from(H0), so = Int32Array.from(H0);
  compress(si, inner, 0);
  compress(so, outer, 0);
  return (msg) => finish(finish(bytesOf(msg), si, 64), so, 64);
}

export function hmac(key, msg) { return hmacKey(key)(msg); }

// PBKDF2-HMAC-SHA256 with a 32-byte result (one block).
export function pbkdf2(password, salt, rounds) {
  const mac = hmacKey(password);
  const first = new Uint8Array([...bytesOf(salt), 0, 0, 0, 1]);
  let u = mac(first);
  const out = Uint8Array.from(u);
  for (let i = 1; i < rounds; i++) {
    u = mac(u);
    for (let j = 0; j < 32; j++) out[j] ^= u[j];
  }
  return out;
}

export function hex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

// The VNC password, sealed by the computer with a pad from the key.
export function unseal(key, nonce, sealed) {
  const pad = hmac(key, `vnc:${nonce}`);
  let out = '';
  for (let i = 0; i < sealed.length / 2 && i < 32; i++) {
    out += String.fromCharCode(parseInt(sealed.substr(i * 2, 2), 16) ^ pad[i]);
  }
  return out;
}
