from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExponentialSmoother:
    """Exponential moving average over one responsibility's noisy assessment scores.

    Semantic assessment scores are produced independently per evaluation and can
    oscillate while the underlying work is stable. Smoothing the values a
    responsibility thresholds on tracks genuine movement instead of noise.

    The smoother is owned by a single responsibility instance and keeps separate
    state per check key, so enabling it never mutates the shared ``ForemanResult``
    and never couples one responsibility's decisions to another's. ``alpha=1.0``
    (the default) disables smoothing: values pass through untouched and no state
    is recorded, preserving upstream behavior.

    Two smoothing modes are offered because not every signal may be damped.
    :meth:`smooth` is the symmetric EMA for completion-style scores, where the
    goal is to track genuine movement instead of assessment noise.
    :meth:`smooth_safety` is fast-attack, slow-release for safety signals
    (``needs_human``, ``work_off_track``): a newly high reading is never
    damped, so escalation fires immediately.
    """

    alpha: float = 1.0
    _previous: dict[str, float] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError("smoothing alpha must be in (0, 1]")

    def smooth(self, key: str, value: float) -> float:
        """Fold ``value`` for ``key`` into the moving average and return it."""
        if self.alpha >= 1.0:
            return value
        previous = self._previous.get(key)
        if previous is None:
            smoothed = value
        else:
            smoothed = self.alpha * value + (1.0 - self.alpha) * previous
        self._previous[key] = smoothed
        return smoothed

    def smooth_safety(self, key: str, value: float) -> float:
        """Fold ``value`` for ``key`` into a fast-attack, slow-release average.

        Safety signals must never be damped on a rising edge: when the new
        reading is at or above the recorded average, the raw value wins so a
        newly high ``needs_human`` or ``work_off_track`` escalates on the very
        assessment that reports it. Falling edges still ease down through the
        EMA so a single low reading does not flap the signal off. Damping a
        newly high safety signal would be a safety regression; damping
        completion noise is the intended use of :meth:`smooth`.
        """
        if self.alpha >= 1.0:
            return value
        previous = self._previous.get(key)
        if previous is None or value >= previous:
            smoothed = value
        else:
            smoothed = self.alpha * value + (1.0 - self.alpha) * previous
        self._previous[key] = smoothed
        return smoothed

    def reset(self) -> None:
        """Discard all recorded state, e.g. when a new run begins."""
        self._previous.clear()
