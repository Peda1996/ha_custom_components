"""ETA Heating integration for Home Assistant."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import slugify

from .api import EtaApiError
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
)
from .runtime import async_create_runtime, normalize_config

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
                vol.Optional(CONF_PREFIX, default=DEFAULT_PREFIX): cv.string,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=DEFAULT_SCAN_INTERVAL,
                ): cv.time_period,
                vol.Optional(
                    CONF_CACHE_DISCOVERY,
                    default=DEFAULT_CACHE_DISCOVERY,
                ): cv.boolean,
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
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up ETA from YAML by importing it as a config entry."""
    hass.data.setdefault(DOMAIN, {})
    if DOMAIN not in config:
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data=normalize_config(config[DOMAIN]),
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ETA from a config entry."""
    data = normalize_config({**entry.data, **entry.options})
    try:
        runtime = await async_create_runtime(
            hass,
            data,
            storage_suffix=entry.entry_id,
        )
    except EtaApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    _migrate_entity_registry(hass, entry, runtime)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an ETA config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


def _migrate_entity_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime,
) -> None:
    """Adopt old eta_heating entries, then rename early eta_heating entity ids."""
    if runtime.discovery is None:
        return

    registry = er.async_get(hass)
    for endpoint in runtime.discovery.endpoints:
        sensor_uid = f"{runtime.prefix}_{runtime.device_id}.{endpoint.unique_key}"
        sensor_entity_id = f"sensor.{slugify(f'{runtime.prefix}_{endpoint.name}')}"
        possible_sensor_uids = _possible_unique_ids(runtime, endpoint.unique_key)
        _adopt_legacy_registry_entry(
            registry,
            domain="sensor",
            desired_entity_id=sensor_entity_id,
            desired_unique_id=sensor_uid,
            possible_unique_ids=possible_sensor_uids,
            config_entry_id=entry.entry_id,
        )
        _migrate_registry_entry(
            registry,
            _get_registry_entry(registry, "sensor", DOMAIN, sensor_uid),
            sensor_entity_id,
            endpoint.enabled_default,
        )
        if len(endpoint.valid_values) == 2:
            switch_uid = f"{sensor_uid}_switch"
            switch_entity_id = (
                f"switch.{slugify(f'{runtime.prefix}_{endpoint.name} Schalter')}"
            )
            possible_switch_uids = {
                f"{unique_id}_switch" for unique_id in possible_sensor_uids
            }
            _adopt_legacy_registry_entry(
                registry,
                domain="switch",
                desired_entity_id=switch_entity_id,
                desired_unique_id=switch_uid,
                possible_unique_ids=possible_switch_uids,
                config_entry_id=entry.entry_id,
            )
            _migrate_registry_entry(
                registry,
                _get_registry_entry(registry, "switch", DOMAIN, switch_uid),
                switch_entity_id,
                True,
            )


def _possible_unique_ids(runtime, endpoint_key: str) -> set[str]:
    """Return current and historical ETA unique IDs for one endpoint."""
    prefixes = {runtime.prefix, "eta", "eta_heating"}
    return {
        f"{prefix}_{runtime.device_id}.{endpoint_key}"
        for prefix in prefixes
        if prefix
    }


def _get_registry_entry(registry, domain: str, platform: str, unique_id: str):
    """Get one registry entry by domain/platform/unique_id."""
    entity_id = registry.async_get_entity_id(domain, platform, unique_id)
    if entity_id is None:
        return None
    return registry.async_get(entity_id)


def _adopt_legacy_registry_entry(
    registry,
    *,
    domain: str,
    desired_entity_id: str,
    desired_unique_id: str,
    possible_unique_ids: set[str],
    config_entry_id: str,
) -> None:
    """Move old eta_heating registry entries to the eta platform when possible."""
    legacy_entry = _find_legacy_registry_entry(
        registry,
        domain=domain,
        desired_entity_id=desired_entity_id,
        possible_unique_ids=possible_unique_ids,
    )
    if legacy_entry is None:
        return

    current_entry = _get_registry_entry(registry, domain, DOMAIN, desired_unique_id)
    if current_entry is not None and current_entry.entity_id != legacy_entry.entity_id:
        if not current_entry.entity_id.split(".", 1)[-1].startswith("eta_heating_"):
            return
        _move_duplicate_unique_id(registry, current_entry)

    try:
        registry.async_update_entity_platform(
            legacy_entry.entity_id,
            DOMAIN,
            new_config_entry_id=config_entry_id,
            new_unique_id=desired_unique_id,
        )
    except ValueError:
        return


def _find_legacy_registry_entry(
    registry,
    *,
    domain: str,
    desired_entity_id: str,
    possible_unique_ids: set[str],
):
    """Find a registry entry created by the old eta_heating platform."""
    target_entry = registry.async_get(desired_entity_id)
    if target_entry is not None and target_entry.domain == domain:
        if target_entry.platform == "eta_heating":
            return target_entry
        if (
            target_entry.platform == DOMAIN
            and target_entry.unique_id in possible_unique_ids
        ):
            return target_entry

    for unique_id in possible_unique_ids:
        entry = _get_registry_entry(registry, domain, "eta_heating", unique_id)
        if entry is not None:
            return entry
    return None


def _move_duplicate_unique_id(registry, registry_entry) -> None:
    """Keep duplicate eta_heating_* entries but free the desired unique id."""
    base_unique_id = f"{registry_entry.unique_id}_migrated_duplicate"
    for count in range(1, 100):
        suffix = "" if count == 1 else f"_{count}"
        try:
            registry.async_update_entity(
                registry_entry.entity_id,
                new_unique_id=f"{base_unique_id}{suffix}",
            )
            return
        except ValueError:
            continue


def _migrate_registry_entry(
    registry,
    registry_entry,
    desired_entity_id: str,
    should_enable: bool,
) -> None:
    """Apply one safe entity-registry migration."""
    if registry_entry is None:
        return

    updates: dict[str, Any] = {}
    if (
        registry_entry.entity_id != desired_entity_id
        and registry_entry.entity_id.split(".", 1)[-1].startswith("eta_heating_")
        and registry.async_get(desired_entity_id) is None
    ):
        updates["new_entity_id"] = desired_entity_id

    disabled_by = getattr(registry_entry, "disabled_by", None)
    integration_disabler = getattr(
        getattr(er, "RegistryEntryDisabler", object),
        "INTEGRATION",
        "integration",
    )
    if should_enable and disabled_by in (integration_disabler, "integration"):
        updates["disabled_by"] = None

    if updates:
        registry.async_update_entity(registry_entry.entity_id, **updates)
