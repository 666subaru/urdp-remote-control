// The tablet side of URDP's tablet screen: asks the computer for the screen,
// draws it with noVNC and keeps reconnecting until the computer stops sharing.
import RFB from './novnc/core/rfb.js';

const $ = (id) => document.getElementById(id);
const STORE = 'urdp-tablet-code';

let strings = {};
let rfb = null;
let failures = 0;
let timer = 0;
let shownSize = '';

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
    document.body.classList.add('live');
    status(text('connected', 'Connected'));
    updateChrome();
  });
  rfb.addEventListener('disconnect', () => {
    rfb = null;
    document.body.classList.remove('live');
    updateChrome();
    retryLater();
  });
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
