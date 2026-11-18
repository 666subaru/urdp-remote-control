// Cuts the microphone into 10 ms pieces of 16-bit mono for the computer.
// Runs in the audio thread; tablet.js sends each piece over a WebSocket.
class MicPieces extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = Math.round(sampleRate / 100);
    this.piece = new Int16Array(this.size);
    this.filled = 0;
  }

  process(inputs) {
    const channels = inputs[0];
    if (!channels || !channels.length) return true;
    const first = channels[0];
    for (let i = 0; i < first.length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i];
      value /= channels.length;
      this.piece[this.filled++] = Math.max(-32768, Math.min(32767, Math.round(value * 32768)));
      if (this.filled === this.size) {
        this.port.postMessage(this.piece.buffer, [this.piece.buffer]);
        this.piece = new Int16Array(this.size);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('urdp-mic', MicPieces);
