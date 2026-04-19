"""NASA news-release RSS adapter (https://www.nasa.gov/news-release/feed/)."""

from ._rss import RSSSource


class NasaSource(RSSSource):
    name = "nasa"
    domain = "nasa.gov"
    feed_url = "https://www.nasa.gov/news-release/feed/"
