"""
Bed Profile for the DRIP Hydration Monitoring System.

Stores per-bed configuration that overrides system-level defaults.
No personal or identifying information about the occupant is held here.
Clinical staff know which bed belongs to which patient — the system
does not need to.

Using a bed identifier rather than a patient identifier means that when
a patient is discharged and a new patient occupies the bed, only the
daily goal needs updating. No personal data is ever written to disk or
transmitted.
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
        ward_id: Optional ward identifier for grouping beds in
            multi-ward deployments.
        daily_goal_ml: Target fluid intake for the current occupant in
            ml. Updated by staff when a new patient is admitted or when
            a clinician changes the target. Overrides
            :attr:`config.SessionConfig.default_daily_goal_ml`.
    """

    bed_id: str
    ward_id: str | None = None
    daily_goal_ml: float = 2000.0
