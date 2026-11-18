// The tablet side of URDP's tablet screen: asks the computer for the screen,
// draws it with noVNC and keeps reconnecting until the computer stops sharing.
import RFB from './novnc/core/rfb.js';
import { encodings } from './novnc/core/encodings.js';
import { clientToElement } from './novnc/core/util/element.js';
import { hex, hmac, pbkdf2, unseal } from './auth.js';
import FastView from './fastview.js';

// Ask the server to draw the cursor into the picture.  With the cursor
// pseudo-encodings noVNC draws it itself, but only where the tablet is being
// touched, so the computer's own mouse would be invisible on this screen.
const CURSOR_ENCODINGS = [encodings.pseudoEncodingCursor,
                          encodings.pseudoEncodingVMwareCursor];
const sendEncodings = RFB.messages.clientEncodings;
RFB.messages.clientEncodings = (sock, list) =>
  sendEncodings(sock, list.filter((e) => !CURSOR_ENCODINGS.includes(e)));

const $ = (id) => document.getElementById(id);
const STORE = 'urdp-tablet-code';

let strings = {};
let rfb = null;
let failures = 0;
let timer = 0;
let shownSize = '';
let pageVersion = '';            // the page files this page was loaded from
let inputSock = null;            // direct touch / pen channel, when offered
let fastFailures = 0;            // fast pictures in a row that never showed
let derived = { from: '', key: null };   // the key the code makes, kept

function keyFor(salt, rounds) {
  const from = [code, salt, rounds].join('\n');
  if (derived.from !== from) derived = { from, key: pbkdf2(code, salt, rounds) };
  return derived.key;
}

// The code arrives in the fragment of the address the QR code holds.  Keep it
// and take it out of the address bar, so a screenshot does not show it.
let code = new URLSearchParams(location.hash.slice(1)).get('k') || '';
try {
  if (code) localStorage.setItem(STORE, code);
  else code = localStorage.getItem(STORE) || '';
} catch (e) { /* storage disabled: the code lives for this page only */ }
if (location.hash) history.replaceState(null, '', location.pathname);

function text(key, fallback) { return strings[key] || fallback; }

function status(message) { $('status').textContent = message; }

function applyStrings() {
  $('codelabel').textContent = text('code', 'Enter the code shown in URDP on the computer.');
  $('codebutton').textContent = text('connect', 'Connect');
  $('full').textContent = text('fullscreen', 'Full Screen');
}

function screenSize() {
  return [screen.width, screen.height, window.devicePixelRatio || 1];
}

function isFull() { return !!document.fullscreenElement; }

function updateChrome() {
  const live = !!(rfb && document.body.classList.contains('live'));
  $('full').hidden = isFull();
  $('fullmini').hidden = !live || isFull() || !document.fullscreenEnabled;
  $('keys').hidden = !live || !direct();
}

function schedule(ms) {
  clearTimeout(timer);
  timer = setTimeout(connect, ms);
}

function retryLater() {
  status(text('reconnecting', 'Reconnecting…'));
  schedule(Math.min(500 * 2 ** failures++, 5000));
}

function askCode(wrong) {
  status(wrong ? text('wrong', 'Wrong code') : '');
  $('codeform').hidden = false;
  $('code').value = '';
  $('code').focus();
}

async function connect() {
  clearTimeout(timer);
  if (rfb) return;
  status(text('connecting', 'Connecting…'));
  const [w, h, dpr] = screenSize();
  // The computer sends a challenge; the answer proves the code without
  // sending it, and the VNC password comes back sealed with it.
  let answer, challenge, key;
  try {
    const reply = await fetch('nonce', { cache: 'no-store' });
    challenge = await reply.json();
    strings = challenge.strings || strings;
    // URDP was updated while this page stayed open: load the new page.
    if (challenge.version) {
      if (pageVersion && challenge.version !== pageVersion) { location.reload(); return; }
      pageVersion = challenge.version;
    }
    applyStrings();
    if (!code) { askCode(false); return; }
    key = keyFor(challenge.salt, challenge.rounds);
    const p = hex(hmac(key, `hello:${challenge.nonce}`));
    const query = new URLSearchParams({ n: challenge.nonce, p, w, h, dpr });
    answer = await (await fetch('hello?' + query, { cache: 'no-store' })).json();
  } catch (e) {
    retryLater();
    return;
  }
  if (!answer.ok) {
    askCode(true);
    if (answer.reason === 'wait' || answer.reason === 'locked') {
      status(text('wait', 'Too many wrong codes; wait a minute and try again.'));
    }
    return;
  }
  $('codeform').hidden = true;
  try { localStorage.setItem(STORE, code); } catch (e) { /* ignore */ }
  shownSize = [w, h, dpr].join('x');
  if (answer.restart) {           // the computer is resizing the screen
    schedule(1500);
    return;
  }

  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const ticket = encodeURIComponent(answer.ticket);
  if (answer.video && fastFailures < 2) {
    // The computer records the screen itself: up to 60 frames a second.
    rfb = new FastView($('screen'), `${scheme}://${location.host}/video?t=${ticket}`);
  } else {
    const url = `${scheme}://${location.host}/ws?t=${ticket}`;
    rfb = new RFB($('screen'), url, {
      credentials: { password: unseal(key, challenge.nonce, answer.password) },
      shared: true,
      wsProtocols: ['binary'],
    });
  }
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.qualityLevel = 8;
  rfb.compressionLevel = 2;
  rfb.focusOnClick = false;
  rfb.addEventListener('connect', () => {
    failures = 0;
    if (answer.input) openInput(ticket);
    if (answer.audio) openAudio(ticket);
    if (answer.mic) openMic(ticket);
    else stopMic();
    document.body.classList.add('live');
    status(text('connected', 'Connected'));
    updateChrome();
  });
  const shown = rfb;
  rfb.addEventListener('disconnect', () => {
    if (shown instanceof FastView) fastFailures = shown.everConnected ? 0 : fastFailures + 1;
    rfb = null;
    if (inputSock) inputSock.close();
    if (audioSock) audioSock.close();
    if (micSock) micSock.close();
    document.body.classList.remove('live');
    updateChrome();
    retryLater();
  });
}

// ----------------------------------------------------------- direct input
// When the computer can create input devices, touches and pen strokes go to
// it as they are: a real touchscreen and a real pen there, tied to this
// screen.  KDE then does the gestures, scrolling and right-clicks itself,
// and the pen keeps its pressure, tilt, hover and eraser.
function openInput(ticket) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const sock = new WebSocket(`${scheme}://${location.host}/input?t=${ticket}`);
  sock.addEventListener('open', () => { inputSock = sock; updateChrome(); });
  sock.addEventListener('close', () => { if (inputSock === sock) inputSock = null; });
  sock.addEventListener('error', () => sock.close());
}

// ------------------------------------------------------------------ sound
// The computer's sound arrives as 10 ms pieces of 48 kHz 16-bit stereo.
// Each is scheduled right after the one before, behind a cushion that
// absorbs Wi-Fi jitter.  The cushion adapts: it starts small, grows a step
// each time the sound runs dry, and shrinks back after a calm spell.  When
// pieces pile up past it, they are dropped, so the sound never falls behind
// the picture.  Browsers start sound only after a touch.
const RATE = 48000;
const MIN_CUSHION = 0.03, MAX_CUSHION = 0.2, STEP = 0.02;   // seconds
const SLACK = 0.05;              // tolerated above the cushion before dropping
const PAUSE = 0.1;               // a gap this long is silence, not a hiccup
const CALM = 10;                 // seconds without a hiccup before shrinking
let audioSock = null;
let audioCtx = null;
let audioAt = 0;
let cushion = MIN_CUSHION;
let calmSince = 0;

function openAudio(ticket) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const sock = new WebSocket(`${scheme}://${location.host}/audio?t=${ticket}`);
  sock.binaryType = 'arraybuffer';
  if (!audioCtx) {
    try {
      audioCtx = new AudioContext({ sampleRate: RATE, latencyHint: 'interactive' });
      audioCtx.addEventListener('statechange', soundHint);
    } catch (e) {
      sock.close();
      return;
    }
  }
  sock.addEventListener('open', () => { audioSock = sock; soundHint(); });
  sock.addEventListener('message', (ev) => playPiece(ev.data));
  sock.addEventListener('close', () => { if (audioSock === sock) audioSock = null; soundHint(); });
  sock.addEventListener('error', () => sock.close());
}

function playPiece(data) {
  if (!audioCtx || audioCtx.state !== 'running') return;
  const pcm = new Int16Array(data);
  const frames = pcm.length >> 1;
  if (!frames) return;
  const now = audioCtx.currentTime;
  if (audioAt < now + 0.002) {
    // Ran dry.  Right after sound (not after a pause, when the computer
    // sends nothing) that was a hiccup: keep a bigger cushion from now on.
    if (audioAt > now - PAUSE) cushion = Math.min(MAX_CUSHION, cushion + STEP);
    audioAt = now + cushion;
    calmSince = now;
  } else if (audioAt > now + cushion + SLACK) {
    return;                                       // piled up: catch up
  } else if (now - calmSince > CALM && cushion > MIN_CUSHION) {
    cushion = Math.max(MIN_CUSHION, cushion - 0.01);
    calmSince = now;
  }
  const buffer = audioCtx.createBuffer(2, frames, RATE);
  const left = buffer.getChannelData(0), right = buffer.getChannelData(1);
  for (let i = 0; i < frames; i++) {
    left[i] = pcm[2 * i] / 32768;
    right[i] = pcm[2 * i + 1] / 32768;
  }
  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);
  source.start(audioAt);
  audioAt += frames / RATE;
}

function soundHint() {
  const sound = !!(audioSock && audioCtx && audioCtx.state !== 'running');
  const mic = !!(micSock && micCtx && micCtx.state !== 'running');
  const hint = $('soundhint');
  if (sound) hint.textContent = text('sound', 'Touch the screen to turn on the sound.');
  else if (mic) hint.textContent = text('mic', 'Touch the screen to turn on the microphone.');
  else if (micRefused) hint.textContent = text('nomic', 'The browser did not allow the microphone.');
  hint.hidden = !(sound || mic || micRefused);
}

function startSound() {
  for (const ctx of [audioCtx, micCtx]) {
    if (ctx && ctx.state !== 'running') ctx.resume().then(soundHint, () => {});
  }
}

// ------------------------------------------------------------- microphone
// What the tablet hears goes to the computer's "URDP Tablet Microphone" as
// 10 ms pieces of 48 kHz 16-bit mono (micworklet.js cuts them).  The browser
// cleans it first: its echo cancelling keeps the computer's sound, playing
// from this tablet, out of it.  Browsers allow the microphone only over
// HTTPS.  The microphone stays open across reconnects, so the browser asks
// once; when the computer turns it off, it is closed.
let micSock = null;
let micCtx = null;
let micStream = null;
let micSetup = null;
let micRefused = false;
const MIC_BACKLOG = 9600;        // bytes waiting to be sent: 100 ms

async function setupMic() {
  if (!navigator.mediaDevices || !window.AudioWorkletNode) throw new Error('no microphone here');
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  micCtx = new AudioContext({ sampleRate: RATE, latencyHint: 'interactive' });
  micCtx.addEventListener('statechange', soundHint);
  await micCtx.audioWorklet.addModule('micworklet.js');
  const node = new AudioWorkletNode(micCtx, 'urdp-mic');
  micCtx.createMediaStreamSource(micStream).connect(node);
  node.connect(micCtx.destination);        // silent; keeps it running
  node.port.onmessage = (ev) => {
    if (micSock && micSock.readyState === WebSocket.OPEN
        && micSock.bufferedAmount < MIC_BACKLOG) micSock.send(ev.data);
  };
}

function openMic(ticket) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const sock = new WebSocket(`${scheme}://${location.host}/mic?t=${ticket}`);
  sock.binaryType = 'arraybuffer';
  sock.addEventListener('open', () => { micSock = sock; soundHint(); });
  sock.addEventListener('close', () => { if (micSock === sock) micSock = null; soundHint(); });
  sock.addEventListener('error', () => sock.close());
  if (!micSetup) {
    micSetup = setupMic().then(() => { micRefused = false; soundHint(); }, () => {
      stopMic();
      micRefused = true;                   // said for a while, then dropped
      soundHint();
      setTimeout(() => { micRefused = false; soundHint(); }, 5000);
      sock.close();
    });
  }
}

function stopMic() {
  if (micStream) micStream.getTracks().forEach((track) => track.stop());
  if (micCtx) micCtx.close().catch(() => {});
  micStream = micCtx = micSetup = null;
}

function direct() { return inputSock && inputSock.readyState === WebSocket.OPEN; }

function post(message) { inputSock.send(JSON.stringify(message)); }

function unit(value) { return Math.round(Math.min(1, Math.max(0, value)) * 1e5) / 1e5; }

function where(ev) {
  const r = rfb._canvas.getBoundingClientRect();
  return [unit((ev.clientX - r.left) / r.width), unit((ev.clientY - r.top) / r.height)];
}

// *sample* is a coalesced event: its position and pressure are used, the
// buttons come from the real event *ev*.
function penMessage(sample, phase, ev = sample) {
  const [x, y] = where(sample);
  const rubber = ev.button === 5 || (ev.buttons & 32) !== 0;
  const barrel = (ev.buttons & 2) !== 0;
  let pressure = sample.pointerType === 'pen' ? sample.pressure : 0.5;
  if (phase === 'm' && !(pressure > 0)) pressure = ev.pressure || 0.5;
  return ['p', phase, x, y, Math.round(pressure * 1000) / 1000,
          ev.tiltX || 0, ev.tiltY || 0, barrel ? 1 : 0, rubber ? 1 : 0];
}

// Whether the pen (or a mouse button) is down is decided by pointerdown and
// pointerup alone.  Chrome's coalesced in-between samples can carry pressure
// 0 and no buttons, and reading those as "lifted" broke every stroke.
const penDown = new Set();

function directDown(ev) {
  if (ev.pointerType === 'touch') { post(['t', 'd', ev.pointerId, ...where(ev)]); return; }
  penDown.add(ev.pointerId);
  post(penMessage(ev, 'd'));
}

function directMove(ev) {
  const events = ev.getCoalescedEvents ? ev.getCoalescedEvents() : [];
  for (const e of events.length ? events : [ev]) {
    if (ev.pointerType === 'touch') post(['t', 'm', ev.pointerId, ...where(e)]);
    else post(penMessage(e, penDown.has(ev.pointerId) ? 'm' : 'h', ev));
  }
}

function directUp(ev) {
  if (ev.pointerType === 'touch') { post(['t', 'u', ev.pointerId, ...where(ev)]); return; }
  penDown.delete(ev.pointerId);
  post(penMessage(ev, 'u'));
}

function directLeave(ev) {
  if (direct() && ev.pointerType !== 'touch') post(penMessage(ev, 'o'));
}

// --------------------------------------------------------------- keyboard
// A physical keyboard sends keys: its KeyboardEvent.code (the key's place,
// not its letter) becomes the Linux key code, and the computer's own layout
// decides the character, as with a keyboard plugged into it.
const KEYS = {
  Escape: 1, Digit1: 2, Digit2: 3, Digit3: 4, Digit4: 5, Digit5: 6, Digit6: 7,
  Digit7: 8, Digit8: 9, Digit9: 10, Digit0: 11, Minus: 12, Equal: 13,
  Backspace: 14, Tab: 15, KeyQ: 16, KeyW: 17, KeyE: 18, KeyR: 19, KeyT: 20,
  KeyY: 21, KeyU: 22, KeyI: 23, KeyO: 24, KeyP: 25, BracketLeft: 26,
  BracketRight: 27, Enter: 28, ControlLeft: 29, KeyA: 30, KeyS: 31, KeyD: 32,
  KeyF: 33, KeyG: 34, KeyH: 35, KeyJ: 36, KeyK: 37, KeyL: 38, Semicolon: 39,
  Quote: 40, Backquote: 41, ShiftLeft: 42, Backslash: 43, KeyZ: 44, KeyX: 45,
  KeyC: 46, KeyV: 47, KeyB: 48, KeyN: 49, KeyM: 50, Comma: 51, Period: 52,
  Slash: 53, ShiftRight: 54, NumpadMultiply: 55, AltLeft: 56, Space: 57,
  CapsLock: 58, F1: 59, F2: 60, F3: 61, F4: 62, F5: 63, F6: 64, F7: 65, F8: 66,
  F9: 67, F10: 68, NumLock: 69, ScrollLock: 70, Numpad7: 71, Numpad8: 72,
  Numpad9: 73, NumpadSubtract: 74, Numpad4: 75, Numpad5: 76, Numpad6: 77,
  NumpadAdd: 78, Numpad1: 79, Numpad2: 80, Numpad3: 81, Numpad0: 82,
  NumpadDecimal: 83, IntlBackslash: 86, F11: 87, F12: 88, IntlRo: 89,
  NumpadEnter: 96, ControlRight: 97, NumpadDivide: 98, PrintScreen: 99,
  AltRight: 100, Home: 102, ArrowUp: 103, PageUp: 104, ArrowLeft: 105,
  ArrowRight: 106, End: 107, ArrowDown: 108, PageDown: 109, Insert: 110,
  Delete: 111, Pause: 119, MetaLeft: 125, MetaRight: 126, ContextMenu: 127,
};

function onKey(ev) {
  if (!direct() || ev.isComposing || ev.keyCode === 229) return;
  if (ev.target === $('code')) return;        // typing the access code
  const code = KEYS[ev.code];
  if (!code) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (ev.type === 'keydown' && ev.repeat) return;   // the computer repeats
  post(['k', code, ev.type === 'keydown' ? 1 : 0]);
}
document.addEventListener('keydown', onKey, true);
document.addEventListener('keyup', onKey, true);

// The on-screen keyboard sends text, often rewriting a word it is composing.
// What it typed is kept in a hidden field and every change is sent as
// "delete n characters, type this", which also covers autocorrect.
const typing = $('typing');
const SENTINEL = '  ';                 // lets Backspace work on an empty field
let typed = '';

function resetTyping() {
  typing.value = SENTINEL;
  typed = '';
  typing.setSelectionRange(SENTINEL.length, SENTINEL.length);
}

typing.addEventListener('input', () => {
  if (!direct()) return;
  const before = SENTINEL + typed;
  const now = typing.value;
  let same = 0;
  while (same < before.length && same < now.length && before[same] === now[same]) same++;
  for (let i = same; i < before.length; i++) post(['k', 14, 1]), post(['k', 14, 0]);
  const added = now.slice(same);
  if (added) post(['x', added]);
  if (!now.startsWith(SENTINEL) || typed.length > 200 || added.includes('\n')) {
    if (!typing.composing) resetTyping();
    else typed = now.slice(SENTINEL.length);
  } else {
    typed = now.slice(SENTINEL.length);
  }
});
typing.addEventListener('compositionstart', () => { typing.composing = true; });
typing.addEventListener('compositionend', () => { typing.composing = false; });

function toggleKeyboard() {
  if (document.activeElement === typing) {
    typing.blur();
  } else {
    resetTyping();
    typing.focus({ preventScroll: true });
  }
}
typing.addEventListener('focus', () => $('keys').classList.add('on'));
typing.addEventListener('blur', () => $('keys').classList.remove('on'));
$('keys').addEventListener('pointerdown', (ev) => ev.preventDefault());  // keep focus
$('keys').addEventListener('click', toggleKeyboard);

// ------------------------------------------------------------- vnc input
// The fallback when the computer cannot create input devices.
// noVNC turns touches into gestures (a tap clicks only after a delay, a drag
// starts only past a threshold).  For a screen used with a pen and fingers,
// direct input feels better, so input is handled here instead:
//   pen / mouse: down = press, move = move (also while the pen hovers),
//                barrel or right button = right button
//   one finger:  tap = click, drag = drag
//   two fingers: drag = scroll, tap = right click
const LEFT = 0x1, MIDDLE = 0x2, RIGHT = 0x4;
const WHEEL = { up: 0x8, down: 0x10, left: 0x20, right: 0x40 };
const SCROLL_STEP = 40;          // CSS px of finger travel per wheel click
const TOUCH_HOLD = 70;           // ms a lone finger waits for a second one
const touches = new Map();       // pointerId -> {x, y, startX, startY}
let mask = 0;
let pressTimer = 0;
let pendingPress = false;
let scrolling = false;
let scrolled = false;
let scrollRest = { x: 0, y: 0 };

function point(ev) {
  return clientToElement(ev.clientX, ev.clientY, rfb._canvas);
}

function send(x, y, buttons) {
  if (rfb) rfb._sendMouse(x, y, buttons);
}

function buttonsOf(ev) {
  let m = 0;
  if (ev.buttons & 1) m |= LEFT;
  if (ev.buttons & 2) m |= RIGHT;
  if (ev.buttons & 4) m |= MIDDLE;
  if (ev.buttons & 32) m |= LEFT;            // pen eraser end
  return m;
}

function click(x, y, button) {
  send(x, y, 0);
  send(x, y, button);
  send(x, y, 0);
}

function wheel(x, y, button, count) {
  for (let i = 0; i < count; i++) {
    send(x, y, button);
    send(x, y, 0);
  }
}

function startPress() {
  clearTimeout(pressTimer);
  if (!pendingPress) return;
  pendingPress = false;
  const [t] = touches.values();
  if (!t) return;
  mask = LEFT;
  send(t.x, t.y, mask);
}

function onDown(ev) {
  if (!rfb || !document.body.classList.contains('live')) return;
  ev.preventDefault();
  ev.stopPropagation();
  try { $('screen').setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
  if (direct()) { directDown(ev); return; }
  const p = point(ev);
  if (ev.pointerType !== 'touch') {
    mask = buttonsOf(ev) || LEFT;
    send(p.x, p.y, 0);
    send(p.x, p.y, mask);
    return;
  }
  touches.set(ev.pointerId, { x: p.x, y: p.y, startX: ev.clientX, startY: ev.clientY });
  if (touches.size === 1) {
    scrolling = scrolled = false;
    send(p.x, p.y, 0);                       // move there first
    pendingPress = true;
    pressTimer = setTimeout(startPress, TOUCH_HOLD);
  } else if (touches.size === 2 && pendingPress) {
    clearTimeout(pressTimer);                // a second finger: scroll mode
    pendingPress = false;
    scrolling = true;
    scrollRest = { x: 0, y: 0 };
  }
}

function onMove(ev) {
  if (!rfb) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (direct()) { directMove(ev); return; }
  const p = point(ev);
  if (ev.pointerType !== 'touch') {
    mask = buttonsOf(ev);
    send(p.x, p.y, mask);                   // hover moves the cursor too
    return;
  }
  const t = touches.get(ev.pointerId);
  if (!t) return;
  const dx = ev.clientX - t.startX, dy = ev.clientY - t.startY;
  if (scrolling) {
    if (touches.size < 2) return;
    // Both fingers move together; follow the one that moved, halved.
    scrollRest.x += (ev.clientX - (t.lastX ?? t.startX)) / 2;
    scrollRest.y += (ev.clientY - (t.lastY ?? t.startY)) / 2;
    t.lastX = ev.clientX; t.lastY = ev.clientY;
    const [f] = touches.values();
    const vy = Math.trunc(scrollRest.y / SCROLL_STEP);
    const vx = Math.trunc(scrollRest.x / SCROLL_STEP);
    if (vy) { wheel(f.x, f.y, vy > 0 ? WHEEL.up : WHEEL.down, Math.abs(vy)); scrollRest.y -= vy * SCROLL_STEP; scrolled = true; }
    if (vx) { wheel(f.x, f.y, vx > 0 ? WHEEL.left : WHEEL.right, Math.abs(vx)); scrollRest.x -= vx * SCROLL_STEP; scrolled = true; }
    return;
  }
  t.x = p.x; t.y = p.y;
  if (pendingPress && Math.hypot(dx, dy) > 8) startPress();   // a drag
  if (!pendingPress) send(p.x, p.y, mask);
}

function onUp(ev) {
  if (!rfb) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (direct()) { directUp(ev); return; }
  const p = point(ev);
  if (ev.pointerType !== 'touch') {
    mask = buttonsOf(ev);
    send(p.x, p.y, mask);
    return;
  }
  const t = touches.get(ev.pointerId);
  touches.delete(ev.pointerId);
  if (!t) return;
  if (scrolling) {
    if (touches.size === 0) {
      if (!scrolled) click(t.x, t.y, RIGHT);   // two-finger tap
      scrolling = false;
    }
    return;
  }
  if (pendingPress) {                          // a quick tap
    clearTimeout(pressTimer);
    pendingPress = false;
    click(t.x, t.y, LEFT);
    return;
  }
  mask = 0;
  send(t.x, t.y, 0);
}

const screenEl = $('screen');
screenEl.addEventListener('pointerdown', onDown, true);
screenEl.addEventListener('pointermove', onMove, true);
screenEl.addEventListener('pointerup', onUp, true);
screenEl.addEventListener('pointercancel', onUp, true);
screenEl.addEventListener('pointerleave', directLeave, true);
screenEl.addEventListener('wheel', (ev) => {
  if (!direct()) return;                 // noVNC handles it over VNC
  ev.preventDefault();
  ev.stopPropagation();
  const step = ev.deltaMode === 1 ? 3 : ev.deltaMode === 2 ? 0.1 : 100;
  post(['w', -ev.deltaX / step, -ev.deltaY / step]);
}, { capture: true, passive: false });
// Keep noVNC's own mouse and touch handling out of the way.
for (const type of ['mousedown', 'mouseup', 'mousemove', 'click', 'contextmenu',
                    'touchstart', 'touchmove', 'touchend', 'touchcancel']) {
  screenEl.addEventListener(type, (ev) => {
    ev.stopPropagation();
    if (ev.cancelable) ev.preventDefault();
  }, { capture: true, passive: false });
}

// Rotating the tablet changes its screen: reconnect so the computer can
// resize the virtual screen to match.
let resizeTimer = 0;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (rfb && screenSize().join('x') !== shownSize) rfb.disconnect();
  }, 800);
});

// ------------------------------------------------------------- stay awake
// The Wake Lock API exists only on HTTPS pages, and without openssl on the
// computer this page is plain HTTP.  Android also keeps the screen on while a
// video plays, so a tiny silent black video loops as well.  Playing needs a user
// gesture, hence the first touch starts it.
const keepAwake = document.createElement('video');
keepAwake.src = 'keepawake.mp4';
keepAwake.muted = true;
keepAwake.loop = true;
keepAwake.playsInline = true;
keepAwake.setAttribute('playsinline', '');
keepAwake.setAttribute('aria-hidden', 'true');
// Full size behind the screen: Chrome only counts a video it considers
// visible, and a 1-pixel or transparent one did not keep the screen on.
keepAwake.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;object-fit:cover;z-index:-1;pointer-events:none';
document.body.prepend(keepAwake);
let wakeLock = null;

async function stayAwake() {
  if (window.isSecureContext && navigator.wakeLock) {
    try {
      if (!wakeLock) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => { wakeLock = null; });
      }
      return;
    } catch (e) { /* fall back to the video */ }
  }
  if (keepAwake.paused) keepAwake.play().catch(() => {});
}

document.addEventListener('pointerdown', stayAwake, true);
document.addEventListener('pointerdown', startSound, true);
document.addEventListener('keydown', startSound, true);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && navigator.userActivation?.hasBeenActive) stayAwake();
});

function goFull() {
  document.documentElement.requestFullscreen({ navigationUI: 'hide' })
    .catch(() => {});
}

$('full').addEventListener('click', goFull);
$('fullmini').addEventListener('click', goFull);
document.addEventListener('fullscreenchange', updateChrome);
document.addEventListener('contextmenu', (e) => e.preventDefault());
$('codeform').addEventListener('submit', (e) => {
  e.preventDefault();
  code = $('code').value.trim().toLowerCase();
  if (code) connect();
});

applyStrings();
updateChrome();
connect();
