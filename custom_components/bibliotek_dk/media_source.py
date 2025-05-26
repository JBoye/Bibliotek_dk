import logging
from homeassistant.components.media_source.models import (
    MediaSource,
    MediaSourceItem,
    BrowseMediaSource,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return BibliotekMediaSource(hass)


class BibliotekMediaSource(MediaSource):
    """Media Source exposing Bibliotek audiobooks."""

    def __init__(self, hass: HomeAssistant):
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        entries = self.hass.data.get(DOMAIN, {}).get("entries", {})
        identifier = item.identifier

        if identifier is None:
            children = [
                BrowseMediaSource(
                    domain=self.domain,
                    identifier=entry_id,
                    title=data.get("title", f"User {entry_id}"),
                    media_class="directory",
                    media_content_type="library",
                    can_expand=True,
                    can_play=False,
                )
                for entry_id, data in entries.items()
            ]
            return BrowseMediaSource(
                domain=self.domain,
                identifier=None,
                title="eReolen lydbøger",
                media_class="directory",
                media_content_type="library",
                can_expand=True,
                can_play=False,
                children=children,
            )

        if "|" in identifier:
            entry_id, slug = identifier.split("|", 1)
            raise ValueError("Cannot expand individual book item")
        else:
            entry_id = identifier

        entry = entries.get(entry_id)
        if not entry or "library" not in entry:
            raise ValueError(f"'library' in entry '{entry_id}' is not valid")

        library = entry["library"]

        try:
            loans = library.get_audiobooks()
        except Exception as e:
            _LOGGER.exception("Failed to retrieve loans for entry %s: %s", entry_id, e)
            raise ValueError(f"Could not retrieve loans for entry '{entry_id}'")

        children = [
            BrowseMediaSource(
                domain=self.domain,
                identifier=f"{entry_id}|{slugify(book.get('title', 'Untitled'))}",
                title=book.get("title", "Untitled"),
                media_class="music",
                media_content_type="audio/mpeg",
                can_play=True,
                can_expand=False,
                thumbnail=book.get("cover"),
            )
            for book in loans
            if book.get("order_id")
        ]

        return BrowseMediaSource(
            domain=self.domain,
            identifier=entry_id,
            title=entry.get("title", entry_id),
            media_class="directory",
            media_content_type="library",
            can_expand=True,
            can_play=False,
            children=children,
        )

    async def async_resolve_media(self, item: MediaSourceItem) -> dict:
        _LOGGER.debug("Resolving media for item: %s", item)
        try:
            entry_id, slug = item.identifier.split("|", 1)
        except ValueError:
            raise ValueError(f"Invalid identifier format: {item.identifier}")

        entries = self.hass.data.get(DOMAIN, {}).get("entries", {})
        entry = entries.get(entry_id)
        if not entry or "library" not in entry:
            raise ValueError(f"Entry '{entry_id}' not found or invalid")

        library = entry["library"]
        loans = library.get_audiobooks()
        book = next(
            (b for b in loans if b.get("order_id") and slugify(b.get("title")) == slug),
            None,
        )

        if not book:
            raise ValueError(f"Book not found for slug: {slug}")
        


        filename = f"{slug}.mp3"

        _LOGGER.debug("Resolved filename: %s", filename)
        return {
            "mime_type": "audio/mpeg",
            "url": f"/media/local/{filename}",
        }
