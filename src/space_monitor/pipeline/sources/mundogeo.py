"""MundoGEO RSS adapter (https://mundogeo.com/feed/).

Brazilian Portuguese-language geospatial / space industry coverage. Claude
handles the language; the controlled vocabulary stays English.
"""

from ._rss import RSSSource


class MundoGeoSource(RSSSource):
    name = "mundogeo"
    domain = "mundogeo.com"
    feed_url = "https://mundogeo.com/feed/"
