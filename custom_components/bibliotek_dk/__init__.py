"""The Bibliotek DK integration."""
from __future__ import annotations
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .media_source import BibliotekAudioView

from .library_api import Library

from .const import (
    CONF_AGENCY,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_PINCODE,
    CONF_USER_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

import os
import tempfile

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bibliotek from a config entry."""
    hass.data.setdefault(DOMAIN, {"entries": {}})

    # Create Library instance
    library = Library(
        entry.data[CONF_USER_ID],
        entry.data[CONF_PINCODE],
        entry.data[CONF_HOST],
        entry.data[CONF_AGENCY],
        libraryName=entry.data[CONF_MUNICIPALITY],
    )

    display_name = entry.title or entry.data.get(CONF_MUNICIPALITY, "Bibliotek")

    # Register under a clean structure for media_source
    hass.data[DOMAIN]["entries"][entry.entry_id] = {
        "library": library,
        "title": display_name,
    }

    temp_audio_dir = os.path.join(tempfile.gettempdir(), "bibliotek_dk_audio")
    os.makedirs(temp_audio_dir, exist_ok=True)

    hass.http.register_view(BibliotekAudioView(temp_audio_dir))
    hass.data[DOMAIN]["audio_dir"] = temp_audio_dir


    # Legacy path for sensor support (optional)
    hass.data[DOMAIN][entry.entry_id] = library

    _LOGGER.debug("Registered Bibliotek entry: %s → %s", entry.entry_id, display_name)
    _LOGGER.debug("All entries so far: %s", list(hass.data[DOMAIN]["entries"].keys()))

    entry.async_on_unload(entry.add_update_listener(update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN]["entries"].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
