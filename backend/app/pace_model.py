"""
Pace model for the DRIP hydration monitoring system.

Computes the expected cumulative intake at a given point in the active
day. Two modes are supported:

- **Milestone** (default): a piecewise-linear curve defined by a list of
  (elapsed_hours, goal_fraction) waypoints.  The default curve encodes
  the Addenbrooke's / nurse recommendation that patients reach roughly
  half their daily goal by midday, then continue at a gentler rate in
  the afternoon.

  Default waypoints::

      0 h  →   0 % of daily goal   (session start)
      6 h  →  53 % of daily goal   (~800 ml at 1500 ml min; midday anchor)
     16 h  → 100 % of daily goal   (end of active day)

  Morning rate ≈ 1.4 × the afternoon rate.

- **Linear**: uniform rate across the full active day. Simple, easy to
  explain, useful as a clinical baseline comparison.

Grace period
------------
For the first ``grace_period_s`` of active time (default 30 min) the
expected value is 0 regardless of mode. This prevents the display
opening with an immediate deficit the moment the daily reset fires.

The model operates on ``active_elapsed_s`` — seconds the session has
been in the ACTIVE state — so nap pauses are automatically excluded.
"""

from app.config import PaceModelConfig, SystemConfig
from app.patient_profile import BedProfile


class PaceModel:
    """
    Computes expected intake for the current active session duration.

    Instantiate once per session and call :meth:`expected_by_now` on
    each pace display refresh.  The model is stateless between calls.

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

        Returns 0 during the grace period.  After the grace period the
        value follows the configured curve, capped at ``daily_goal_ml``.

        Args:
            active_elapsed_s: Seconds the session has been ACTIVE
                (nap time excluded).

        Returns:
            Expected intake in ml (non-negative integer, capped at goal).
        """
        # Grace period — no deficit shown while the unit is warming up.
        if active_elapsed_s < self._cfg.grace_period_s:
            return 0.0

        if self._cfg.mode == "linear":
            return self._linear_expected(active_elapsed_s)
        return self._milestone_expected(active_elapsed_s)

    def deficit(self, actual_ml: float, active_elapsed_s: float) -> float:
        """
        Return how far behind the patient is, floored at 0.

        Args:
            actual_ml:          Actual intake recorded so far in ml.
            active_elapsed_s:   Elapsed active seconds.

        Returns:
            ml behind pace, or 0.0 if on pace or ahead.
        """
        return max(0.0, self.expected_by_now(active_elapsed_s) - actual_ml)

    # ------------------------------------------------------------------
    # Private curve implementations
    # ------------------------------------------------------------------

    def _linear_expected(self, active_elapsed_s: float) -> float:
        """Uniform rate across the full active day."""
        active_day_s = self._cfg.active_day_hours * 3600.0
        fraction = min(active_elapsed_s / max(active_day_s, 1.0), 1.0)
        return round(fraction * self._daily_goal_ml)

    def _milestone_expected(self, active_elapsed_s: float) -> float:
        """
        Piecewise-linear interpolation between the configured waypoints.

        Waypoints are defined in absolute elapsed *hours* from session
        start (not offset by the grace period).  The grace-period check
        has already fired before this method is called.
        """
        elapsed_h = active_elapsed_s / 3600.0
        hours = self._cfg.milestone_hours
        fracs = self._cfg.milestone_fractions

        # Beyond the last waypoint → return 100 % of goal.
        if elapsed_h >= hours[-1]:
            return round(min(fracs[-1], 1.0) * self._daily_goal_ml)

        # Before the first waypoint → return 0.
        if elapsed_h <= hours[0]:
            return 0.0

        # Find the enclosing segment and linearly interpolate.
        for i in range(len(hours) - 1):
            lo_h, hi_h = hours[i], hours[i + 1]
            lo_f, hi_f = fracs[i], fracs[i + 1]
            if lo_h <= elapsed_h <= hi_h:
                span = hi_h - lo_h
                t = (elapsed_h - lo_h) / span if span > 0 else 1.0
                goal_frac = lo_f + t * (hi_f - lo_f)
                return round(min(goal_frac, 1.0) * self._daily_goal_ml)

        return 0.0
