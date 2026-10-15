// The captions engine, in its own process (see captions.js). It takes the mic as 16 kHz
// 16-bit PCM, applies the room's gain and EQ, recognises speech and reports what it heard
// (raw, for the server to tidy exactly as it tidies its own) and the mic level. The commit
// rules and the EQ curve are the server's (server/atsuit/asr.py and dsp.py).
const fs = require("fs");
const path = require("path");
const sherpa = require("sherpa-onnx-node");

const RATE = 16000;
const RTA_BANDS = 31, RTA_MIN = 20, RTA_MAX = RATE / 2, RTA_FLOOR = -60;
const port = process.parentPort;

let rec = null, next = null, stream = null, key = "";
let running = false, lastPartial = "", lastLevel = 0;
let gain = 1, filters = [];

function load({ dir, key: k, hotwordsScore }) {
  try {
    const hot = path.join(dir, "hotwords.txt");
    const hasHot = fs.existsSync(hot) && fs.statSync(hot).size > 0;
    const r = new sherpa.OnlineRecognizer({
      featConfig: { sampleRate: RATE, featureDim: 80 },
      modelConfig: {
        transducer: { encoder: path.join(dir, "encoder.onnx"), decoder: path.join(dir, "decoder.onnx"), joiner: path.join(dir, "joiner.onnx") },
        tokens: path.join(dir, "tokens.txt"), numThreads: 2, provider: "cpu", debug: 0,
      },
      decodingMethod: "modified_beam_search", maxActivePaths: 6,
      enableEndpoint: 1, rule1MinTrailingSilence: 2.4, rule2MinTrailingSilence: 1.2, rule3MinUtteranceLength: 300,
      hotwordsFile: hasHot ? hot : "", hotwordsScore: hasHot ? hotwordsScore : 0,
    });
    if (rec && running) next = r; // new vocabulary: switch at the next pause, so nobody restarts the mic
    else { rec = r; stream = null; }
    key = k;
    port.postMessage({ type: "loaded", key });
  } catch (e) {
    port.postMessage({ type: "load-error", detail: `The speech model didn't load: ${e.message || e}` });
  }
}

// RBJ peaking biquads, one per band, as the server's EQ (it applies the same curve as a FIR).
function peaking(f0, db, q) {
  const a = 10 ** (db / 40), w0 = (2 * Math.PI * f0) / RATE, alpha = Math.sin(w0) / (2 * q), c = Math.cos(w0);
  const a0 = 1 + alpha / a;
  return { b0: (1 + alpha * a) / a0, b1: (-2 * c) / a0, b2: (1 - alpha * a) / a0, a1: (-2 * c) / a0, a2: (1 - alpha / a) / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
}

function setAudio(audio) {
  if (!audio) return;
  gain = 10 ** (Math.max(-20, Math.min(20, +audio.gain_db || 0)) / 20);
  const bands = audio.eq_bands_hz || [];
  filters = (audio.eq_band_gains_db || []).map((g, i) => (g && bands[i] < RATE / 2 ? peaking(bands[i], Math.max(-12, Math.min(12, g)), audio.band_q || 1.4) : null)).filter(Boolean);
}

function toI16(pcm) {
  if (pcm instanceof ArrayBuffer) return new Int16Array(pcm, 0, pcm.byteLength >> 1);
  const copy = new Uint8Array(pcm.byteLength); // an aligned copy, whatever the IPC handed over
  copy.set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength));
  return new Int16Array(copy.buffer, 0, copy.byteLength >> 1);
}

function process16(pcm) {
  const i16 = toI16(pcm);
  const out = new Float32Array(i16.length);
  for (let n = 0; n < i16.length; n++) {
    let x = (i16[n] / 32768) * gain;
    for (const f of filters) {
      const y = f.b0 * x + f.b1 * f.x1 + f.b2 * f.x2 - f.a1 * f.y1 - f.a2 * f.y2;
      f.x2 = f.x1; f.x1 = x; f.y2 = f.y1; f.y1 = y; x = y;
    }
    out[n] = x > 1 ? 1 : x < -1 ? -1 : x;
  }
  return out;
}

function dbfs(s) {
  let sum = 0;
  for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
  const rms = s.length ? Math.sqrt(sum / s.length) : 0;
  return rms <= 1e-8 ? -60 : Math.round(Math.max(-60, 20 * Math.log10(rms)) * 10) / 10;
}

// The analyser: log-spaced bands in dBFS, as dsp.compute_rta_bands.
function rta(s) {
  const n = s.length;
  if (n < 8) return new Array(RTA_BANDS).fill(RTA_FLOOR);
  let size = 1; while (size < n) size <<= 1;
  const re = new Float64Array(size), im = new Float64Array(size);
  let wsum = 0;
  for (let i = 0; i < n; i++) { const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1)); wsum += w; re[i] = s[i] * w; }
  for (let i = 1, j = 0; i < size; i++) { // in-place radix-2 FFT
    let bit = size >> 1; for (; j & bit; bit >>= 1) j ^= bit; j ^= bit;
    if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
  }
  for (let len = 2; len <= size; len <<= 1) {
    const ang = (-2 * Math.PI) / len, wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < size; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2, tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
        const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
      }
    }
  }
  const scale = 2 / (wsum || 1), out = [];
  const lo = Math.log10(RTA_MIN), hi = Math.log10(RTA_MAX);
  for (let b = 0; b < RTA_BANDS; b++) {
    const f1 = 10 ** (lo + ((hi - lo) * b) / RTA_BANDS), f2 = 10 ** (lo + ((hi - lo) * (b + 1)) / RTA_BANDS);
    let peak = 0;
    for (let k = Math.ceil((f1 * size) / RATE); k * RATE < f2 * size && k <= size / 2; k++) peak = Math.max(peak, Math.hypot(re[k], im[k]) * scale);
    const rms = peak / Math.SQRT2;
    out.push(Math.round(Math.max(RTA_FLOOR, Math.min(0, rms > 1e-8 ? 20 * Math.log10(rms) : RTA_FLOOR)) * 10) / 10);
  }
  return out;
}

function result() {
  const r = rec.getResult(stream);
  return { text: String(r.text || "").trim(), tokens: r.tokens || [], ys_probs: r.ys_probs || [] };
}

function feed(pcm) {
  if (!rec || !running) return;
  if (!stream) stream = rec.createStream();
  const s = process16(pcm);
  const now = Date.now();
  if (now - lastLevel > 250) { lastLevel = now; port.postMessage({ type: "level", db: dbfs(s), rta: rta(s) }); }
  stream.acceptWaveform({ sampleRate: RATE, samples: s });
  while (rec.isReady(stream)) rec.decode(stream);
  const r = result();
  if (rec.isEndpoint(stream)) {
    if (r.text) port.postMessage({ type: "final", ...r });
    rec.reset(stream);
    lastPartial = "";
    if (next) { rec = next; next = null; stream = rec.createStream(); }
  } else if (r.text && r.text !== lastPartial) {
    lastPartial = r.text;
    port.postMessage({ type: "partial", ...r });
  }
}

port.on("message", (e) => {
  const m = e.data;
  if (m.type === "load") load(m);
  else if (m.type === "start") { setAudio(m.audio); running = true; lastPartial = ""; if (rec) stream = rec.createStream(); }
  else if (m.type === "audio") setAudio(m.audio);
  else if (m.type === "pcm") { try { feed(m.pcm); } catch (err) { port.postMessage({ type: "error", detail: String(err.message || err) }); } }
  else if (m.type === "stop") {
    if (rec && stream && running) { // whatever was being said when the mic stopped
      stream.inputFinished();
      while (rec.isReady(stream)) rec.decode(stream);
      const r = result();
      if (r.text) port.postMessage({ type: "final", ...r });
    }
    running = false; stream = null; lastPartial = "";
    if (next) { rec = next; next = null; }
  }
});
