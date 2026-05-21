"""
Bed Profile for the Hydration Monitoring System.

Stores per-bed configuration that overrides system-level defaults.
No personal or identifying information about the occupant is held here.
Clinical staff know which bed belongs to which patient — the system
does not need to.

Using a bed identifier rather than a patient identifier means that when
a patient is discharged and a new patient occupies the bed, only the
goal and tare values need updating. No personal data is ever written to
disk or transmitted.
"""

from dataclasses import dataclass


@dataclass
class BedProfile:
    """
    Configuration for a single monitored bed position.

    Attributes:
        bed_id: Unique identifier for this bed, e.g. ``"ward-4-bed-7"``.
            Used as the key in the persistence layer. Contains no
            personal information.
        ward_id: Optional ward identifier. Used to group beds in
            multi-ward deployments. Contains no personal information.
        daily_goal_ml: Target fluid intake for the current occupant in
            ml. Updated by staff when a new patient is admitted or when
            a clinician changes the target. Overrides
            :attr:`config.SessionConfig.default_daily_goal_ml`.
        cup_tare_weight_g: Empty weight of the cup currently on this
            bed's platform in grams. Set during calibration so the
            system knows how much of the platform weight is cup vs
            fluid. Reset to 0.0 when a different cup is placed.
            0.0 means no tare has been recorded and the raw platform
            weight is used as-is.
        fluid_density_g_per_ml: Density of the fluid on this platform.
            Defaults to 1.0 (water, tea, juice). Adjust for thicker
            drinks if clinically relevant.
    """

    bed_id: str
    ward_id: str | None = None
    daily_goal_ml: float = 2000.0
    cup_tare_weight_g: float = 0.0
    fluid_density_g_per_ml: float = 1.0

    def has_tare(self) -> bool:
        """
        Return whether a cup tare weight has been recorded.

        Returns:
            True if ``cup_tare_weight_g`` is greater than zero.
        """
        return self.cup_tare_weight_g > 0.0

    def effective_fluid_weight_g(self, platform_weight_g: float) -> float:
        """
        Subtract the cup tare from a platform reading to get fluid weight.

        If no tare has been recorded the raw platform weight is returned
        unchanged, which will slightly overestimate fluid volume.

        Args:
            platform_weight_g: Raw stable weight reading from the
                platform in grams.

        Returns:
            Estimated fluid weight in grams.
        """
        if not self.has_tare():
            return platform_weight_g
        return max(0.0, platform_weight_g - self.cup_tare_weight_g)