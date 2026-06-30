"""ETA Heating sensors."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfMass,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify
import homeassistant.helpers.config_validation as cv

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
    """Set up ETA sensors from a config entry."""
    runtime: EtaRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_build_sensors(runtime))


async def async_setup_platform(
    hass: HomeAssistant,
    config,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,
) -> None:
    """Set up ETA sensors from legacy YAML platform config."""
    runtime = await async_get_legacy_runtime(hass, config)
    async_add_entities(_build_sensors(runtime))


def _build_sensors(runtime: EtaRuntime) -> list[EtaSensor]:
    """Create sensor entities."""
    assert runtime.discovery is not None
    assert runtime.coordinator is not None
    return [
        EtaSensor(runtime.coordinator, endpoint, runtime)
        for endpoint in runtime.discovery.endpoints
    ]


class EtaSensor(CoordinatorEntity[EtaCoordinator], SensorEntity):
    """ETA sensor entity."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: EtaCoordinator,
        endpoint: EtaEndpoint,
        runtime: EtaRuntime,
    ) -> None:
        super().__init__(coordinator)
        self.endpoint = endpoint
        self._attr_name = endpoint.name
        self._attr_unique_id = (
            f"{runtime.prefix}_{runtime.device_id}.{endpoint.unique_key}"
        )
        self.entity_id = f"sensor.{slugify(f'{runtime.prefix}_{endpoint.name}')}"
        self._attr_device_info = runtime.device_info_for_endpoint(endpoint)
        self._attr_entity_registry_enabled_default = endpoint.enabled_default

        unit, device_class = unit_mapper(endpoint.unit)
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if device_class is not None:
            self._attr_device_class = device_class

    @property
    def native_value(self) -> int | float | str | None:
        """Return the latest ETA value."""
        value = self.coordinator.data.get(self.endpoint.uri)
        if value is None:
            return None
        return value.native_value


def unit_mapper(unit: str) -> tuple[str | None, SensorDeviceClass | None]:
    """Map ETA units to Home Assistant units and device classes."""
    return {
        "°C": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        "Hz": (UnitOfFrequency.HERTZ, SensorDeviceClass.FREQUENCY),
        "kW": (UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
        "W": (UnitOfPower.WATT, SensorDeviceClass.POWER),
        "kWh": (UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
        "kg": (UnitOfMass.KILOGRAMS, getattr(SensorDeviceClass, "WEIGHT", None)),
        "bar": (UnitOfPressure.BAR, SensorDeviceClass.PRESSURE),
        "Pa": (UnitOfPressure.PA, SensorDeviceClass.PRESSURE),
        "A": (UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
        "mA": (UnitOfElectricCurrent.MILLIAMPERE, SensorDeviceClass.CURRENT),
        "s": (UnitOfTime.SECONDS, getattr(SensorDeviceClass, "DURATION", None)),
        "V": (UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        "mV": (UnitOfElectricPotential.MILLIVOLT, SensorDeviceClass.VOLTAGE),
        "%": (PERCENTAGE, None),
        "l": (UnitOfVolume.LITERS, getattr(SensorDeviceClass, "VOLUME", None)),
        "W/m²": (
            "W/m²",
            getattr(SensorDeviceClass, "IRRADIANCE", None),
        ),
    }.get(unit, (unit or None, None))
