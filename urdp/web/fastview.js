// The fast picture: the computer sends the screen as JPEGs over a WebSocket,
// and each is drawn as it arrives.  When frames come faster than the tablet
// decodes them, only the newest waits, so the picture never lags behind.
// It offers what tablet.js uses of noVNC's RFB (the canvas, connect and
// disconnect events, disconnect()), so the rest of the page works the same
// with either picture.
export default class FastView extends EventTarget {
  constructor(target, url) {
    super();
    this._canvas = document.createElement('canvas');
    this._canvas.className = 'fast';
    this._context = this._canvas.getContext('2d', { alpha: false });
    target.appendChild(this._canvas);
    this.everConnected = false;
    this._pending = null;
    this._drawing = false;
    this._gone = false;
    this._fit = () => this._resize();
    window.addEventListener('resize', this._fit);
    this._sock = new WebSocket(url);
    this._sock.binaryType = 'arraybuffer';
    this._sock.addEventListener('message', (ev) => this._frame(ev.data));
    this._sock.addEventListener('close', () => this._closed());
    this._sock.addEventListener('error', () => this._sock.close());
  }

  _frame(data) {
    this._pending = data;
    if (!this._drawing) this._draw();
  }

  async _draw() {
    this._drawing = true;
    while (this._pending && !this._gone) {
      const data = this._pending;
      this._pending = null;
      let image;
      try {
        image = await createImageBitmap(new Blob([data], { type: 'image/jpeg' }));
      } catch (e) {
        continue;                          // a damaged frame: wait for the next
      }
      if (image.width !== this._canvas.width || image.height !== this._canvas.height) {
        this._canvas.width = image.width;
        this._canvas.height = image.height;
        this._resize();
      }
      this._context.drawImage(image, 0, 0);
      image.close();
      if (!this.everConnected) {
        this.everConnected = true;
        this.dispatchEvent(new Event('connect'));
      }
    }
    this._drawing = false;
  }

  // As large as fits, centred, keeping the screen's shape (like noVNC's
  // scaleViewport).
  _resize() {
    const w = this._canvas.width, h = this._canvas.height;
    if (!w || !h) return;
    const scale = Math.min(window.innerWidth / w, window.innerHeight / h);
    Object.assign(this._canvas.style, {
      width: `${w * scale}px`,
      height: `${h * scale}px`,
      left: `${(window.innerWidth - w * scale) / 2}px`,
      top: `${(window.innerHeight - h * scale) / 2}px`,
    });
  }

  _closed() {
    if (this._gone) return;
    this._gone = true;
    window.removeEventListener('resize', this._fit);
    this._canvas.remove();
    this.dispatchEvent(new Event('disconnect'));
  }

  disconnect() {
    this._sock.close();
    this._closed();
  }

  // The VNC fallback input of tablet.js has nowhere to go here.
  _sendMouse() {}
}
