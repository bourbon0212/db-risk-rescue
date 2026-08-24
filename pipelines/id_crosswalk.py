"""GTFS stop_id <-> station_id crosswalk for the scoped corridor.

Per DATA_SPEC.md Section 2 (pipelines/id_crosswalk.py) and Section 8 step 4.

Phase 2 v1 originally mirrored mock_data.json's 11-station corridor
exactly (DATA_SPEC.md §9.1), with an explicit note to "expand coverage
later once the pipeline is trusted." This is that expansion: a "Golden 35"
-- in practice 30 -- station corridor covering the country's major ICE
hubs and interchange points, so the network has real routing complexity
(multi-hop journeys, real transfer density) instead of a handful of
disconnected point-to-point legs.

All station_ids and their real GTFS.DE stop_ids below were looked up
directly against the downloaded fv_free/rv_free feeds (data/raw/gtfs_fv/,
data/raw/gtfs_rv/), not guessed. The build_real_dataset() Station list is
built directly from STATION_NAMES now (not mock_data.json) since the real
corridor has deliberately outgrown Phase 1's mock station set.

Several logical stations map to more than one real GTFS node, because
DELFI models split/multi-level stations as separate top-level parents
rather than one node with sub-areas -- found by tracing which parent
stop_id station-visit records at each big station actually use, not by
name search alone (name search alone misses these: e.g. Hamburg's S-Bahn
node is named "HBF/Kirchenallee" and doesn't contain "Hamburg" at all):
  - Frankfurt Hbf: surface node + S-Bahn tunnel level ("...tief")
  - Stuttgart Hbf: elevated tracks ("(oben)") + underground S-Bahn/
    regional level ("(tief)")
  - Leipzig Hbf: surface node + City-Tunnel level ("(tief)")
  - Berlin Hbf: DELFI's parent node is named "S+U Berlin Hauptbahnhof"
  - Muenchen Hbf: long-distance node + S-Bahn tunnel level, oddly named
    "Hauptbahnhof (U, Tram)" with no "Muenchen" in its own name at all
  - Hamburg Hbf: long-distance node ("Hamburg, Hamburg Hbf") + S-Bahn
    node ("Hamburg, HBF/Kirchenallee") -- the S-Bahn node alone carries
    ~4x the station-visit volume of the long-distance node
  - Erfurt Hbf: the dominant "Erfurt, Hauptbahnhof" node (1,146 visits)
    plus a much smaller secondary "Erfurt Hbf" node (34 visits, regional
    feed only) -- included for completeness even though minor
  - Kassel-Wilhelmshoehe: the main node plus a secondary "Bereich Gleis
    7/8" node for a specific platform pair (50 visits)

Three targeted "connector" stations were added to unlock stations that were
"one hop away" from the rest of the corridor. Two are confirmed working;
one (Freilassing) turned out not to be enough on its own -- verified by
tracing each station's real GTFS trip sequence, not assumed:
  - Dresden-Neustadt: directly (consecutively) adjacent to both Dresden Hbf
    and Berlin Suedkreuz -- confirmed, unlocks Dresden Hbf.
  - Reutlingen Hbf: directly adjacent to Tuebingen Hbf (381 trips across the
    feed's 30-day window use this pair). Confirmed working -- but only on
    dates when that specific service pattern is active; as of 2026-08-24
    every relevant service_id's calendar.txt window starts several days in
    the future (a real scheduled-service gap in this feed snapshot, not a
    bug), so Tuebingen still shows 0 legs on *that* date specifically.
  - Freilassing: does NOT directly unlock Berchtesgaden Hbf, despite
    looking like the obvious junction. The real branch sequence is
    Freilassing -> Freilassing-Hofham -> Ainring -> Hammerau -> Piding ->
    Bad Reichenhall -> Bad Reichenhall-Kirchberg -> Bayerisch Gmain ->
    Bischofswiesen -> Berchtesgaden Hbf (8 intermediate stops), and
    Freilassing itself isn't even consecutively adjacent to Muenchen either
    (there's at least one more station in between toward Rosenheim).
    Freilassing is kept in the crosswalk anyway -- it's a real, useful
    junction in its own right -- but Berchtesgaden Hbf remains isolated.
    Fully connecting it would need most of that 8-station chain, which
    isn't proportionate for a single low-traffic branch terminus without
    an explicit decision to do so.

KNOWN GAP (still unresolved): Muenchen Hbf and Muenchen Marienplatz are
still not GTFS-adjacent -- Muenchen Karlsplatz sits between them and isn't
in this corridor. Not fixed in this expansion; would need Karlsplatz added
as its own station or multi-hop leg-building.
"""

GTFS_STOP_ID_TO_STATION_ID: dict[str, str] = {
    # --- original 11-station corridor (DATA_SPEC.md §9.1) ---
    "176697": "DE_FRA_HBF",  # Frankfurt (Main) Hauptbahnhof
    "335920": "DE_FRA_HBF",  # Frankfurt (Main) Hauptbahnhof tief (S-Bahn tunnel level)
    "517455": "DE_KOL_HBF",  # Koeln Hbf
    "668361": "DE_STG_HBF",  # Stuttgart Hbf -- "Hauptbahnhof (oben)" (elevated tracks)
    "362545": "DE_STG_HBF",  # Stuttgart Hbf (tief) -- underground S-Bahn/regional level
    "582139": "DE_MAN_HBF",  # Mannheim, Hauptbahnhof
    "635340": "DE_HEI_HBF",  # Heidelberg, Hauptbahnhof
    "9941": "DE_MUC_HBF",  # Muenchen Hbf (long-distance/regional surface node)
    "690993": "DE_MUC_HBF",  # Muenchen Hbf -- S-Bahn tunnel level ("Hauptbahnhof (U, Tram)")
    "99055": "DE_NUE_HBF",  # Nuernberg Hbf
    "53188": "DE_LEI_HBF",  # Leipzig Hbf
    "601768": "DE_LEI_HBF",  # Leipzig Hbf (tief) -- City-Tunnel level
    "613345": "DE_BER_HBF",  # "S+U Berlin Hauptbahnhof" -- DELFI's node name for Berlin Hbf
    "183027": "DE_MUC_MAR",  # Marienplatz (Muenchen)
    "456005": "DE_MUC_OST",  # Ostbahnhof (Muenchen) -- i.e. Muenchen Ost
    # --- "Golden 35" expansion: hubs/routing stations ---
    "416646": "DE_ERF_HBF",  # Erfurt, Hauptbahnhof (dominant node, 1,146 visits)
    "166299": "DE_ERF_HBF",  # Erfurt Hbf (minor secondary node, 34 visits, rv only)
    "531677": "DE_HAL_HBF",  # Halle(Saale)Hbf
    "19112": "DE_KAS_WIL",  # Kassel Bahnhof Wilhelmshoehe
    "341144": "DE_KAS_WIL",  # Kassel Bahnhof Wilhelmshoehe, Bereich Gleis 7/8
    "631640": "DE_WUE_HBF",  # Wuerzburg Hbf
    "93866": "DE_HAN_HBF",  # Hannover Hauptbahnhof
    # --- major/regional endpoints ---
    "428519": "DE_HAM_HBF",  # Hamburg, Hamburg Hbf (long-distance node)
    "52456": "DE_HAM_HBF",  # Hamburg, HBF/Kirchenallee -- the S-Bahn node (~4,686 visits)
    "80740": "DE_TUE_HBF",  # Tuebingen Hauptbahnhof
    "422410": "DE_BGD_HBF",  # Berchtesgaden Hbf
    # --- satellite/relief stations ---
    "391201": "DE_BER_SKZ",  # S Suedkreuz Bhf (Berlin)
    "446591": "DE_BER_SPD",  # S Spandau Bhf (Berlin)
    "95437": "DE_KOL_MSD",  # Koeln Messe/Deutz Bf
    "545038": "DE_MUC_PAS",  # Pasing (Muenchen Pasing)
    # --- additional major ICE stops ---
    "691821": "DE_DUS_HBF",  # Duesseldorf Hbf
    "640892": "DE_DOR_HBF",  # Dortmund Hbf
    "224643": "DE_DRE_HBF",  # Dresden Hauptbahnhof
    "477761": "DE_BRE_HBF",  # Bremen Hbf
    "267257": "DE_ESS_HBF",  # Essen Hbf
    "497089": "DE_KAR_HBF",  # Karlsruhe Hauptbahnhof
    "436294": "DE_BON_HBF",  # Bonn Hbf
    # --- connector stations (unlock a previously "one hop away" station) ---
    "629950": "DE_REU_HBF",  # Reutlingen Hauptbahnhof -- unlocks Tuebingen Hbf
    "252148": "DE_FRL",  # Freilassing -- unlocks Berchtesgaden Hbf
    "553920": "DE_DRE_NST",  # Dresden Bahnhof Neustadt -- unlocks Dresden Hbf
}

# Canonical display names for build_real_dataset()'s Station objects. The
# original 11 match mock_data.json's names exactly (kept stable rather than
# switching to the messier real feed names, same reasoning as line_id's
# short-name-over-raw-id choice in gtfs_ingest.py); the 19 new ones use a
# clean canonical form since there's no Phase 1 precedent to match.
STATION_NAMES: dict[str, str] = {
    "DE_FRA_HBF": "Frankfurt(Main)Hbf",
    "DE_KOL_HBF": "Köln Hbf",
    "DE_STG_HBF": "Stuttgart Hbf",
    "DE_MAN_HBF": "Mannheim Hbf",
    "DE_HEI_HBF": "Heidelberg Hbf",
    "DE_MUC_HBF": "München Hbf",
    "DE_NUE_HBF": "Nürnberg Hbf",
    "DE_LEI_HBF": "Leipzig Hbf",
    "DE_BER_HBF": "Berlin Hbf",
    "DE_MUC_MAR": "München Marienplatz",
    "DE_MUC_OST": "München Ost",
    "DE_ERF_HBF": "Erfurt Hbf",
    "DE_HAL_HBF": "Halle(Saale)Hbf",
    "DE_KAS_WIL": "Kassel-Wilhelmshöhe",
    "DE_WUE_HBF": "Würzburg Hbf",
    "DE_HAN_HBF": "Hannover Hbf",
    "DE_HAM_HBF": "Hamburg Hbf",
    "DE_TUE_HBF": "Tübingen Hbf",
    "DE_BGD_HBF": "Berchtesgaden Hbf",
    "DE_BER_SKZ": "Berlin Südkreuz",
    "DE_BER_SPD": "Berlin Spandau",
    "DE_KOL_MSD": "Köln Messe/Deutz",
    "DE_MUC_PAS": "München Pasing",
    "DE_DUS_HBF": "Düsseldorf Hbf",
    "DE_DOR_HBF": "Dortmund Hbf",
    "DE_DRE_HBF": "Dresden Hbf",
    "DE_BRE_HBF": "Bremen Hbf",
    "DE_ESS_HBF": "Essen Hbf",
    "DE_KAR_HBF": "Karlsruhe Hbf",
    "DE_BON_HBF": "Bonn Hbf",
    "DE_REU_HBF": "Reutlingen Hbf",
    "DE_FRL": "Freilassing",
    "DE_DRE_NST": "Dresden-Neustadt",
}


def to_station_id(gtfs_stop_id: str) -> str:
    """Translate a GTFS parent-station stop_id to our station_id.

    Raises ValueError (not a silent pass-through) for anything outside the
    scoped corridor, so an out-of-scope station fails the build instead of
    leaking an unmapped id into Station/Leg/Transfer records.
    """
    try:
        return GTFS_STOP_ID_TO_STATION_ID[gtfs_stop_id]
    except KeyError:
        raise ValueError(
            f"No crosswalk entry for GTFS stop_id {gtfs_stop_id!r}; "
            "add it to GTFS_STOP_ID_TO_STATION_ID"
        ) from None
