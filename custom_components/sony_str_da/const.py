"""Constants for the Sony STR-DA ES integration."""

DOMAIN = "sony_str_da"

CONF_MIN_DB = "min_db"
CONF_MAX_DB = "max_db"
CONF_SOURCES = "sources"

# The volume slider covers this range; the max also caps what the integration
# will ever set, so a slider at 100% cannot blast the room.
DEFAULT_MIN_DB = -80.0
DEFAULT_MAX_DB = -20.0
