// Microphone capture for WebSocket audio: hands each 128-sample block of the microphone to the page (audio.js), which packs it into 20 ms Opus packets.
class RrCapture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice());
    return true;
  }
}
registerProcessor("rr-capture", RrCapture);
