"""GTFS stop_id <-> station_id crosswalk for the scoped corridor.

Per DATA_SPEC.md Section 2 (pipelines/id_crosswalk.py) and Section 8 step 4.

Phase 2 v1: a hardcoded dict for the station set DATA_SPEC.md §7.1 scopes
to -- the same 11 stations already in mock_data.json (Frankfurt-Koeln-
Stuttgart-Mannheim-Heidelberg-Muenchen-Nuernberg-Leipzig-Berlin, plus
Munich's S-Bahn).

These are the REAL GTFS.DE stop_ids (parent stations, location_type=1),
looked up directly against the downloaded fv_free/rv_free feeds on
2026-08-23 -- not fabricated. Several logical stations map to more than one
real GTFS node, because DELFI models split/multi-level stations as separate
top-level parents rather than one node with sub-areas:
  - Frankfurt Hbf: the surface station plus its S-Bahn tunnel level ("...tief")
  - Stuttgart Hbf: the elevated long-distance/regional tracks ("(oben)")
    plus the underground S-Bahn/regional level ("(tief)")
  - Leipzig Hbf: the surface station plus its City-Tunnel level ("(tief)")
  - Berlin Hbf: DELFI's parent node is named "S+U Berlin Hauptbahnhof"
    (it's a combined S-Bahn/U-Bahn/long-distance hub), not "Berlin Hbf"
  - Muenchen Hbf: the long-distance node ("Muenchen Hbf") plus its S-Bahn
    tunnel level, which is a *separate* top-level node oddly named
    "Hauptbahnhof (U, Tram)" with no "Muenchen" in its own name at all --
    found by tracing which parent stop_id S-Bahn trips (S1-S8) actually use
    at Muenchen Hbf, not by name search
Real station names in the feed are otherwise inconsistent (comma-separated
"Mannheim, Hauptbahnhof" style, or bare "Hauptbahnhof (oben)" with no city
name at all) -- these were resolved by cross-checking each stop_id's
coordinates and parent/child relationships in stops.txt, not by name match
alone. This means to_station_id() is legitimately many-to-one: several raw
keys can share one station_id value.

KNOWN GAP: even with the S-Bahn tunnel node included, Muenchen Hbf and
Muenchen Marienplatz are not GTFS-adjacent -- a real S-Bahn trip's sequence
is "...Hbf (tief) -> Muenchen Karlsplatz -> Muenchen Marienplatz...", with
Karlsplatz as a genuine intermediate stop. mock_data.json's direct 5-minute
Hbf->Marienplatz Leg is a v1 simplification with no single-hop real
equivalent; gtfs_ingest.py's one-leg-per-consecutive-stop-pair model (per
DATA_SPEC.md §3 step 5) means Marienplatz/Muenchen Ost may end up with zero
real legs in a corridor-scoped build unless Karlsplatz is added as its own
station or legs are allowed to span more than one physical hop -- both are
open scope questions, not bugs, and neither is implemented here.
"""

GTFS_STOP_ID_TO_STATION_ID: dict[str, str] = {
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
