// The tablet side of URDP's tablet screen: asks the computer for the screen,
// draws it with noVNC and keeps reconnecting until the computer stops sharing.
import RFB from './novnc/core/rfb.js';
import { encodings } from './novnc/core/encodings.js';
import { clientToElement } from './novnc/core/util/element.js';

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
  let answer;
  try {
    const query = new URLSearchParams({ k: code, w, h, dpr });
    const reply = await fetch('hello?' + query, { cache: 'no-store' });
    answer = await reply.json();
  } catch (e) {
    retryLater();
    return;
  }
  strings = answer.strings || strings;
  // URDP was updated while this page stayed open: load the new page.
  if (answer.version) {
    if (pageVersion && answer.version !== pageVersion) { location.reload(); return; }
    pageVersion = answer.version;
  }
  applyStrings();
  if (!answer.ok) {
    askCode(!!code);
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
  const url = `${scheme}://${location.host}/ws?k=${encodeURIComponent(code)}`;
  rfb = new RFB($('screen'), url, {
    credentials: { password: answer.password },
    shared: true,
    wsProtocols: ['binary'],
  });
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.qualityLevel = 8;
  rfb.compressionLevel = 2;
  rfb.focusOnClick = false;
  rfb.addEventListener('connect', () => {
    failures = 0;
    if (answer.input) openInput();
    document.body.classList.add('live');
    status(text('connected', 'Connected'));
    updateChrome();
  });
  rfb.addEventListener('disconnect', () => {
    rfb = null;
    if (inputSock) inputSock.close();
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
function openInput() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const sock = new WebSocket(`${scheme}://${location.host}/input?k=${encodeURIComponent(code)}`);
  sock.addEventListener('open', () => { inputSock = sock; });
  sock.addEventListener('close', () => { if (inputSock === sock) inputSock = null; });
  sock.addEventListener('error', () => sock.close());
}

function direct() { return inputSock && inputSock.readyState === WebSocket.OPEN; }

function post(message) { inputSock.send(JSON.stringify(message)); }

function unit(value) { return Math.round(Math.min(1, Math.max(0, value)) * 1e5) / 1e5; }

function where(ev) {
  const r = rfb._canvas.getBoundingClientRect();
  return [unit((ev.clientX - r.left) / r.width), unit((ev.clientY - r.top) / r.height)];
}

function penMessage(ev, phase) {
  const [x, y] = where(ev);
  const rubber = ev.button === 5 || (ev.buttons & 32) !== 0;
  const barrel = (ev.buttons & 2) !== 0;
  const pressure = ev.pointerType === 'pen' ? ev.pressure : 0.5;
  return ['p', phase, x, y, Math.round(pressure * 1000) / 1000,
          ev.tiltX || 0, ev.tiltY || 0, barrel ? 1 : 0, rubber ? 1 : 0];
}

function touching(ev) {
  if (ev.pointerType === 'pen') return ev.pressure > 0 || (ev.buttons & 33) !== 0;
  return (ev.buttons & 7) !== 0;                 // a mouse on the tablet
}

function directDown(ev) {
  if (ev.pointerType === 'touch') post(['t', 'd', ev.pointerId, ...where(ev)]);
  else post(penMessage(ev, 'd'));
}

function directMove(ev) {
  const events = ev.getCoalescedEvents ? ev.getCoalescedEvents() : [];
  for (const e of events.length ? events : [ev]) {
    if (ev.pointerType === 'touch') post(['t', 'm', ev.pointerId, ...where(e)]);
    else post(penMessage(e, touching(e) ? 'm' : 'h'));
  }
}

function directUp(ev) {
  if (ev.pointerType === 'touch') post(['t', 'u', ev.pointerId, ...where(ev)]);
  else post(penMessage(ev, touching(ev) ? 'm' : 'u'));
}

function directLeave(ev) {
  if (direct() && ev.pointerType !== 'touch') post(penMessage(ev, 'o'));
}

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
// The Wake Lock API exists only on HTTPS pages, and this page is plain HTTP
// on the local network.  Android also keeps the screen on while a video
// plays, so a tiny silent black video loops instead.  Playing needs a user
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
