// Caption text tidied on the laptop exactly as the server's asr.tidy does it: spelled-out
// acronyms joined ("U K" -> "UK"), [MUSIC] for a line of nothing but filler, and optional
// sentence case keeping the vocabulary's spelling. Kept free of Electron so tests can load it.
const ACRONYM = /\b([A-Za-z])(?:\s+([A-Za-z]))+\b/g;
const FILLER = new Set(["um", "uh", "umm", "uhh", "erm", "hmm", "mm", "mmm", "huh"]);
const MUSIC_LABEL = "[MUSIC]";
const I_FORMS = { I: "I", "I'M": "I'm", "I'D": "I'd", "I'LL": "I'll", "I'VE": "I've" };

function caseWords(vocabulary) {
  const out = {};
  for (const phrase of vocabulary || []) {
    for (const w of String(phrase).split(/\s+/)) if (/\p{L}/u.test(w) && !(w.toUpperCase() in out)) out[w.toUpperCase()] = w;
  }
  return out;
}

function isFillerSpam(text) {
  const words = text.toLowerCase().match(/[a-z']+/g) || [];
  return words.length > 0 && words.every((w) => FILLER.has(w));
}

function tidy(text, options = {}, keep = {}) {
  text = String(text || "").trim();
  const acronyms = new Set();
  if (options.join_acronyms !== false) {
    text = text.replace(ACRONYM, (m) => { const a = m.replace(/\s+/g, "").toUpperCase(); acronyms.add(a); return a; });
  }
  if (options.sentence_case) {
    const one = (w) => {
      const up = w.toUpperCase();
      if (up in keep) return keep[up];
      if (up in I_FORMS) return I_FORMS[up];
      if (acronyms.has(up) || w === MUSIC_LABEL) return w;
      return w.toLowerCase();
    };
    text = text.split(/\s+/).filter(Boolean).map(one).join(" ");
    text = text.slice(0, 1).toUpperCase() + text.slice(1);
  }
  return text;
}

module.exports = { tidy, caseWords, isFillerSpam, MUSIC_LABEL };
