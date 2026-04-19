"""Configured query set for the Google News RSS-search adapter.

Each entry runs as a separate ``news.google.com/rss/search?q=...`` request.
Edit this list to expand or narrow the discovery surface.

Three groups:

* **Topic queries** — broad space-industry signals in English.
* **Localized topic queries** — same intent, native language + region. Worth
  the duplication because Google News indexes regional press differently
  per (hl, gl) pair.
* **Country watchlist queries** — under-covered geographies where we want
  any space mention. Mostly catches contracts, agency announcements, and
  partnerships involving these countries that English-only feeds miss.

Cost note: each query returns up to 100 articles. With ~25 queries running
daily, the prefilter classifier runs against ~2.5K candidate titles and
extracts the survivors. Typical cost ceiling ~$30/month including all
existing sources.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    q: str
    hl: str = "en"          # interface language
    gl: str = "US"          # geographic location
    label: str = ""         # optional human label for logs

    @property
    def ceid(self) -> str:
        return f"{self.gl}:{self.hl}"


# English topic queries — broadest reach, captures the bulk of space
# industry news regardless of country.
TOPIC_EN: list[Query] = [
    Query(q='"space partnership" OR "space cooperation agreement"', label="partnership"),
    Query(q='"satellite launch contract" OR "launch services agreement"', label="launch_contract"),
    Query(q='"space agency" announces', label="agency_announces"),
    Query(q='"satellite constellation" deployed OR launched', label="constellation"),
    Query(q='"earth observation satellite" launched OR contract', label="eo_satellite"),
    Query(q='"lunar mission" contract OR partnership', label="lunar"),
    Query(q='"space technology transfer"', label="tech_transfer"),
    Query(q='"ground station agreement" OR "ground segment contract"', label="ground_station"),
    Query(q='"space strategy" published OR released OR adopted', label="strategy"),
    Query(q='"space budget" approved OR announced', label="budget"),
    Query(q='space "memorandum of understanding"', label="space_mou"),
    Query(q='satellite operator acquires OR acquisition', label="satop_acquisition"),
]


# Localized topic queries — same intent, native language. Each (hl, gl)
# pair changes which national press Google News indexes. Worth the
# duplication for catching regional outlets the English search misses.
TOPIC_LOCALIZED: list[Query] = [
    # French (France) — CNES-orbit press
    Query(q='"partenariat spatial" OR "coopération spatiale"', hl="fr", gl="FR", label="fr_partnership"),
    Query(q='satellite contrat lancement', hl="fr", gl="FR", label="fr_launch_contract"),
    # German (Germany) — DLR-orbit press
    Query(q='"Raumfahrt-Partnerschaft" OR "Weltraum-Kooperation"', hl="de", gl="DE", label="de_partnership"),
    # Japanese (Japan) — JAXA-orbit press
    Query(q='"宇宙協力" OR "宇宙パートナーシップ"', hl="ja", gl="JP", label="ja_partnership"),
    # Korean (South Korea) — KARI-orbit press
    Query(q='"우주 협력" OR "위성 발사"', hl="ko", gl="KR", label="ko_partnership"),
    # Portuguese (Brazil) — INPE-orbit press
    Query(q='"parceria espacial" OR "cooperação espacial" Brasil', hl="pt-BR", gl="BR", label="pt_br_partnership"),
    # Spanish — Latin America space cooperation
    Query(q='"cooperación espacial" satélite acuerdo', hl="es", gl="MX", label="es_partnership"),
    # Italian — ASI-orbit press
    Query(q='"cooperazione spaziale" OR "partenariato spaziale"', hl="it", gl="IT", label="it_partnership"),
    # Arabic — UAE / Saudi / Egypt space programs
    Query(q='"تعاون فضائي" OR "شراكة فضائية"', hl="ar", gl="AE", label="ar_partnership"),
]


# Country watchlist queries — broad "[country] + space" net for under-covered
# geographies. Most articles will be filtered by the prefilter; what remains
# is genuinely space-relevant news from places our existing sources miss.
COUNTRY_WATCHLIST: list[Query] = [
    Query(q='Vietnam space OR satellite VNSC', label="cn_vietnam"),
    Query(q='Indonesia "space agency" OR satellite LAPAN BRIN', label="cn_indonesia"),
    Query(q='Philippines satellite PhilSA', label="cn_philippines"),
    Query(q='Nigeria satellite NASRDA', label="cn_nigeria"),
    Query(q='Egypt "space agency" EgSA satellite', label="cn_egypt"),
    Query(q='"Saudi Arabia" satellite "space program"', label="cn_saudi"),
    Query(q='Kenya satellite KSA "space agency"', label="cn_kenya"),
    Query(q='Argentina CONAE satellite', label="cn_argentina"),
    Query(q='Mexico AEM satellite "space"', label="cn_mexico"),
    Query(q='Thailand GISTDA satellite', label="cn_thailand"),
    Query(q='Kazakhstan KazCosmos satellite', label="cn_kazakhstan"),
    Query(q='"Czech Republic" satellite "space"', label="cn_czech"),
]


ALL_QUERIES: list[Query] = TOPIC_EN + TOPIC_LOCALIZED + COUNTRY_WATCHLIST
