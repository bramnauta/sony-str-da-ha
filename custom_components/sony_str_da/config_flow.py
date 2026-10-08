"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from . import SonyStrDaConfigEntry
from .client import DEFAULT_PORT, CannotConnect, ExternalControlDisabled, async_probe
from .const import (
    CONF_MAX_DB,
    CONF_MIN_DB,
    CONF_SOURCES,
    DEFAULT_MAX_DB,
    DEFAULT_MIN_DB,
    DOMAIN,
)
from .protocol import parse_sources


class SonyStrDaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SonyStrDaConfigEntry) -> OptionsFlow:
        return SonyStrDaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            try:
                model = await async_probe(host, port)
            except ExternalControlDisabled:
                errors["base"] = "external_control_disabled"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=model, data={CONF_HOST: host, CONF_PORT: port}
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )


class SonyStrDaOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_sources(user_input.get(CONF_SOURCES, ""))
            except ValueError:
                errors[CONF_SOURCES] = "invalid_sources"
            if user_input[CONF_MIN_DB] >= user_input[CONF_MAX_DB]:
                errors["base"] = "invalid_range"
            if not errors:
                return self.async_create_entry(data=user_input)

        db_selector = NumberSelector(
            NumberSelectorConfig(
                min=-100, max=20, step=0.5, unit_of_measurement="dB", mode=NumberSelectorMode.BOX
            )
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_MIN_DB, default=DEFAULT_MIN_DB): db_selector,
                vol.Required(CONF_MAX_DB, default=DEFAULT_MAX_DB): db_selector,
                vol.Optional(CONF_SOURCES, default=""): TextSelector(
                    TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.config_entry.options
            ),
            errors=errors,
        )
