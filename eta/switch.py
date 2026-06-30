"""ETA Heating switches."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.switch import PLATFORM_SCHEMA, SwitchEntity
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify
import homeassistant.helpers.config_validation as cv

from .api import EtaEndpoint, is_switch_on, split_switch_values
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
    DEFAULT_USE_VARIABLE_SET,
    DOMAIN,
)
from .coordinator import EtaCoordinator
from .runtime import EtaRuntime, async_get_legacy_runtime

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PREFIX, default=DEFAULT_PREFIX): cv.string,
        vol.Optional(CONF_CACHE_DISCOVERY, default=DEFAULT_CACHE_DISCOVERY): cv.boolean,
        vol.Optional(
            CONF_DISCOVERY_WORKERS,
            default=DEFAULT_DISCOVERY_WORKERS,
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional(
            CONF_FULL_SWITCH_DISCOVERY,
            default=DEFAULT_FULL_SWITCH_DISCOVERY,
        ): cv.boolean,
        vol.Optional(
            CONF_USE_VARIABLE_SET,
            default=DEFAULT_USE_VARIABLE_SET,
        ): cv.boolean,
        vol.Optional(CONF_SCAN_INTERVAL): cv.time_period,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ETA switches from a config entry."""
    runtime: EtaRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_build_switches(runtime))


async def async_setup_platform(
    hass: HomeAssistant,
    config,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,
) -> None:
    """Set up ETA switches from legacy YAML platform config."""
    runtime = await async_get_legacy_runtime(hass, config)
    async_add_entities(_build_switches(runtime))


def _build_switches(runtime: EtaRuntime) -> list[EtaSwitch]:
    """Create switch entities."""
    assert runtime.discovery is not None
    assert runtime.coordinator is not None
    return [
        EtaSwitch(runtime.coordinator, endpoint, runtime)
        for endpoint in runtime.discovery.endpoints
        if len(endpoint.valid_values) == 2
    ]


class EtaSwitch(CoordinatorEntity[EtaCoordinator], SwitchEntity):
    """ETA writable two-state text variable."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: EtaCoordinator,
        endpoint: EtaEndpoint,
        runtime: EtaRuntime,
    ) -> None:
        super().__init__(coordinator)
        self.endpoint = endpoint
        self._attr_name = f"{endpoint.name} Schalter"
        self._attr_unique_id = (
            f"{runtime.prefix}_{runtime.device_id}.{endpoint.unique_key}_switch"
        )
        self.entity_id = f"switch.{slugify(f'{runtime.prefix}_{endpoint.name} Schalter')}"
        self._attr_device_info = runtime.device_info_for_endpoint(endpoint)
        self._attr_entity_registry_enabled_default = endpoint.enabled_default

    @property
    def is_on(self) -> bool | None:
        """Return true if the ETA variable is in its on state."""
        return is_switch_on(
            self.coordinator.data.get(self.endpoint.uri),
            self.endpoint.valid_values,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the ETA variable on."""
        _, on_value = split_switch_values(self.endpoint.valid_values)
        await self.coordinator.client.async_write_value(self.endpoint.uri, on_value)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the ETA variable off."""
        off_value, _ = split_switch_values(self.endpoint.valid_values)
        await self.coordinator.client.async_write_value(self.endpoint.uri, off_value)
        await self.coordinator.async_request_refresh()
