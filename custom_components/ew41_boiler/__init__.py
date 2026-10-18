"""Home Assistant entry lifecycle for EW41 Boiler."""

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv, service
import voluptuous as vol

from .coordinator import EW41Coordinator
from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.SELECT, Platform.SENSOR, Platform.BUTTON, Platform.BINARY_SENSOR]


async def async_setup(hass, config):
    service.async_register_platform_entity_service(
        hass, DOMAIN, "set_repeat_heating", entity_domain="climate",
        schema={vol.Required("on"): cv.boolean}, func="async_set_repeat_heating",
    )
    return True


async def async_setup_entry(hass, entry):
    coordinator = EW41Coordinator(hass, entry)
    await coordinator.async_initialize_repeat_state()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
