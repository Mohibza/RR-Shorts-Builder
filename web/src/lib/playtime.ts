// The preview's playhead, shared without re-rendering the whole editor 60 times a second.
// Components subscribe to a *derived* value (e.g. the index of the spoken word) and only re-render
// when that value changes.
import { useSyncExternalStore } from "react";

let T = 0;
const subs = new Set<() => void>();

export function publishTime(t: number) {
  T = t;
  subs.forEach((f) => f());
}

export function playTime() { return T; }

function subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f); }; }

/** Re-renders only when `pick(T)` returns a different value. */
export function usePlayTime<V>(pick: (t: number) => V): V {
  return useSyncExternalStore(subscribe, () => pick(T), () => pick(T));
}
