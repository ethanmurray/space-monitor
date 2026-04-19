"""European Space Agency RSS adapter."""

from ._rss import RSSSource


class EsaSource(RSSSource):
    name = "esa"
    domain = "esa.int"
    feed_url = "https://www.esa.int/rssfeed/Our_Activities"
