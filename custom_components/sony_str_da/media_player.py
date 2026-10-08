"""Media player for the main zone of a Sony STR-DA ES receiver."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SonyStrDaConfigEntry
from .client import ReceiverError
from .const import (
    CONF_MAX_DB,
    CONF_MIN_DB,
    CONF_SOURCES,
    DEFAULT_MAX_DB,
    DEFAULT_MIN_DB,
    DOMAIN,
)
from .protocol import DEFAULT_SOURCE_CODES, INPUT_NAMES, SOUND_MODES, parse_sources

VOLUME_STEP_COUNT = 2  # one volume up/down button press = 2 steps = 1 dB


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SonyStrDaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SonyStrDaMediaPlayer(entry)])


class SonyStrDaMediaPlayer(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.SELECT_SOUND_MODE
    )

    def __init__(self, entry: SonyStrDaConfigEntry) -> None:
        self._client = entry.runtime_data
        self._attr_unique_id = entry.unique_id or entry.entry_id
        self._min_db = float(entry.options.get(CONF_MIN_DB, DEFAULT_MIN_DB))
        self._max_db = float(entry.options.get(CONF_MAX_DB, DEFAULT_MAX_DB))

        names = {code: INPUT_NAMES[code] for code in DEFAULT_SOURCE_CODES}
        names.update(parse_sources(entry.options.get(CONF_SOURCES, "")))
        self._source_names = names
        self._source_codes = {name: code for code, name in names.items()}
        self._attr_source_list = list(self._source_codes)

        self._sound_codes = {name: code for code, name in SOUND_MODES.items()}
        self._attr_sound_mode_list = list(self._sound_codes)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            manufacturer="Sony",
            model=entry.title,
            name=entry.title,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._client.state.power
        if power is None:
            return None
        return MediaPlayerState.ON if power else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        db = self._client.state.volume_db
        if db is None:
            return None
        level = (db - self._min_db) / (self._max_db - self._min_db)
        return min(1.0, max(0.0, level))

    @property
    def is_volume_muted(self) -> bool | None:
        return self._client.state.muted

    @property
    def source(self) -> str | None:
        code = self._client.state.input_code
        if code is None:
            return None
        return self._source_names.get(code) or INPUT_NAMES.get(code) or f"Input {code:02X}"

    @property
    def sound_mode(self) -> str | None:
        code = self._client.state.sound_mode
        if code is None:
            return None
        return SOUND_MODES.get(code, f"Mode {code:02X}")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._client.state
        attributes: dict[str, Any] = {}
        if state.input_code is not None:
            # Handy to find the code of an input that is not in the source list
            # yet: select it on the receiver and read it here.
            attributes["input_code"] = f"{state.input_code:02X}"
        if state.volume_db is not None:
            attributes["volume_db"] = state.volume_db
        return attributes

    async def _run(self, call: Awaitable[None]) -> None:
        try:
            await call
        except ReceiverError as err:
            raise HomeAssistantError(f"Sony receiver: {err or type(err).__name__}") from err

    async def async_turn_on(self) -> None:
        await self._run(self._client.async_set_power(True))

    async def async_turn_off(self) -> None:
        await self._run(self._client.async_set_power(False))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._run(self._client.async_set_mute(mute))

    async def async_set_volume_level(self, volume: float) -> None:
        db = self._min_db + volume * (self._max_db - self._min_db)
        db = min(self._max_db, max(self._min_db, round(db * 2) / 2))
        self._client.set_volume_db(db)

    async def async_volume_up(self) -> None:
        db = self._client.state.volume_db
        if db is not None and db >= self._max_db:
            return
        await self._run(self._client.async_volume_step(True, VOLUME_STEP_COUNT))

    async def async_volume_down(self) -> None:
        await self._run(self._client.async_volume_step(False, VOLUME_STEP_COUNT))

    async def async_select_source(self, source: str) -> None:
        if (code := self._source_codes.get(source)) is None:
            raise HomeAssistantError(f"Unknown source {source}")
        await self._run(self._client.async_set_input(code))

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        if (code := self._sound_codes.get(sound_mode)) is None:
            raise HomeAssistantError(f"Unknown sound mode {sound_mode}")
        await self._run(self._client.async_set_sound_mode(code))
