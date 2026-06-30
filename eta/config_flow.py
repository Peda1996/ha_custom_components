"""Config flow for ETA Heating."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EtaApiClient, EtaApiError
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
from .runtime import normalize_config


class EtaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an ETA config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle manual UI setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = normalize_config(user_input)
            try:
                device_id = await self._async_validate(data)
            except EtaApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: data[CONF_HOST],
                        CONF_PORT: data[CONF_PORT],
                    }
                )
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_data_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_import(
        self,
        import_data: dict[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Import YAML config."""
        data = normalize_config(import_data)
        try:
            device_id = await self._async_validate(data)
        except EtaApiError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: data[CONF_PORT],
            }
        )
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EtaOptionsFlow:
        """Return the options flow."""
        return EtaOptionsFlow(config_entry)

    async def _async_validate(self, data: dict[str, Any]) -> str:
        client = EtaApiClient(
            data[CONF_HOST],
            int(data[CONF_PORT]),
            async_get_clientsession(self.hass),
        )
        return await client.async_get_device_id()


class EtaOptionsFlow(config_entries.OptionsFlow):
    """ETA options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Manage ETA options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = normalize_config({**self.config_entry.data, **self.config_entry.options})
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
        )


def _data_schema(values: dict[str, Any]) -> vol.Schema:
    """Return setup schema."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=values.get(CONF_HOST, "")): str,
            vol.Optional(CONF_PORT, default=values.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Optional(CONF_NAME, default=values.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Optional(
                CONF_PREFIX,
                default=values.get(CONF_PREFIX, DEFAULT_PREFIX),
            ): str,
        }
    )


def _options_schema(values: dict[str, Any]) -> vol.Schema:
    """Return options schema."""
    scan_interval = values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    if hasattr(scan_interval, "total_seconds"):
        scan_interval = int(scan_interval.total_seconds())

    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=int(scan_interval),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
            vol.Optional(
                CONF_CACHE_DISCOVERY,
                default=values.get(CONF_CACHE_DISCOVERY, DEFAULT_CACHE_DISCOVERY),
            ): bool,
            vol.Optional(
                CONF_USE_VARIABLE_SET,
                default=values.get(CONF_USE_VARIABLE_SET, DEFAULT_USE_VARIABLE_SET),
            ): bool,
            vol.Optional(
                CONF_DISCOVERY_WORKERS,
                default=values.get(CONF_DISCOVERY_WORKERS, DEFAULT_DISCOVERY_WORKERS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
            vol.Optional(
                CONF_FULL_SWITCH_DISCOVERY,
                default=values.get(
                    CONF_FULL_SWITCH_DISCOVERY,
                    DEFAULT_FULL_SWITCH_DISCOVERY,
                ),
            ): bool,
        }
    )
