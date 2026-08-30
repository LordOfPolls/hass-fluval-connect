DOMAIN = "fluval"

CONF_KEEP_CONNECTED = "keep_connected"
CONF_POLL_INTERVAL = "poll_interval"

# The light accepts one central at a time and stops advertising while connected
DEFAULT_KEEP_CONNECTED = True

# Polling on a shared link means a fresh connect every time, so the default backs off.
POLL_INTERVAL = 60
POLL_INTERVAL_ON_DEMAND = 300
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600


def default_poll_interval(keep_connected: bool) -> int:
    return POLL_INTERVAL if keep_connected else POLL_INTERVAL_ON_DEMAND
