"""Central inclusive date gate for initial outreach and future follow-up dispatch."""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def campaign_date_eligible(config, now: datetime) -> bool:
    # Require an actual aware system instant; never assume the host's time zone.
    if now.tzinfo is None or now.utcoffset() is None:
        return False
    try:
        local_date = now.astimezone(ZoneInfo(config.time_zone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return False
    if config.start_date is None and config.end_date is None:
        return True  # Legacy undated plans retain no date restriction.
    if config.start_date is None or config.end_date is None:
        return False
    return config.start_date <= local_date <= config.end_date
