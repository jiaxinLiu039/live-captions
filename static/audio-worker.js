/**
 * AudioWorklet Processor — 音频采集 + 下采样到 16kHz mono PCM16
 * 每 100ms (1600 samples @16kHz) 输出一帧
 * 使用平均值下采样（简易低通）代替点采样，减少混叠噪声
 */
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(16384);
    this._bufferLen = 0;
    this._inputSampleRate = sampleRate; // e.g. 48000
    this._targetRate = 16000;
    this._frameSize = 1600; // 100ms @16kHz
    this._energyFrameCount = 0;
    this._energySum = 0;
    // Debug counters（仅诊断用，正常流程不受影响）
    this._dbgCalls = 0;
    this._dbgSamples = 0;
    this._dbgEnergy = 0;
  }

  process(inputs) {
    const input = inputs[0];

    // --- debug: 每 ~0.5s 汇报一次采集链路健康状态 ---
    this._dbgCalls++;
    if (input && input.length && input[0]) {
      const ch0 = input[0];
      this._dbgSamples += ch0.length;
      for (let i = 0; i < ch0.length; i++) this._dbgEnergy += ch0[i] * ch0[i];
    }
    if (this._dbgSamples >= this._inputSampleRate * 0.5) {
      const rms = Math.sqrt(this._dbgEnergy / Math.max(1, this._dbgSamples));
      this.port.postMessage({
        type: 'dbg',
        calls: this._dbgCalls,
        chCount: input ? input.length : 0,
        ch0Len: (input && input.length) ? (input[0] ? input[0].length : 0) : 0,
        rms: +rms.toFixed(5),
      });
      this._dbgSamples = 0;
      this._dbgEnergy = 0;
    }
    // --- end debug ---

    if (!input || !input.length) return true;

    const ch0 = input[0];
    if (!ch0) return true;

    // Grow buffer if needed
    while (this._bufferLen + ch0.length > this._buffer.length) {
      const newBuf = new Float32Array(this._buffer.length * 2);
      newBuf.set(this._buffer.subarray(0, this._bufferLen));
      this._buffer = newBuf;
    }

    // Accumulate samples
    this._buffer.set(ch0, this._bufferLen);
    this._bufferLen += ch0.length;

    // Calculate energy for visualization (~50ms intervals)
    for (let i = 0; i < ch0.length; i++) {
      this._energySum += ch0[i] * ch0[i];
    }
    this._energyFrameCount += ch0.length;

    if (this._energyFrameCount >= this._inputSampleRate * 0.05) {
      const rms = Math.sqrt(this._energySum / this._energyFrameCount);
      this.port.postMessage({ type: 'energy', rms });
      this._energySum = 0;
      this._energyFrameCount = 0;
    }

    // Downsample and emit PCM16 frames
    const ratio = this._inputSampleRate / this._targetRate; // e.g. 3.0 for 48k->16k
    const samplesNeeded = Math.ceil(this._frameSize * ratio);

    while (this._bufferLen >= samplesNeeded) {
      const frame = new Int16Array(this._frameSize);

      for (let i = 0; i < this._frameSize; i++) {
        // Average-based downsampling (acts as low-pass filter)
        const startIdx = Math.floor(i * ratio);
        const endIdx = Math.floor((i + 1) * ratio);
        let sum = 0;
        let count = 0;
        for (let j = startIdx; j < endIdx && j < this._bufferLen; j++) {
          sum += this._buffer[j];
          count++;
        }
        let s = count > 0 ? sum / count : 0;
        // Clamp and quantize to int16
        s = Math.max(-1, Math.min(1, s));
        frame[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }

      // Send PCM16 to main thread
      this.port.postMessage({ type: 'pcm', pcm16: frame.buffer }, [frame.buffer]);

      // Shift buffer: remove consumed samples
      const consumed = Math.floor(this._frameSize * ratio);
      this._buffer.copyWithin(0, consumed, this._bufferLen);
      this._bufferLen -= consumed;
    }

    return true;
  }
}

registerProcessor('capture-processor', CaptureProcessor);
