"""SpaceWatch.Global RSS adapter (https://spacewatch.global/feed/)."""

from ._rss import RSSSource


class SpaceWatchSource(RSSSource):
    name = "spacewatch"
    domain = "spacewatch.global"
    feed_url = "https://spacewatch.global/feed/"
    # Cloudflare JS challenge blocks the httpx fetcher; needs a Playwright
    # path (see P2 in BACKLOG.md). Excluded from --source all until then.
    disabled = True
