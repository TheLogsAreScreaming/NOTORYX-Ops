// SARNEG: Search And Rescue Numerical Encryption Grid.
//
// Same wire format as the NOTORYX phone apps:
//   "[SARNEG] LRs KSAC" = "17S 4823" with the word BLACKHORSE.
// Each digit is sent as the code letter at that index (UPPER case); every
// other letter goes in lower case so it is never read as a digit.
// Code words are shared as "SARNEG1:WORD;yyyy-mm-dd=WORD;...". A dated word
// replaces the main word on that local calendar day.
//
// This hides numbers from a casual listener. It is not encryption.

export const SARNEG_MARKER = "[SARNEG]";
export const SARNEG_SHARE_PREFIX = "SARNEG1:";

export type SarnegKeys = { word: string; daily: Record<string, string> };

export function normalizeWord(w: string): string {
  return w.replace(/\s/g, "").toUpperCase();
}

/** Why a word cannot be used, or null when it can. */
export function wordProblem(w: string): string | null {
  if (!/^[A-Z]*$/.test(w)) return "Use only the letters A to Z";
  if (w.length !== 10) return "Use exactly 10 letters";
  if (new Set(w).size !== 10) return "No letter may appear twice";
  return null;
}

export function isSarneg(text: string): boolean {
  return text.trimStart().startsWith(SARNEG_MARKER);
}

/** `yyyy-mm-dd` for the local day of `t` (Unix seconds or a Date). */
export function sarnegDay(t: number | Date): string {
  const d = typeof t === "number" ? new Date(t * 1000) : t;
  const two = (v: number) => String(v).padStart(2, "0");
  return `${String(d.getFullYear()).padStart(4, "0")}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

export function wordFor(keys: SarnegKeys, t: number | Date): string {
  return keys.daily[sarnegDay(t)] ?? keys.word;
}

export function encode(plain: string, word: string): string {
  let out = "";
  for (const ch of plain) out += /[0-9]/.test(ch) ? word[Number(ch)] : ch.toLowerCase();
  return out;
}

export function decode(coded: string, word: string): string {
  let out = "";
  for (const ch of coded) {
    const i = word.indexOf(ch);
    out += i >= 0 ? String(i) : ch;
  }
  return out;
}

/** The plain text of a SARNEG message, or null when it has no marker. */
export function decodeMessage(text: string, word: string): string | null {
  const t = text.trimStart();
  if (!t.startsWith(SARNEG_MARKER)) return null;
  return decode(t.slice(SARNEG_MARKER.length).trim(), word);
}

/** Reads "SARNEG1:WORD;day=WORD" or a bare word. Throws Error with a reason. */
export function parseKeys(text: string): SarnegKeys {
  let t = text.trim();
  if (t.toUpperCase().startsWith(SARNEG_SHARE_PREFIX)) t = t.slice(SARNEG_SHARE_PREFIX.length);
  const parts = t.split(";");
  const word = normalizeWord(parts[0]);
  const p = wordProblem(word);
  if (p) throw new Error(p);
  const daily: Record<string, string> = {};
  for (const part of parts.slice(1)) {
    if (!part.trim()) continue;
    const eq = part.indexOf("=");
    if (eq < 0) throw new Error("A dated word is missing its date");
    const day = part.slice(0, eq).trim();
    const w = normalizeWord(part.slice(eq + 1));
    const d = new Date(`${day}T12:00:00`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || isNaN(d.getTime()) || sarnegDay(d) !== day) throw new Error(`Not a date: ${day}`);
    if (wordProblem(w)) throw new Error(`The word for ${day} is not 10 different letters`);
    daily[day] = w;
  }
  return { word, daily };
}

const STORE = "notoryx-ops-sarneg";

/** The code words kept in this browser only (never sent to the station). */
export function loadKeys(): SarnegKeys | null {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return null;
    const k = JSON.parse(raw) as SarnegKeys;
    return wordProblem(k.word) ? null : { word: k.word, daily: k.daily || {} };
  } catch {
    return null;
  }
}

export function saveKeys(k: SarnegKeys | null): void {
  try {
    if (k) localStorage.setItem(STORE, JSON.stringify(k));
    else localStorage.removeItem(STORE);
  } catch {
    // private window or blocked storage: the words last until the page closes
  }
}
