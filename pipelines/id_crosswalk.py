"""GTFS stop_id <-> station_id crosswalk for the scoped corridor.

Per DATA_SPEC.md §2 and §8 step 4. Corridor scope, the split-station
many-to-one mappings, and the connector-station reasoning: DATA_SPEC.md §9.1.

Note the mapping is many-to-one by design -- several logical stations have
two GTFS parent nodes -- so callers must go through to_station_id() rather
than assuming a 1:1 stop_id/station_id relationship.
"""

# IDs re-keyed 2026-10-02: the 2026-09-26 GTFS.DE feeds renumbered every stop_id
# (matched by station name + coordinates against the new stops.txt parents).
# Visit counts in the comments below predate that and are indicative only.
GTFS_STOP_ID_TO_STATION_ID: dict[str, str] = {
    # --- original 11-station corridor (DATA_SPEC.md §9.1) ---
    "343024": "DE_FRA_HBF",  # Frankfurt (Main) Hauptbahnhof
    "30764": "DE_FRA_HBF",  # Frankfurt (Main) Hauptbahnhof tief (S-Bahn tunnel level)
    "2678": "DE_KOL_HBF",  # Koeln Hbf
    "458291": "DE_STG_HBF",  # Stuttgart Hbf -- "Hauptbahnhof (oben)" (elevated tracks)
    "604648": "DE_STG_HBF",  # Stuttgart Hbf (tief) -- underground S-Bahn/regional level
    "508911": "DE_MAN_HBF",  # Mannheim, Hauptbahnhof
    "123740": "DE_HEI_HBF",  # Heidelberg, Hauptbahnhof
    "39355": "DE_MUC_HBF",  # Muenchen Hbf (long-distance/regional surface node)
    "259093": "DE_MUC_HBF",  # Muenchen Hbf -- S-Bahn tunnel level ("Hauptbahnhof (U, Tram)")
    "570977": "DE_NUE_HBF",  # Nuernberg Hbf
    "185848": "DE_LEI_HBF",  # Leipzig Hbf
    "187869": "DE_LEI_HBF",  # Leipzig Hbf (tief) -- City-Tunnel level
    "462566": "DE_BER_HBF",  # "S+U Berlin Hauptbahnhof" -- DELFI's node name for Berlin Hbf
    "360857": "DE_MUC_MAR",  # Marienplatz (Muenchen)
    "440508": "DE_MUC_OST",  # Ostbahnhof (Muenchen) -- i.e. Muenchen Ost
    # --- "Golden 35" expansion: hubs/routing stations ---
    "620981": "DE_ERF_HBF",  # Erfurt, Hauptbahnhof (dominant node, 1,146 visits)
    "466504": "DE_ERF_HBF",  # Erfurt Hbf (minor secondary node, 34 visits, rv only)
    "121402": "DE_HAL_HBF",  # Halle(Saale)Hbf
    "321266": "DE_KAS_WIL",  # Kassel Bahnhof Wilhelmshoehe
    "556318": "DE_KAS_WIL",  # Kassel Bahnhof Wilhelmshoehe, Bereich Gleis 7/8
    "434131": "DE_WUE_HBF",  # Wuerzburg Hbf
    "160713": "DE_HAN_HBF",  # Hannover Hauptbahnhof
    # --- major/regional endpoints ---
    "333381": "DE_HAM_HBF",  # Hamburg, Hamburg Hbf (long-distance node)
    "634389": "DE_HAM_HBF",  # Hamburg, HBF/Kirchenallee -- the S-Bahn node (~4,686 visits)
    "567829": "DE_TUE_HBF",  # Tuebingen Hauptbahnhof
    "2197": "DE_BGD_HBF",  # Berchtesgaden Hbf
    # --- satellite/relief stations ---
    "326584": "DE_BER_SKZ",  # S Suedkreuz Bhf (Berlin)
    "69614": "DE_BER_SPD",  # S Spandau Bhf (Berlin)
    "166210": "DE_KOL_MSD",  # Koeln Messe/Deutz Bf
    "199359": "DE_MUC_PAS",  # Pasing (Muenchen Pasing)
    # --- additional major ICE stops ---
    "126321": "DE_DUS_HBF",  # Duesseldorf Hbf
    "64583": "DE_DOR_HBF",  # Dortmund Hbf
    "641615": "DE_DRE_HBF",  # Dresden Hauptbahnhof
    "451591": "DE_BRE_HBF",  # Bremen Hbf
    "351643": "DE_ESS_HBF",  # Essen Hbf
    "101172": "DE_KAR_HBF",  # Karlsruhe Hauptbahnhof
    "505281": "DE_BON_HBF",  # Bonn Hbf
    # --- connector stations (unlock a previously "one hop away" station) ---
    "366614": "DE_REU_HBF",  # Reutlingen Hauptbahnhof -- unlocks Tuebingen Hbf
    "194471": "DE_FRL",  # Freilassing -- unlocks Berchtesgaden Hbf
    "234042": "DE_DRE_NST",  # Dresden Bahnhof Neustadt -- unlocks Dresden Hbf
}

# Canonical display names for build_real_dataset()'s Station objects. The
# original 11 match mock_data.json's names exactly (kept stable rather than
# switching to the messier real feed names, same reasoning as line_id's
# short-name-over-raw-id choice in gtfs_ingest.py); the 22 new ones use a
# clean canonical form since there's no Phase 1 precedent to match.
STATION_NAMES: dict[str, str] = {
    "DE_FRA_HBF": "Frankfurt(Main) Hbf",
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
