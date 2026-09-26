"""Jump-cut pacing: remove dead air between sentences so Shorts feel fast (like pro edits)."""
from __future__ import annotations


def keep_ranges(words: list[dict], dur: float, max_pause: float = 0.45, lead: float = 0.12,
                tail: float = 0.15, start_pad: float = 0.08, end_pad: float = 0.35) -> list[tuple[float, float]]:
    """Time ranges (relative to the clip) to keep. Gaps longer than `max_pause` shrink to lead+tail."""
    if not words:
        return [(0.0, dur)]
    ranges = []
    a = max(0.0, words[0]["s"] - start_pad) if words[0]["s"] > 0.4 else 0.0
    prev_end = words[0]["e"]
    for w in words[1:]:
        gap = w["s"] - prev_end
        if gap > max_pause:
            ranges.append((a, prev_end + tail))
            a = w["s"] - lead
        prev_end = max(prev_end, w["e"])
    ranges.append((a, min(dur, prev_end + end_pad)))
    # merge overlaps / tiny pieces
    out: list[tuple[float, float]] = []
    for s, e in ranges:
        if e - s < 0.05:
            continue
        if out and s <= out[-1][1] + 0.02:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out or [(0.0, dur)]


class TimeMap:
    """Maps original clip time -> tightened time."""

    def __init__(self, ranges: list[tuple[float, float]]):
        self.ranges = ranges
        self.offsets = []
        acc = 0.0
        for s, e in ranges:
            self.offsets.append(acc - s)
            acc += e - s
        self.duration = acc

    def __call__(self, t: float) -> float:
        for (s, e), off in zip(self.ranges, self.offsets):
            if t < s:
                return s + off          # inside a removed gap -> snap to the next kept moment
            if t <= e:
                return t + off
        return self.duration

    def removed(self) -> float:
        return (self.ranges[-1][1] if self.ranges else 0) - self.duration


def tighten(words: list[dict], dur: float, camera: list, enabled: bool = True):
    """Returns (ranges|None, retimed words, retimed camera path, new duration)."""
    if not enabled or len(words) < 3:
        return None, words, camera, dur
    ranges = keep_ranges(words, dur)
    tm = TimeMap(ranges)
    if dur - tm.duration < 0.4:  # nothing worth cutting
        return None, words, camera, dur
    new_words = [{**w, "s": round(tm(w["s"]), 3), "e": round(max(tm(w["e"]), tm(w["s"]) + 0.05), 3)} for w in words]
    new_cam = []
    for t, x in camera:
        nt = tm(t)
        if not new_cam or nt > new_cam[-1][0] + 1e-3:
            new_cam.append((nt, x))
        else:
            new_cam[-1] = (new_cam[-1][0], x)
    return ranges, new_words, new_cam, tm.duration


def select_expr(ranges: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in ranges)
