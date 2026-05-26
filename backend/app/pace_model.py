"""
Pace model for the DRIP hydration monitoring system.

Computes the expected cumulative intake at a given point in the active
day. Two modes are supported:

- **Linear** (default): intake is assumed uniform throughout the waking
  day. Simple, transparent, easy to explain to clinical staff.

- **Meal-weighted** (optional, set ``PaceModelConfig.weighted = True``):
  expected intake is weighted toward typical meal windows — breakfast
  (08:00), lunch (12:00), and dinner (17:00) — and lower overnight.
  This produces a more clinically realistic pace curve.

The model operates on ``active_elapsed_s`` — elapsed time during which
the session is in the ACTIVE state — so nap pauses are automatically
excluded without any wall-clock adjustment here.

The expected value is always floored at 0 and capped at ``daily_goal_ml``
so it can be displayed directly without additional clamping.
"""

from app.config import PaceModelConfig, SystemConfig
from app.patient_profile import BedProfile


# Fraction of daily goal expected in each waking hour (07:00–22:00 inclusive).
# Peaks around breakfast (08:00), lunch (12:00), and dinner (17:00).
# Values sum to 1.0 over the 16-hour active day.
_HOURLY_FRACTIONS: dict[int, float] = {
    7:  0.04,
    8:  0.10,
    9:  0.05,
    10: 0.05,
    11: 0.05,
    12: 0.12,
    13: 0.06,
    14: 0.05,
    15: 0.05,
    16: 0.05,
    17: 0.12,
    18: 0.06,
    19: 0.05,
    20: 0.05,
    21: 0.04,
    22: 0.06,
}


class PaceModel:
    """
    Computes expected intake for the current active session duration.

    Instantiate once per session and call :meth:`expected_by_now` on
    each pace display refresh. The model is stateless between calls.

    Example::

        pace = PaceModel(config, bed)
        expected = pace.expected_by_now(session.summary().duration_s)
        deficit  = pace.deficit(summary.total_consumed_ml, summary.duration_s)
    """

    def __init__(self, config: SystemConfig, bed: BedProfile):
        self._cfg: PaceModelConfig = config.pace_model
        self._daily_goal_ml: float = bed.daily_goal_ml

    def expected_by_now(self, active_elapsed_s: float) -> float:
        """
        Return the expected cumulative intake in ml given elapsed active time.

        Args:
            active_elapsed_s: Seconds the session has been in the ACTIVE
                state (nap time excluded).

        Returns:
            Expected intake in ml, rounded to the nearest ml.
            Capped at ``daily_goal_ml``.
        """
        if self._cfg.weighted:
            return self._weighted_expected(active_elapsed_s)
        return self._linear_expected(active_elapsed_s)

    def deficit(self, actual_ml: float, active_elapsed_s: float) -> float:
        """
        Return how far behind the patient is, floored at 0.

        Args:
            actual_ml: Actual intake recorded so far in ml.
            active_elapsed_s: Elapsed active seconds.

        Returns:
            ml behind pace, or 0.0 if on pace or ahead.
        """
        return max(0.0, self.expected_by_now(active_elapsed_s) - actual_ml)

    def _linear_expected(self, active_elapsed_s: float) -> float:
        active_day_s = self._cfg.active_day_hours * 3600.0
        fraction = min(active_elapsed_s / max(active_day_s, 1.0), 1.0)
        return round(fraction * self._daily_goal_ml)

    def _weighted_expected(self, active_elapsed_s: float) -> float:
        # Map elapsed active seconds onto the waking day starting one hour
        # after the daily reset (e.g. reset at 06:00 → day starts at 07:00).
        day_start_hour = self._cfg.daily_reset_hour + 1
        elapsed_h = active_elapsed_s / 3600.0
        current_hour = day_start_hour + elapsed_h

        accumulated = 0.0
        for hour in sorted(_HOURLY_FRACTIONS):
            if hour < day_start_hour:
                continue
            if hour >= current_hour:
                break
            # Full hour if we've passed it, fractional if we're mid-hour.
            hour_progress = min(current_hour - hour, 1.0)
            accumulated += _HOURLY_FRACTIONS[hour] * hour_progress

        return round(min(accumulated * self._daily_goal_ml, self._daily_goal_ml))
