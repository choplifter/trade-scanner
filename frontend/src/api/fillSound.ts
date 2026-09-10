/**
 * A short chime when something fills, and the watcher that decides when
 * that is.
 *
 * No audio file: two sine notes from the Web Audio API, built on first
 * use. A file would be one more asset to ship and cache for a sound that
 * lasts a fifth of a second.
 *
 * What counts as a fill is deliberately narrow. The app polls what the
 * account *holds* -- equity positions and option packages -- so a change
 * in what is held is a fill by definition, whichever side it was on. It
 * is not read off orders leaving the open list: an order can leave that
 * list by being cancelled or rejected, and a chime for those would train
 * the ear to ignore it.
 *
 * The first snapshot of each kind is only recorded, never sounded, or
 * every page load would chime for positions that were there all along.
 */

import { getSettings } from "./settings";

let context: AudioContext | null = null;

function tone(ctx: AudioContext, at: number, hz: number, seconds: number, gain: number): void {
  const osc = ctx.createOscillator();
  const vol = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = hz;
  // A short ramp either side: a square-edged note clicks.
  vol.gain.setValueAtTime(0, at);
  vol.gain.linearRampToValueAtTime(gain, at + 0.012);
  vol.gain.exponentialRampToValueAtTime(0.0001, at + seconds);
  osc.connect(vol).connect(ctx.destination);
  osc.start(at);
  osc.stop(at + seconds + 0.02);
}

/** Two rising notes. Silent when the setting is off, and silent rather
 * than throwing where the browser refuses audio (no gesture yet, or a
 * device that has none). */
export function playFillChime(): void {
  if (!getSettings().fillSound) return;
  try {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    context = context ?? new Ctor();
    if (context.state === "suspended") void context.resume();
    const now = context.currentTime;
    tone(context, now, 880, 0.09, 0.12);
    tone(context, now + 0.1, 1318.5, 0.16, 0.1);
  } catch {
    // A chime is a courtesy; never let it break the poll that called it.
  }
}

/** The last fingerprint per kind of holding. Module-level so the pollers
 * that report in can live in different components. */
const seen = new Map<string, Map<string, string>>();

/**
 * Report what a poll now sees. `holdings` maps a stable key (a symbol, a
 * package id) to a fingerprint of its size, so a quantity change counts
 * as much as an arrival or a disappearance.
 *
 * Chimes at most once per report, however many holdings moved: one fill
 * of a four-legged package is one event, not four.
 */
export function noteHoldings(kind: string, holdings: Map<string, string>): void {
  const before = seen.get(kind);
  seen.set(kind, holdings);
  if (!before) return;
  let changed = holdings.size !== before.size;
  if (!changed) {
    for (const [key, value] of holdings) {
      if (before.get(key) !== value) {
        changed = true;
        break;
      }
    }
  }
  if (changed) playFillChime();
}

/** Forget what was seen -- on a mode switch, where the whole book changes
 * for a reason that is not a fill. */
export function resetHoldings(): void {
  seen.clear();
}
