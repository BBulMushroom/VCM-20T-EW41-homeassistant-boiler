"""UI setup and editable options; setup only sends the confirmed status query."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, CONF_TIMEOUT
from homeassistant.core import callback

from .const import (
    CONF_MAX_TEMP, CONF_MIN_TEMP, DEFAULT_HOST, DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP,
    DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN,
)
from .protocol import EW41Client, EW41Error, encode_temperature


def settings_schema(settings, include_address=True):
    fields = {}
    if include_address:
        fields.update({
            vol.Required(CONF_HOST, default=settings.get(CONF_HOST, DEFAULT_HOST)): str,
            vol.Required(CONF_PORT, default=settings.get(CONF_PORT, DEFAULT_PORT)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        })
    fields.update({
        vol.Required(CONF_TIMEOUT, default=settings.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10)),
        vol.Required(CONF_SCAN_INTERVAL, default=settings.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
        vol.Required(CONF_MIN_TEMP, default=settings.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)): vol.Coerce(float),
        vol.Required(CONF_MAX_TEMP, default=settings.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)): vol.Coerce(float),
    })
    return vol.Schema(fields)


def valid_temperature_range(settings):
    try:
        encode_temperature(settings[CONF_MIN_TEMP])
        encode_temperature(settings[CONF_MAX_TEMP])
    except (KeyError, ValueError):
        return False
    return settings[CONF_MIN_TEMP] < settings[CONF_MAX_TEMP]


class EW41ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().lower()
            if not valid_temperature_range(user_input):
                errors["base"] = "invalid_temperature_range"
            else:
                await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                try:
                    client = EW41Client(user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_TIMEOUT])
                    await self.hass.async_add_executor_job(client.get_status)
                except (EW41Error, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title="EW41 보일러", data=user_input)
        return self.async_show_form(step_id="user", data_schema=settings_schema(user_input or {}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EW41OptionsFlow()


class EW41OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        settings = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            if valid_temperature_range(user_input):
                return self.async_create_entry(title="", data=user_input)
            errors["base"] = "invalid_temperature_range"
            settings.update(user_input)
        return self.async_show_form(step_id="init", data_schema=settings_schema(settings, False), errors=errors)
