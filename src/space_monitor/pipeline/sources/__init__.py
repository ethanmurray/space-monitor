"""Source adapters: each adapter discovers candidate article URLs from a single
news source. Add a source by creating a new module here that implements the
:class:`Source` protocol from :mod:`.base` and registering it in :data:`REGISTRY`.

Most adapters subclass :class:`._rss.RSSSource` and only set ``name``,
``domain``, and ``feed_url``. Sources without a usable RSS feed (corporate
sites behind JS, government portals, africanews.space) need bespoke fetchers
and are not yet wired up.
"""

from .asi import AsiSource
from .asianscientist import AsianScientistSource
from .base import CandidateArticle, Source
from .breakingdefense import BreakingDefenseSource
from .cnes import CnesSource
from .csa import CsaSource
from .defensenews import DefenseNewsSource
from .disdg import DisDgSource
from .esa import EsaSource
from .eusst import EusstSource
from .gnews import GNewsSource
from .govuk import GovUkSource
from .inpe import InpeSource
from .isa import IsaSource
from .mundogeo import MundoGeoSource
from .nasa import NasaSource
from .nasaspaceflight import NasaSpaceflightSource
from .payloadspace import PayloadSpaceSource
from .philsa import PhilSaSource
from .sansa import SansaSource
from .satellitetoday import SatelliteTodaySource
from .satnews import SatNewsSource
from .skao import SkaoSource
from .spacenews import SpaceNewsSource
from .spacepolicyonline import SpacePolicyOnlineSource
from .spacewatch import SpaceWatchSource
from .uae import UaeSpaceSource

REGISTRY: dict[str, Source] = {
    "spacenews": SpaceNewsSource(),
    "nasa": NasaSource(),
    "esa": EsaSource(),
    "govuk": GovUkSource(),
    "spacewatch": SpaceWatchSource(),
    "satnews": SatNewsSource(),
    "satellitetoday": SatelliteTodaySource(),
    "mundogeo": MundoGeoSource(),
    "asianscientist": AsianScientistSource(),
    "skao": SkaoSource(),
    "eusst": EusstSource(),
    "disdg": DisDgSource(),
    "asi": AsiSource(),
    "cnes": CnesSource(),
    "gnews": GNewsSource(),
    "payloadspace": PayloadSpaceSource(),
    "nasaspaceflight": NasaSpaceflightSource(),
    "spacepolicyonline": SpacePolicyOnlineSource(),
    "philsa": PhilSaSource(),
    "breakingdefense": BreakingDefenseSource(),
    "defensenews": DefenseNewsSource(),
    "sansa": SansaSource(),
    "csa": CsaSource(),
    "uae": UaeSpaceSource(),
    "inpe": InpeSource(),
    "isa": IsaSource(),
}

__all__ = ["CandidateArticle", "Source", "REGISTRY"]
