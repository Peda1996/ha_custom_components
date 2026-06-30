"""Runtime setup helpers for ETA Heating."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import EtaApiClient, EtaApiError, EtaDiscovery, async_discover
from .api import EtaEndpoint
from .const import (
    CONF_CACHE_DISCOVERY,
    CONF_DISCOVERY_WORKERS,
    CONF_FULL_SWITCH_DISCOVERY,
    CONF_PREFIX,
    CONF_USE_VARIABLE_SET,
    DEFAULT_CACHE_DISCOVERY,
    DEFAULT_DISCOVERY_WORKERS,
    DEFAULT_FULL_SWITCH_DISCOVERY,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PREFIX,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_VARIABLE_SET,
    DOMAIN,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .coordinator import EtaCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EtaRuntime:
    """Runtime state shared by ETA platforms."""

    hass: HomeAssistant
    client: EtaApiClient
    data: dict[str, Any]
    storage_key: str
    device_id: str | None = None
    discovery: EtaDiscovery | None = None
    coordinator: EtaCoordinator | None = None

    @property
    def host(self) -> str:
        """Configured ETA host."""
        return self.data[CONF_HOST]

    @property
    def port(self) -> int:
        """Configured ETA port."""
        return int(self.data.get(CONF_PORT, DEFAULT_PORT))

    @property
    def name(self) -> str:
        """Configured device name."""
        return self.data.get(CONF_NAME) or DEFAULT_NAME

    @property
    def prefix(self) -> str:
        """Configured unique id prefix."""
        return self.data.get(CONF_PREFIX) or DEFAULT_PREFIX

    @property
    def update_interval(self) -> timedelta:
        """Configured update interval."""
        raw = self.data.get(CONF_SCAN_INTERVAL)
        if isinstance(raw, timedelta):
            return raw
        if raw is None:
            return DEFAULT_SCAN_INTERVAL
        return timedelta(seconds=max(10, int(raw)))

    @property
    def device_info(self) -> dict[str, Any]:
        """Home Assistant device info."""
        return {
            "identifiers": {(DOMAIN, self.device_id or f"{self.host}:{self.port}")},
            "manufacturer": "ETA Heiztechnik",
            "name": self.name,
            "configuration_url": self.client.base_url,
        }

    def device_info_for_endpoint(self, endpoint: EtaEndpoint) -> dict[str, Any]:
        """Group ETA entities by top-level ETA menu area in Home Assistant."""
        group = endpoint.name.split(" ", 1)[0] if endpoint.name else self.name
        return {
            "identifiers": {
                (
                    DOMAIN,
                    self.device_id or f"{self.host}:{self.port}",
                    group,
                )
            },
            "manufacturer": "ETA Heiztechnik",
            "name": f"{self.name} {group}",
            "configuration_url": self.client.base_url,
        }

    async def async_setup(self) -> None:
        """Load or create discovery metadata and initialize the coordinator."""
        self.device_id = await self.client.async_get_device_id()
        previous_discovery = await self._async_load_discovery(
            allow_version_mismatch=True
        )
        self.discovery = await self._async_load_discovery()

        if self.discovery is None:
            _LOGGER.info("Discovering ETA endpoints from %s", self.client.base_url)
            self.discovery = await async_discover(
                self.client,
                self.device_id,
                workers=int(self.data.get(CONF_DISCOVERY_WORKERS, DEFAULT_DISCOVERY_WORKERS)),
                full_switch_discovery=bool(
                    self.data.get(
                        CONF_FULL_SWITCH_DISCOVERY,
                        DEFAULT_FULL_SWITCH_DISCOVERY,
                    )
                ),
            )
            self.discovery.add_legacy_keys_from(previous_discovery)
            await self._async_save_discovery()
        else:
            _LOGGER.info(
                "Loaded %s ETA endpoints for %s from discovery cache",
                len(self.discovery.endpoints),
                self.device_id,
            )

        self.coordinator = EtaCoordinator(
            self.hass,
            self.client,
            name=_safe_storage_part(self.device_id),
            endpoints=self.discovery.endpoints,
            update_interval=self.update_interval,
            use_variable_set=bool(
                self.data.get(CONF_USE_VARIABLE_SET, DEFAULT_USE_VARIABLE_SET)
            ),
            variable_set_name=f"haeta{_safe_storage_part(self.device_id)}"[:32],
            workers=int(self.data.get(CONF_DISCOVERY_WORKERS, DEFAULT_DISCOVERY_WORKERS)),
        )
        await self.coordinator.async_config_entry_first_refresh()

    async def _async_load_discovery(
        self,
        *,
        allow_version_mismatch: bool = False,
    ) -> EtaDiscovery | None:
        if not self.data.get(CONF_CACHE_DISCOVERY, DEFAULT_CACHE_DISCOVERY):
            return None

        store: Store[dict[str, Any]] = Store(
            self.hass,
            STORAGE_VERSION,
            self.storage_key,
        )
        cache = await store.async_load()
        return EtaDiscovery.from_cache(
            cache,
            self.host,
            self.port,
            self.device_id or "",
            allow_version_mismatch=allow_version_mismatch,
        )

    async def _async_save_discovery(self) -> None:
        if (
            not self.data.get(CONF_CACHE_DISCOVERY, DEFAULT_CACHE_DISCOVERY)
            or self.discovery is None
            or self.device_id is None
        ):
            return

        store: Store[dict[str, Any]] = Store(
            self.hass,
            STORAGE_VERSION,
            self.storage_key,
        )
        await store.async_save(self.discovery.to_cache(self.host, self.port))


async def async_create_runtime(
    hass: HomeAssistant,
    data: dict[str, Any],
    *,
    storage_suffix: str,
) -> EtaRuntime:
    """Create and set up ETA runtime from config data."""
    merged = normalize_config(data)
    client = EtaApiClient(
        merged[CONF_HOST],
        int(merged.get(CONF_PORT, DEFAULT_PORT)),
        async_get_clientsession(hass),
    )
    runtime = EtaRuntime(
        hass=hass,
        client=client,
        data=merged,
        storage_key=f"{STORAGE_KEY_PREFIX}_{_safe_storage_part(storage_suffix)}",
    )
    try:
        await runtime.async_setup()
    except EtaApiError:
        raise
    except Exception as err:  # noqa: BLE001 - translated to ConfigEntryNotReady by caller.
        raise EtaApiError(str(err)) from err
    return runtime


async def async_get_legacy_runtime(
    hass: HomeAssistant,
    data: dict[str, Any],
) -> EtaRuntime:
    """Return one shared runtime for legacy YAML platform setup."""
    merged = normalize_config(data)
    key = _safe_storage_part(
        f"{merged[CONF_HOST]}_{merged.get(CONF_PORT, DEFAULT_PORT)}_{merged.get(CONF_PREFIX, DEFAULT_PREFIX)}"
    )
    legacy = hass.data.setdefault(DOMAIN, {}).setdefault("legacy", {})
    existing = legacy.get(key)
    if isinstance(existing, EtaRuntime):
        return existing
    if existing is None:
        existing = hass.async_create_task(
            async_create_runtime(hass, merged, storage_suffix=f"legacy_{key}")
        )
        legacy[key] = existing

    runtime = await existing
    legacy[key] = runtime
    return runtime


def normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults and normalize types."""
    normalized = dict(data)
    normalized[CONF_PORT] = int(normalized.get(CONF_PORT, DEFAULT_PORT))
    normalized.setdefault(CONF_NAME, DEFAULT_NAME)
    normalized.setdefault(CONF_PREFIX, DEFAULT_PREFIX)
    normalized.setdefault(CONF_CACHE_DISCOVERY, DEFAULT_CACHE_DISCOVERY)
    normalized.setdefault(CONF_DISCOVERY_WORKERS, DEFAULT_DISCOVERY_WORKERS)
    normalized.setdefault(CONF_FULL_SWITCH_DISCOVERY, DEFAULT_FULL_SWITCH_DISCOVERY)
    normalized.setdefault(CONF_USE_VARIABLE_SET, DEFAULT_USE_VARIABLE_SET)
    return normalized


def _safe_storage_part(value: str | None) -> str:
    """Return a compact alphanumeric key component."""
    safe = re.sub(r"[^a-zA-Z0-9]", "", value or "")
    return safe or "eta"
