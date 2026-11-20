# Trip planning: what travellers check, and where the data comes from

CityPulse helps travellers decide **where and when** to go. This file lists every
aspect a traveller weighs when planning, with a real data source for each.

- **Built**: in the app now (`/trip/{city}`, `/destinations`), tested against the live source.
- **Tested, not built**: the source responds and fits; it is the next thing to add.
- **Candidate**: a known public source that has not been tried from this project yet. Check its terms before using it.

All built sources are free and need no API key.

## Weather and climate

| Aspect | Status | Source | Notes |
|---|---|---|---|
| Typical weather in the travel month (highs, lows) | Built | [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api) (ERA5 reanalysis) | Averages over the last 3 full years. Bundled for built-in destinations. |
| Rain: monthly total and rainy days | Built | Open-Meteo historical weather | A rainy day is 1 mm or more. |
| Sunshine hours per day | Built | Open-Meteo historical weather | |
| Humidity (how muggy it feels) | Built | Open-Meteo historical weather | Feeds the "hot and humid" caution. |
| Daylight hours | Built | Calculated from latitude | Standard sunrise equation, no API needed. |
| Best months to visit | Built | Derived from the monthly climate | Top 3 months by climate score. |
| Current weather and 3-day forecast | Built | [Open-Meteo forecast](https://open-meteo.com/en/docs) | For travellers already booked. |
| Sea temperature (beach and diving trips) | Tested, not built | [Open-Meteo marine](https://open-meteo.com/en/docs/marine-weather-api) | Current values only; needs a coastal point, inland cities return nothing useful. |
| UV index | Candidate | Open-Meteo forecast (`uv_index_max`) | Forecast only, no monthly history. |
| Pollen (allergies) | Candidate | Open-Meteo air quality (`grass_pollen`, `birch_pollen`, ...) | Europe only. |
| Air quality | Built (city snapshot) | [Open-Meteo air quality](https://open-meteo.com/en/docs/air-quality-api) | Current reading. |

## Safety and security

| Aspect | Status | Source | Notes |
|---|---|---|---|
| Official travel warnings | Built | [UK FCDO travel advice](https://www.gov.uk/foreign-travel-advice) content API | Warning levels: none, parts, whole country. Latest change summary and link included. |
| Violent crime (homicide rate) | Built | [World Bank `VC.IHR.PSRC.P5`](https://data.worldbank.org/indicator/VC.IHR.PSRC.P5) (UNODC data) | National, lags 1 to 2 years. |
| Street-level crime | Built (UK only) | [data.police.uk](https://data.police.uk/docs/) | No free equivalent for other countries. |
| Road safety | Built | [WHO GHO `RS_198`](https://www.who.int/data/gho) road traffic deaths per 100k | |
| Political stability and unrest risk | Built | [World Bank Worldwide Governance Indicators](https://www.worldbank.org/en/publication/worldwide-governance-indicators) `GOV_WGI_PV.EST` | Scale about -2.5 to +2.5. |
| US State Department advisory levels | Candidate | [travel.state.gov](https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories.html) RSS | A second official opinion next to the UK's. |
| Terrorism history | Candidate | [Global Terrorism Database](https://www.start.umd.edu/gtd/) | Needs registration; data lags. |
| LGBTQ+ safety and legal status | Candidate | [Equaldex API](https://www.equaldex.com/equality-index-api) | Free key required. |
| Women's safety | Candidate | [Georgetown Women, Peace and Security Index](https://giwps.georgetown.edu/the-index/) | Downloadable data, no API. |
| Corruption (bribes, police) | Candidate | [Transparency International CPI](https://www.transparency.org/en/cpi) | Yearly CSV download. |

## Health, diseases and pandemics

| Aspect | Status | Source | Notes |
|---|---|---|---|
| Disease outbreaks in the country (last 12 months) | Built | [WHO Disease Outbreak News](https://www.who.int/emergencies/disease-outbreak-news) API | Matched by country name; links to each WHO report. |
| Worldwide and pandemic alerts | Built | WHO Disease Outbreak News | Items marked Global or Multi-country. |
| Malaria risk | Built | [WHO GHO `MALARIA_EST_INCIDENCE`](https://www.who.int/data/gho) | Cases per 1,000 people at risk; no figure means no local transmission. |
| Safe drinking water | Built | [World Bank `SH.H2O.BASW.ZS`](https://data.worldbank.org/indicator/SH.H2O.BASW.ZS) (WHO/UNICEF JMP) | Share of people with basic drinking water. |
| Access to doctors | Built | [World Bank `SH.MED.PHYS.ZS`](https://data.worldbank.org/indicator/SH.MED.PHYS.ZS) | Physicians per 1,000 people. |
| Childhood vaccination coverage | Built | WHO GHO `WHS4_100` (DTP3), `WHS8_110` (measles) | Signal of general health-system strength. |
| Recommended and required travel vaccines | Candidate | [CDC Travelers' Health](https://wwwnc.cdc.gov/travel/destinations/list) | No public API; pages per country. |
| Dengue and other mosquito-borne diseases | Candidate | WHO GHO and [PAHO PLISA](https://www3.paho.org/data/index.php/en/) (Americas) | Patchy coverage outside the Americas. |
| Altitude sickness risk | Built (data) | Elevation from Open-Meteo geocoding | Shown for each city; above ~2,500 m is a risk. |

## Natural hazards and calamities

| Aspect | Status | Source | Notes |
|---|---|---|---|
| Active disaster alerts (cyclones, floods, droughts, volcanoes, earthquakes) | Built | [GDACS](https://www.gdacs.org/) (UN and European Commission) | Last 90 days, Green, Orange or Red alert levels. |
| Recent earthquakes nearby | Built (city snapshot) | [USGS earthquake feed](https://earthquake.usgs.gov/fdsnws/event/1/) | Within 300 km, last 30 days. |
| Open natural events (wildfires, storms, ice) | Built (city snapshot) | [NASA EONET](https://eonet.gsfc.nasa.gov/docs/v3) | |
| Hurricane and typhoon season | Candidate | [NOAA IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive) historical storm tracks | Would show which months storms usually hit. |
| Active wildfires | Candidate | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/api/) | Free key required. |
| Volcano activity | Candidate | [Smithsonian Global Volcanism Program](https://volcano.si.edu/) weekly reports | GDACS already covers major eruptions. |

## Money and costs

| Aspect | Status | Source | Notes |
|---|---|---|---|
| How expensive the country is for visitors | Built | World Bank `PA.NUS.PPP` / `PA.NUS.FCRF` | Price level relative to the US, labelled Very cheap to Very expensive. |
| Exchange rate | Built | [open.er-api.com](https://www.exchangerate-api.com/docs/free) | About 160 currencies, daily. |
| How touristy it is | Built | World Bank `ST.INT.ARVL` international arrivals | Pre-2020 years are the most complete. |
| Flight prices | Candidate | [Amadeus for Developers](https://developers.amadeus.com/) self-service | Free test tier with key; production needs sign-up. |
| Hotel prices | Candidate | Amadeus Hotel Search | Same as above. |
| Everyday prices (meals, transport) | Candidate | [Numbeo](https://www.numbeo.com/api/doc.jsp) | Paid API; crowdsourced. |

## Practical

| Aspect | Status | Source | Notes |
|---|---|---|---|
| Public holidays in the travel month | Built | [Nager.Date](https://date.nager.at/) | Crowds, closures, higher prices. About 120 countries. |
| Languages spoken | Built | Unicode CLDR (via `babel`) | Official languages per country. |
| Local time and time zone | Built | Open-Meteo | |
| Population and elevation | Built | Open-Meteo geocoding | |
| Visa requirements by passport | Candidate | [Passport Index dataset](https://github.com/ilyankou/passport-index-dataset) | Open CSV of visa rules between countries; updated irregularly. |
| Entry requirements detail | Candidate | FCDO "Entry requirements" page part | UK passport holders' view; already fetched with the advisory. |
| Plug type and voltage | Candidate | IEC World Plugs list | Static table, no API; could be bundled. |
| Emergency phone numbers | Candidate | Static table | Could be bundled. |
| Internet speed | Candidate | [Ookla open data](https://github.com/teamookla/ookla-open-data) | Quarterly tiles on AWS. |
| Driving side | Candidate | Static table | Could be bundled. |
| Events and festivals | Candidate | [PredictHQ](https://www.predicthq.com/), Ticketmaster Discovery | Key required. |

## How the trip score uses this

The trip score (0 to 100) weighs four areas for the chosen month:

| Area | Weight | Built from |
|---|---|---|
| Climate | 40 | Warmth (about 26 °C afternoons are ideal), dry days, sunshine, and a penalty for heat with high humidity |
| Safety | 30 | UK travel advice (40%), homicide rate (30%), political stability (15%), road deaths (15%) |
| Health | 18 | WHO outbreaks in the last year (35%), malaria (25%), drinking water (20%), doctors (10%), vaccination (10%) |
| Hazards | 12 | GDACS Red and Orange alerts in the last 90 days |

Two rules keep the score honest:

- An official "avoid travel to the whole country" warning caps safety at 35, so it can't be averaged away.
- A trip can't score more than 45 points above its climate score, so a freezing or monsoon month can't look great just because the country is safe.

Cost is shown but not scored: cheap is good for some travellers and a warning sign for others.
