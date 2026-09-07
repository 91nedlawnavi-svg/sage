class SagePlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.port.onmessage = (event) => {
      if (event.data === "clear") {
        this.queue = [];
        this.offset = 0;
      } else if (event.data instanceof Float32Array) {
        this.queue.push(event.data);
      }
    };
  }

  process(_inputs, outputs) {
    const channel = outputs[0]?.[0];
    if (!channel) return true;
    channel.fill(0);
    let outputOffset = 0;
    while (outputOffset < channel.length && this.queue.length) {
      const current = this.queue[0];
      const count = Math.min(channel.length - outputOffset, current.length - this.offset);
      channel.set(current.subarray(this.offset, this.offset + count), outputOffset);
      outputOffset += count;
      this.offset += count;
      if (this.offset === current.length) {
        this.queue.shift();
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("sage-playback", SagePlaybackProcessor);
