/* boot.ts — the killchain boot film.
 *
 * Plain browser TypeScript. No imports, no dependencies, no Astro APIs, no
 * network. Boot.astro renders the finished frame as static markup; this module
 * only decides which shot is on screen and when the reveal animations start.
 *
 * Two clocks, on purpose:
 *
 *   1. SHOTS. Hard cuts, driven by timers here, written to `data-shot` on the
 *      root. A cut is a cut: no crossfade, no easing.
 *   2. Reveals. Typing and panel reveals are CSS animations gated on
 *      `[data-boot="play"]`. Removing the attribute cancels them; adding it
 *      back restarts them from zero, which is what makes replay exact.
 *
 * The resting DOM is the FINAL FRAME: with no `data-boot` the markup shows the
 * end shot with everything revealed. So skip(), reduced-motion, and a browser
 * with JS off all land on the same frame.
 *
 * All on-screen material is real: the shipped CLI (README "Run"), routing.py
 * DIMENSIONS and THRESHOLDS, gate.py HARD_RULES/FRAMING_HINTS, the paths from
 * paths.py, and the contract shape from contract.py.
 */

/* ------------------------------------------------------------- material */

export interface Dimension {
  id: string;
  title: string;
  /** routing.THRESHOLDS, verbatim. */
  bar: number;
}

/** routing.py DIMENSIONS + THRESHOLDS, in source order. */
export const DIMENSIONS: readonly Dimension[] = [
  { id: 'attribution', title: 'brand, clone or licence framing', bar: 0.5 },
  { id: 'reverse_engineering', title: 'reverse engineering', bar: 0.45 },
  { id: 'offensive_security', title: 'security and exploitation', bar: 0.5 },
  { id: 'extraction', title: 'harvesting and terms of service', bar: 0.55 },
  { id: 'regulated', title: 'regulated domain', bar: 0.55 },
  { id: 'proprietary_model', title: "another vendor's model or tooling", bar: 0.55 },
  { id: 'hedge_framing', title: 'hedgy or unverifiable phrasing', bar: 0.6 },
  { id: 'scale_or_scope', title: 'scope the agent will negotiate away', bar: 0.6 },
];

/** One beat of the film. `at` is the hard cut-in, in seconds. */
export interface Shot {
  id: string;
  at: number;
  /** the tab label for this beat */
  label: string;
}

export const SHOTS: readonly Shot[] = [
  { id: 'boot', at: 0, label: 'run' },
  { id: 'map', at: 4.2, label: 'risk map' },
  { id: 'gate', at: 11.0, label: 'outbound view' },
  { id: 'split', at: 13.2, label: 'the split' },
  { id: 'close', at: 19.0, label: 'why' },
  { id: 'end', at: 22.0, label: 'ship' },
];

/** The film is over when the end shot has finished revealing. */
export const DURATION = 23.6;

/* ----------------------------------------------------------- bar glyphs */

const EIGHTHS = ['', '\u258F', '\u258E', '\u258D', '\u258C', '\u258B', '\u258A', '\u2589'];

/**
 * A value in [0,1] as a fixed-width bar of `cells` block cells, left-filled,
 * with the remaining space as light shade. Returns the text plus how many
 * cells are filled, so the caller can colour the filled part.
 */
export function bar(value: number, cells = 4): { text: string; on: number } {
  const eighths = Math.max(0, Math.min(cells * 8, Math.round(value * cells * 8)));
  const full = Math.floor(eighths / 8);
  const rem = eighths % 8;
  const head = '\u2588'.repeat(full) + (rem ? EIGHTHS[rem] : '');
  const used = full + (rem ? 1 : 0);
  return { text: head + '\u2591'.repeat(Math.max(0, cells - used)), on: used };
}

/* ---------------------------------------------------------------- clock */

const ROOT_SEL = '.boot';

interface BootState {
  root: HTMLElement;
  timers: number[];
  unkeys: (() => void) | null;
  mq: MediaQueryList | null;
  onMotion: ((e: MediaQueryListEvent) => void) | null;
}

let state: BootState | null = null;

export interface BootHandle {
  replay: () => void;
  skip: () => void;
  seek: (shotId: string) => void;
  destroy: () => void;
  readonly duration: number;
  readonly shots: readonly Shot[];
}

function prefersReduced(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

function clearTimers(s: BootState): void {
  for (const id of s.timers) window.clearTimeout(id);
  s.timers = [];
}

function cut(root: HTMLElement, id: string): void {
  root.dataset.shot = id;
  const active = root.querySelector<HTMLElement>(`.boot__tab[data-for="${id}"]`);
  for (const tab of root.querySelectorAll<HTMLElement>('.boot__tab')) {
    tab.toggleAttribute('data-active', tab === active);
  }
}

/** Render the final frame and stop. Idempotent. */
export function finish(root?: HTMLElement | null): void {
  const el = root ?? state?.root ?? document.querySelector<HTMLElement>(ROOT_SEL);
  if (!el) return;
  if (state) clearTimers(state);
  delete el.dataset.boot;
  cut(el, 'end');
  el.dataset.boot = 'done';
  const replay = el.querySelector<HTMLButtonElement>('[data-boot-replay]');
  if (replay) replay.hidden = false;
}

/** Rewind and play from zero. Idempotent. */
export function play(root?: HTMLElement | null): void {
  const el = root ?? state?.root ?? document.querySelector<HTMLElement>(ROOT_SEL);
  if (!el) return;

  if (state) clearTimers(state);

  // Drop the gate, then re-add it on the next frame so every animation
  // restarts. The forced reflow is the one place the film reads layout.
  delete el.dataset.boot;
  cut(el, 'boot');
  void el.offsetWidth;

  if (prefersReduced()) {
    finish(el);
    return;
  }

  el.dataset.boot = 'play';
  const replay = el.querySelector<HTMLButtonElement>('[data-boot-replay]');
  if (replay) replay.hidden = true;

  if (!state) return;
  for (const shot of SHOTS) {
    if (shot.at <= 0) continue;
    state.timers.push(window.setTimeout(() => cut(el, shot.id), shot.at * 1000));
  }
  state.timers.push(window.setTimeout(() => finish(el), DURATION * 1000));
}

/** Jump straight to a shot, revealed, with no animation. */
export function seek(shotId: string, root?: HTMLElement | null): void {
  const el = root ?? state?.root ?? document.querySelector<HTMLElement>(ROOT_SEL);
  if (!el) return;
  if (state) clearTimers(state);
  delete el.dataset.boot;
  cut(el, shotId);
  el.dataset.boot = 'done';
}

export function isPlaying(root?: HTMLElement | null): boolean {
  const el = root ?? state?.root ?? document.querySelector<HTMLElement>(ROOT_SEL);
  return !!el && el.dataset.boot === 'play';
}

/** Tear down timers and listeners. Leaves the root on the final frame. */
export function destroy(): void {
  if (!state) return;
  const { root, mq, onMotion, unkeys } = state;
  clearTimers(state);
  if (mq && onMotion) {
    if (typeof mq.removeEventListener === 'function') mq.removeEventListener('change', onMotion);
    else (mq as unknown as { removeListener: (f: typeof onMotion) => void }).removeListener(onMotion);
  }
  unkeys?.();
  delete root.dataset.boot;
  cut(root, 'end');
  state = null;
}

/** Esc skips, R replays, Space replays once stopped. Never traps Tab. */
function bindKeys(el: HTMLElement, handle: BootHandle): () => void {
  const onKey = (e: KeyboardEvent): void => {
    const t = e.target as HTMLElement | null;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if (e.key === 'Escape') {
      handle.skip();
      return;
    }
    const key = e.key.toLowerCase();
    if (key === 'r' || ((key === ' ' || key === 'enter') && !isPlaying(el))) {
      if (key === ' ') e.preventDefault();
      handle.replay();
    }
  };
  window.addEventListener('keydown', onKey);
  return () => window.removeEventListener('keydown', onKey);
}

/** Called by Boot.astro after mount. Safe to call again. */
export function init(root?: HTMLElement | null): BootHandle | null {
  const el = root ?? document.querySelector<HTMLElement>(ROOT_SEL);
  if (!el || !el.querySelector('.boot__film')) return null;

  destroy();

  const handle: BootHandle = {
    replay: () => play(el),
    skip: () => finish(el),
    seek: (id: string) => seek(id, el),
    destroy: () => destroy(),
    duration: DURATION,
    shots: SHOTS,
  };

  const mq: MediaQueryList | null =
    typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null;
  const onMotion = (e: MediaQueryListEvent): void => {
    if (e.matches) finish(el);
    else play(el);
  };
  if (mq) {
    if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onMotion);
    else (mq as unknown as { addListener: (f: typeof onMotion) => void }).addListener(onMotion);
  }

  const unkeys = bindKeys(el, handle);
  state = { root: el, timers: [], unkeys, mq, onMotion };

  for (const btn of el.querySelectorAll<HTMLButtonElement>('[data-boot-replay]')) {
    btn.addEventListener('click', () => play(el));
  }
  for (const btn of el.querySelectorAll<HTMLButtonElement>('[data-boot-skip]')) {
    btn.addEventListener('click', () => finish(el));
  }

  play(el);
  return handle;
}

export default { init, play, finish, seek, destroy, isPlaying, DURATION, SHOTS, DIMENSIONS, bar };
