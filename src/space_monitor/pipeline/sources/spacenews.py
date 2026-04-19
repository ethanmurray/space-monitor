"""SpaceNews RSS adapter (https://spacenews.com/feed/)."""

from ._rss import RSSSource


class SpaceNewsSource(RSSSource):
    name = "spacenews"
    domain = "spacenews.com"
    feed_url = "https://spacenews.com/feed/"
