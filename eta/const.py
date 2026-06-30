"""Constants for the ETA Heating integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "eta"

DEFAULT_NAME = "ETA Heating"
DEFAULT_PORT = 8080
DEFAULT_PREFIX = "eta"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)

CONF_CACHE_DISCOVERY = "cache_discovery"
CONF_DISCOVERY_WORKERS = "discovery_workers"
CONF_FULL_SWITCH_DISCOVERY = "full_switch_discovery"
CONF_PREFIX = "prefix"
CONF_USE_VARIABLE_SET = "use_variable_set"

DEFAULT_CACHE_DISCOVERY = True
DEFAULT_DISCOVERY_WORKERS = 32
DEFAULT_FULL_SWITCH_DISCOVERY = False
DEFAULT_USE_VARIABLE_SET = True

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_discovery"

