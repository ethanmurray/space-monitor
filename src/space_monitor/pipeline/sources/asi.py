"""Italian Space Agency (Agenzia Spaziale Italiana) RSS adapter.

Italian-language. Claude handles non-English content fine; the controlled
vocabulary stays English. Feed at https://www.asi.it/feed/ (~50 entries).
"""

from ._rss import RSSSource


class AsiSource(RSSSource):
    name = "asi"
    domain = "asi.it"
    feed_url = "https://www.asi.it/feed/"
