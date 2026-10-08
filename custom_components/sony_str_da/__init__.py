"""Sony STR-DA ES receiver (RS-232C protocol over TCP)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .client import SonyReceiverClient

PLATFORMS = [Platform.MEDIA_PLAYER]

type SonyStrDaConfigEntry = ConfigEntry[SonyReceiverClient]


async def async_setup_entry(hass: HomeAssistant, entry: SonyStrDaConfigEntry) -> bool:
    client = SonyReceiverClient(entry.data[CONF_HOST], entry.data[CONF_PORT])
    # The client reconnects by itself, so a receiver that is off at startup only
    # makes the entity unavailable until it is reachable.
    entry.async_create_background_task(hass, client.run(), f"sony_str_da {client.host}")
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SonyStrDaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: SonyStrDaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
