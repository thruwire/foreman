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

    def reset(self) -> None:
        """Discard all recorded state, e.g. when a new run begins."""
        self._previous.clear()
