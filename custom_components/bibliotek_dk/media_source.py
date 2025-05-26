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

        _LOGGER.debug("🟦 Browsing item: %s", identifier)
        _LOGGER.debug("🟦 Available entry IDs: %s", list(entries.keys()))

        # Top-level directory
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
            is_book_item = True
        else:
            entry_id = identifier
            is_book_item = False

        entry = entries.get(entry_id)

        # Logging
        _LOGGER.debug("🟦 Raw entry object: %s", entry)
        _LOGGER.debug("🟦 Type of entry: %s", type(entry))

        try:
            library = entry["library"]
        except (KeyError, TypeError):
            _LOGGER.warning("Missing or invalid 'library' for entry %s", entry_id)
            raise ValueError(f"'library' in entry '{entry_id}' is not valid")

        _LOGGER.debug("📦 library type: %s from module: %s", type(library), type(library).__module__)
        _LOGGER.debug("🔍 library dir: %s", dir(library))

        if not hasattr(library, "get_loans") or not callable(library.get_loans):
            raise ValueError(f"'get_loans' in entry '{entry_id}' is not callable")

        # ✅ Only now reject direct book expansion
        if is_book_item:
            raise ValueError("Cannot expand individual book item")

        # 🔥 This should now run!
        try:
            loans = library.get_loans()
            _LOGGER.debug("📚 get_loans() returned %d items", len(loans))
        except Exception as e:
            _LOGGER.exception("💥 Failed to call get_loans() on library for entry %s: %s", entry_id, e)
            raise ValueError(f"Could not retrieve loans for entry '{entry_id}'")
        _LOGGER.debug("📚 get_loans() returned %d items", len(loans))
        children = []

        for book in loans:
            _LOGGER.debug("📖 Book: %s", book)
            if not book.get("order_id"):
                continue

            title = book.get("title", "Untitled")
            slug = slugify(title)
            children.append(
                BrowseMediaSource(
                    domain=self.domain,
                    identifier=f"{entry_id}|{slug}",
                    title=title,
                    media_class="music",
                    media_content_type="audio/mpeg",
                    can_play=True,
                    can_expand=False,
                    thumbnail=book.get("cover"),
                    artist=book.get("creator"),
                )
            )

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
        """Resolve an audiobook item to a local MP3 file."""
        try:
            entry_id, slug = item.identifier.split("|", 1)
        except ValueError:
            raise ValueError(f"Invalid identifier format: {item.identifier}")

        entries = self.hass.data.get(DOMAIN, {}).get("entries", {})
        entry = entries.get(entry_id)
        if not entry or "library" not in entry:
            raise ValueError(f"Entry '{entry_id}' not found or invalid")

        library = entry["library"]
        loans = library.get_loans()
        book = next(
            (b for b in loans if b.get("order_id") and slugify(b.get("title")) == slug),
            None,
        )

        if not book:
            raise ValueError(f"Book not found for slug: {slug}")

        filename = f"{slug}.mp3"
        return {
            "mime_type": "audio/mpeg",
            "url": f"/media/local/{filename}",
        }
