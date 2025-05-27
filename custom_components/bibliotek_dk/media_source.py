import logging
import os
import subprocess

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return EreolenMediaSource(hass)


class EreolenMediaSource(MediaSource):
    def __init__(self, hass: HomeAssistant):
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        _LOGGER.debug("Browsing media for item: %s", item)
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

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
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

        order_id = book.get("order_id")
        _LOGGER.debug("Resolved order_id: %s", order_id)

        output_dir = self.hass.data[DOMAIN]["audio_dir"]
        output_path = os.path.join(output_dir, f"{order_id}.mp3")

        if not os.path.isfile(output_path):
            _LOGGER.debug("Starting download: %s", output_path)

            os.makedirs(output_dir, exist_ok=True)
            stream_url = f"https://audio.api.streaming.pubhub.dk/v1/stream/hls/{order_id}/playlist.m3u8"
            command = [
                "ffmpeg",
                "-i", stream_url,
                "-vn",
                output_path
            ]
            try:
                subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                _LOGGER.info("Downloaded audiobook to: %s", output_path)
            except subprocess.CalledProcessError as e:
                _LOGGER.error("FFmpeg failed: %s", e.stderr.decode())
                raise ValueError("Failed to download audio")

        return PlayMedia(
            url=f"/bibliotek_dk/ereolen/{order_id}.mp3",
            mime_type="audio/mpeg",
        )
    
class EreolenServer(HomeAssistantView):
    url = "/bibliotek_dk/ereolen/{order_id}.mp3"
    name = "bibliotek_dk:ereolen"
    requires_auth = True

    def __init__(self, directory):
        self._directory = directory

    async def get(self, request, order_id):
        from aiohttp import web
        file_path = os.path.join(self._directory, f"{order_id}.mp3")
        if not os.path.isfile(file_path):
            return web.Response(status=404, text="Audio file not found")
        return web.FileResponse(path=file_path, headers={"Content-Type": "audio/mpeg"})


