# Space Dashboard Hardcopy — Summary & Automation Brief

Source artifact: `Space_Dashboard_Hardcopy.xlsx` (17 sheets, ~16 MB, hand-curated by analysts).
Reader persona: a stakeholder whose mandate is **international space partnerships and cooperation** — they need to land on a country, an organization, or a mission area and quickly understand the landscape, the actors, the money, and the existing activity.

---

## 1. Executive summary

The workbook is a **flat-file intelligence binder**. It tries to do four things at once:

| Layer | Question it answers | Sheets |
| --- | --- | --- |
| **Landscape & reference** | Who exists? Where are they? What taxonomies do we use? | Universal Legend, Organization List, Data Validation Lists, LatLong Auto-Tagging, Launch Sites, Partnership Sources |
| **Capacity** | How much can each country actually spend or build? | Budget Toplines, Defense Spending, Space Program Investments, Industrial Base Scores |
| **Activity** | What is on orbit, who builds it, and who is partnering with whom? | Space Assets, International Partnerships, Industry List |
| **Commercial signal** | Where is private capital flowing? | VC Investments, Macro VC Flow |
| **Analyst output** | Given all of the above, what should we prioritize? | Investment Outlook |

**Automation thesis (one paragraph):** The cataloging layers (assets, deals, sites, geocoding, taxonomies, defense spending) are largely solved problems with established external sources — they should be ingested by connectors on a schedule, not maintained by hand. The activity layer (international partnerships, program investments) is the highest-leverage automation target: today an analyst reads a press release and fills 30 columns; an LLM extraction pipeline can produce a draft row from the same article and route it to a human review queue. The scoring/narrative layer (Investment Outlook, Industrial Base Scores, Partnership Strength) is where analyst judgment is the product — automate the *evidence gathering* that feeds it, but keep the human authoring the verdict.

---

## 2. Persona use-cases

The workbook supports three recurring questions:

1. **"What is country X currently doing in space, and where is it headed?"**
   Pull the country row from **Universal Legend** for the COCOM and mission applicability, the per-mission rows from **Investment Outlook** for the near/long-term narrative, the asset roster from **Space Assets**, the flagship programs from **Space Program Investments**, and the budget envelope from **Budget Toplines** + **Defense Spending**.

2. **"Who could we partner with on mission Y in country X?"**
   **Industry List** filtered by country + mission gives candidate companies (with HQ city, value-chain segment, lat/long); **Organization List** gives the relevant agencies; **Industrial Base Scores** tells you whether the country has expert-rated capability in that value-chain × mission cell; **International Partnerships** shows precedent — who has already partnered there and on what.

3. **"Where is private capital concentrating, and is it aligned with our priorities?"**
   **VC Investments** is the deal-level raw feed (PitchBook export); **Macro VC Flow** is the year × source-country × destination-country pivot. Cross-reference against **Investment Outlook** priorities to spot capital/strategy mismatches.

---

## 3. Sheet-by-sheet breakdown

Each block: **Purpose · Key fields · How it's produced today · Automation outlook**.

### Landscape & reference

#### Universal Legend *(210 × 16)*
- **Purpose:** Master country table mapping every country to its US Combatant Command (COCOM) and providing a binary on/off flag for each value-chain segment and mission area. Acts as the "is this country in scope for X?" lookup the rest of the workbook joins against.
- **Key fields:** `Country`, `COCOM`, the value-chain columns (Payloads & Sensors, Bus & Spacecraft Integration, Launch, Ground, Data Processing, Other), the mission columns (SATCOM, SDA, ISR, MW&T, Sci/Exp, PNT).
- **How produced:** Hand-maintained reference. Country→COCOM is from DoD's Unified Command Plan; the on/off flags appear to default to 1 (in-scope) for all rows in the current snapshot.
- **Automation outlook:** **Fully automatable.** Country→COCOM is a small, near-static public table — load once from DoD/Wikipedia, refresh on UCP revisions. The value-chain/mission flags should be *derived* from Industry List + Space Assets ("does country X have any actor or asset in this segment?") rather than maintained by hand.

#### Organization List *(699 × 11)*
- **Purpose:** Authoritative roster of agencies, ministries, militaries, and notable orgs that appear elsewhere as `Organization 1` / `Organization 2` in partnerships.
- **Key fields:** sparse in this snapshot (most rows blank in the captured range), but functions as a controlled vocabulary for partnership tagging.
- **How produced:** Curated as analysts encounter new orgs.
- **Automation outlook:** **Mostly automatable (human review).** Auto-suggest new entries by clustering org strings extracted from partnership descriptions; surface candidates in a review UI before promoting them to the canonical list.

#### Data Validation Lists *(5,003 × 80)*
- **Purpose:** All dropdown enumerations and the scoring rubrics behind them — partnership type → score, business model → score, mission type → score, level of commitment → score, and the lookup that combines model+mission into a strength score.
- **Key fields:** paired (label, score) columns for `Partnership Type`, `Level of Commitment`, `Business Model`, `Mission Type`; a `PartnershipModelMission Concat` lookup; `All COCOMs` / `All Countries` / `Priority?` reference columns.
- **How produced:** Designed once, edited rarely. Drives Excel data validation everywhere else.
- **Automation outlook:** **Fully automatable.** This is a schema, not data. Move it into a database (or a typed config file) so every other system enforces the same vocabulary; the scoring tables become deterministic functions.

#### LatLong Auto-Tagging *(42,906 × 17)*
- **Purpose:** World-cities gazetteer (SimpleMaps World Cities export) used to attach lat/long to a city+country string anywhere it appears (Industry List HQ city, partnership locations, launch sites).
- **Key fields:** `city`, `country`, `lat`, `lng`, `iso2`, `iso3`, `CityCountry CONCAT` (the join key).
- **How produced:** Static download, refreshed when SimpleMaps updates.
- **Automation outlook:** **Fully automatable.** Replace with either (a) a persistent gazetteer table backed by the same dataset, or (b) a geocoding API call (Mapbox/Google/Nominatim) at write time. The 42k-row sheet is dead weight in an interactive workbook.

#### Launch Sites *(452 × 14)*
- **Purpose:** Catalog of suborbital and orbital launch infrastructure worldwide with status (active / completed) and lat/long.
- **Key fields:** `Country`, `Type`, `Status`, `Full Name`, `Latitude`, `Longitude`.
- **How produced:** Scraped from `space.skyrocket.de/directories/launchsites.htm` per the URL preserved in row 1.
- **Automation outlook:** **Fully automatable.** A scheduled scrape of the same source, with FAA AST and Wikipedia as cross-checks, replaces this entirely.

#### Partnership Sources *(1,080 × 2)*
- **Purpose:** Tally of source domains used to harvest the International Partnerships rows (NASA, SpaceNews, africanews.space, etc.), useful for source-diversity audits.
- **Key fields:** `Source` (domain), `Count`.
- **How produced:** Pivot of source URLs from the partnerships sheet.
- **Automation outlook:** **Fully automatable.** Trivial derived view — emit at query time, never store.

### Capacity

#### Budget Toplines *(80 × 7)*
- **Purpose:** One-line civil/defense space budget per country with whether the figure is exact or estimated, the source method, and a CAGR band.
- **Key fields:** `Country`, `Civil/Defense`, `Estimate or Exact`, `Method`, `Topline Spend (Est.)`, `CAGR`.
- **How produced:** Analyst reads agency budget documents, press releases, and secondary reporting; estimates where exact figures are not public.
- **Automation outlook:** **Hybrid (LLM draft + analyst).** Primary sources (NASA congressional justifications, ESA Council releases, JAXA budget docs, ISRO outcome budget, national defense budgets) are public PDFs. An ingestion pipeline using LLM extraction can keep the "exact" rows fresh; the "estimate" rows still need the analyst's judgment about which press numbers to trust.

#### Defense Spending *(68 × 24)*
- **Purpose:** Country × year (2016–2029) total defense spending, in thousands of USD, by region.
- **Key fields:** `Region`, `Country`, year columns.
- **How produced:** Sourced from a defense-spending dataset (likely SIPRI or IISS Military Balance) plus a forward projection.
- **Automation outlook:** **Fully automatable.** SIPRI publishes a machine-readable database annually; ingest it directly. The forward projection (2025+) should be stamped with the projection model used so the reader knows it isn't observed.

#### Space Program Investments *(173 × 32)*
- **Purpose:** Per major flagship program (e.g., Italy's IRIDE), the funding profile by year (2018–2030), the lead organization, partnership ID linkage, mission, and overview narrative with three "Learn More" links.
- **Key fields:** `Country`, `Organization`, `Program`, `Primary Mission`, `Total Funding (local) (M)`, `Local Currency`, `Total Funding ($M)`, `Start Year`, `End Year`, year columns.
- **How produced:** Analyst identifies a flagship program (often via news), looks up government press releases for the funding envelope, and back-fills the year columns.
- **Automation outlook:** **Hybrid (LLM draft + analyst).** Program discovery can be automated via news monitoring + RSS on agency sites. Funding amounts and year splits often require translating local-currency figures from PDF press releases — well-suited to an LLM extraction step with the analyst confirming the numbers and the program scope.

#### Industrial Base Scores *(37 × 11)*
- **Purpose:** Expert 1–5 scoring of each major partner country's industrial capability per (value-chain segment × mission area) cell, with a free-text justification.
- **Key fields:** `Country`, `Value Chain Segment`, the per-mission score columns, `Notes/Justification`.
- **How produced:** Pure expert judgment from analysts who know the prime contractors and component suppliers in each country.
- **Automation outlook:** **Keep human-in-loop.** This is the workbook's most analyst-dense product. Automation should *support* the scorer (auto-build a dossier per country×segment cell from Industry List, Space Assets, and recent partnerships) but never replace them — the value here is the ranking and its defensibility.

### Activity (the big curated tables)

#### Space Assets *(5,547 × 59)*
- **Purpose:** Catalog of every spacecraft attributed to each country's space inventory, with owner/operator, prime + subcontractors, launch provider/vehicle, lifecycle dates, and a 4-component score (Coverage, Mass, Launch Year, Capability) that rolls into a Final Score.
- **Key fields:** `Profile Country`, `Spacecraft Name`, `Owner`, `Operator`, `Mission Type`, `Prime Contractor (+ Country)`, `Launch Provider`, `Launch Year`, `EOL Year`, `Final Score`, the per-mission columns (SATCOM, PNT, ISR, etc.), `Orbit`, `Mass`, `Mass Class`.
- **How produced:** Cross-reference of public satellite databases plus analyst attribution to the operating country and mission category, plus the scoring formulas.
- **Automation outlook:** **Mostly automatable (human review).** Ingest UCS/CSIS Satellite Database, Jonathan McDowell's GCAT, Celestrak, and operator press releases. Mission categorization is the part that needs review (a SATCOM bird with a hosted ISR payload is genuinely ambiguous). The four scoring components are deterministic — codify the rubric and let it recompute on every refresh.

#### International Partnerships *(7,618 × 36)*
- **Purpose:** The largest curated table in the workbook. Every observed bilateral or multilateral space partnership: parties (countries, COCOMs, organizations, companies on both sides), the partnership type, mission area, business model (G2G / B2B / B2G / G2B), level of commitment, and a strength score, plus the source URL(s) and the underlying news description.
- **Key fields:** `Partnership ID`, `Description`, `Partnership Year`, `Partnership Type`, `Level of Commitment`, `Business Model`, `Mission Type`, `Partnership Strength`, the Country/COCOM/Organization/Company pairs (1 and 2), `Learn More Link`.
- **How produced:** Analyst monitors space-news domains (per Partnership Sources tally), reads each article, fills the structured fields by hand, and pastes the source description.
- **Automation outlook:** **Hybrid (LLM draft + analyst) — highest-leverage automation target.** A pipeline of (RSS/Google Alerts on tracked domains) → (LLM extraction into the schema) → (review queue with the original description visible) would replace ~80% of the manual data entry. Strength scoring becomes a deterministic function of the extracted business model + mission + partnership type via the Data Validation Lists rubric. Keep the analyst as final approver, especially for ambiguous cases.

#### Industry List *(3,294 × 37)*
- **Purpose:** Master roster of companies relevant to each country's space industrial base. Captures business unit vs. parent (separate HQ), founding year, primary value-chain segment, the value-chain capability columns, mission coverage, and lat/long.
- **Key fields:** `Profile Country`, `Relevant Business Unit`, `Bus. Unit HQ City/Country`, `Parent Company`, `Founding Year`, `Primary Value Chain`, value-chain capability columns, mission columns, `Calculated Lat/Long`.
- **How produced:** Analyst seeds with PitchBook + corporate websites + press; tags value-chain and missions manually.
- **Automation outlook:** **Hybrid (LLM draft + analyst).** Company discovery and HQ data come from PitchBook/Crunchbase/LinkedIn connectors + corporate sites. Value-chain and mission tagging are well-suited to LLM classification using the company description. Geocoding via the LatLong sheet → API. Analyst keeps a thin review pass for misclassifications.

### Commercial signal

#### VC Investments *(3,978 × 166)*
- **Purpose:** Deal-level PitchBook-style export covering every space-relevant VC deal — financing status, deal size, valuation, investors, syndicates, board representation, employees, etc.
- **Key fields (most analytical):** `Companies`, `Description`, `Primary Industry Group`, `Verticals`, `Deal Date`, `Deal Size`, `Pre/Post Valuation`, `Series`, `Deal Type`, `# Investors`, `New Investors`.
- **How produced:** Direct PitchBook export — almost certainly a saved search dumped to xlsx.
- **Automation outlook:** **Fully automatable.** PitchBook offers an API; replace the manual export with a scheduled pull on a saved query. If PitchBook access isn't available, Crunchbase + Dealroom + SpaceCapital's quarterly reports cover most of the same deals.

#### Macro VC Flow *(589 × 54)*
- **Purpose:** Year × source-country × destination-country VC flow matrix derived from the deal-level data — answers "which countries' investors are funding which countries' companies, and how is that changing?"
- **Key fields:** Year, `Type` (Sum/Count), `Country` (source), and a column per destination country.
- **How produced:** Pivot of VC Investments.
- **Automation outlook:** **Fully automatable.** Pure derived view — emit from the warehouse query, never store.

### Analyst output

#### Investment Outlook *(471 × 14)*
- **Purpose:** The headline analyst deliverable. For each (country × mission) pair, a near-term score (2024–2026) with a narrative paragraph of investment priorities, a long-term score (2027+) with its own narrative, the relevant agencies, three source URLs, a "Ready for review?" gate, and reviewer comments.
- **Key fields:** `Countries`, `Mission`, `Relevant Organizations`, `Near Term Score`, `Near-Term Investment Priorities`, `Long Term Score`, `Long Investment Priorities`, `Sources` (1/2/3), `Ready for Review?`, `SB / GJ Comments`.
- **How produced:** Analyst synthesizes from everything else — Space Assets, Space Program Investments, partnerships, news — and writes the narrative cell themselves; the reviewer (initials SB / GJ) signs off.
- **Automation outlook:** **Hybrid (LLM draft + analyst), narrative kept human.** Automate the *evidence pack* per cell (an auto-generated dossier of the relevant assets, programs, partnerships, and recent news for that country×mission). Optionally produce an LLM draft narrative pre-populated with citations. The scores and the final prose stay analyst-authored — they are the product the persona pays for.

### Section divider

#### Calculations & Validation -> *(1 × 1)*
Layout marker — separates the analytical sheets from the reference/validation sheets. No automation implication.

---

## 4. Cross-cutting automation roadmap

Six building blocks recur across the sheets. Building each one once unlocks several sheets at a time:

1. **Taxonomy as schema.** Move Data Validation Lists into a database with foreign-key constraints. Every other sheet stops drifting from the canonical vocabulary, and the scoring tables become deterministic functions.
2. **Geocoding service.** Replaces LatLong Auto-Tagging — either the gazetteer table behind a join, or a geocoder API at write time. Frees ~43k rows of dead weight from the workbook.
3. **News-monitoring + LLM extraction pipeline.** RSS / Google Alerts / scraped feeds on the source domains in Partnership Sources → LLM extracts into the Partnerships, Program Investments, and Investment Outlook *evidence* schemas → routed to a review queue. Highest single source of analyst time savings.
4. **External-database connectors.** Scheduled ingest from UCS/CSIS/GCAT (assets), PitchBook (deals), SIPRI (defense spending), agency budget docs (toplines), `space.skyrocket.de` and FAA AST (launch sites). Each connector replaces a recurring manual refresh.
5. **Scoring rubrics as functions.** The Space Assets composite score (Coverage / Mass / Launch Year / Capability → Final) and Partnership Strength (Type × Model × Mission lookup) are formulas — codify them so refreshing inputs auto-refreshes scores.
6. **Human review queue.** The connective tissue. For every automated draft (a partnership row, a company row, an asset reclassification, a dossier), the analyst sees the source artifact and the proposed structured output side-by-side and approves, edits, or rejects. Audit trail preserved.

A reasonable build order: 1 → 2 → 6 → 4 → 3 → 5. Schema and geocoding unblock everything; the review queue is the UX everything routes into; ingest connectors are independent and parallelizable; news-extraction is the highest-value pipeline but depends on the queue; scoring functions are the cherry on top.

---

## 5. What *not* to automate

Resist the urge to push automation into these areas — the manual work is the value:

- **Industrial Base Scores.** 37 rows of expert judgment, refreshed slowly. Automating loses the ranking's defensibility and the implicit knowledge encoded in the justifications.
- **Investment Outlook narrative paragraphs and final scores.** The analyst's voice and source-weighting are the product. Automate the evidence pack feeding the analyst, not the verdict.
- **Partnership Strength edge cases.** The lookup-based default score is fine for well-formed entries, but ambiguous partnerships (informal MoUs, dual-use commercial deals, multi-party consortia) need analyst override.
- **Anything drawing on non-public knowledge** — relationships, classified context, off-the-record analyst sources. The system should *flag* where it lacks coverage and let the analyst fill the gap, not invent.

The right framing: automation handles **breadth** (refresh everything, miss nothing); the analyst owns **depth** (judgment calls that have to be defended to a stakeholder).
