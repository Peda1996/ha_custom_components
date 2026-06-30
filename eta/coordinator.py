"""ETA Home Assistant data coordinator."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EtaApiClient, EtaApiError, EtaEndpoint, EtaValue
from .const import DEFAULT_DISCOVERY_WORKERS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EtaCoordinator(DataUpdateCoordinator[dict[str, EtaValue]]):
    """Fetch ETA entity states, preferably through one ETA variable set."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EtaApiClient,
        *,
        name: str,
        endpoints: list[EtaEndpoint],
        update_interval: timedelta,
        use_variable_set: bool,
        variable_set_name: str,
        workers: int = DEFAULT_DISCOVERY_WORKERS,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{name}",
            update_interval=update_interval,
        )
        self.client = client
        self.endpoints = endpoints
        self.use_variable_set = use_variable_set
        self.variable_set_name = variable_set_name
        self.workers = workers
        self._variable_set_ready = False

    async def _async_update_data(self) -> dict[str, EtaValue]:
        uris = [endpoint.uri for endpoint in self.endpoints]
        if self.use_variable_set and uris:
            try:
                if not self._variable_set_ready:
                    await self.client.async_ensure_variable_set(
                        self.variable_set_name,
                        uris,
                        limit=max(1, min(self.workers, 16)),
                    )
                    self._variable_set_ready = True

                values = await self.client.async_get_variable_set(self.variable_set_name)
                if values:
                    return values
            except EtaApiError as err:
                self._variable_set_ready = False
                _LOGGER.warning(
                    "ETA variable set update failed; falling back to individual reads: %s",
                    err,
                )

        try:
            return await self.client.async_get_values(
                uris,
                limit=max(1, self.workers),
            )
        except EtaApiError as err:
            raise UpdateFailed(str(err)) from err

