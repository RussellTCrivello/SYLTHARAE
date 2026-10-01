"""Include detector-visible alias notes in the gazetteer fingerprint.

Homograph notes are copied into place-signal evidence by the detector. Earlier
fingerprints omitted that field, so editing a note could leave the detector
cache and stored place runs looking current even though their emitted evidence
had changed. The service now fingerprints notes, and this migration appends a
revision for databases whose stored fingerprint used the old field set.

Fresh installs already compute the expanded fingerprint in m0019 and need no
extra row here. Existing history remains immutable; a new row anchors the
updated detector version to the same reviewed seed digest.
"""

version = "0034"
name = "gazetteer_note_fingerprint"


def upgrade(conn) -> None:
    from services.geo.gazetteer import compute_fingerprint, current_load

    with conn.cursor() as cur:
        latest = current_load(cur)
        if latest is None:
            raise RuntimeError("migration 0019 must load the Gazetteer before 0034")
        fingerprint, place_count, name_count = compute_fingerprint(cur)
        if latest["fingerprint"].strip() == fingerprint:
            return
        stats = {
            "kind": "fingerprint_algorithm_upgrade",
            "reason": "detector-visible alias notes are included in the fingerprint",
            "previous_fingerprint": latest["fingerprint"].strip(),
            "places_active": place_count,
            "names_active": name_count,
        }
        from psycopg2.extras import Json
        cur.execute(
            "INSERT INTO geo_gazetteer_loads (seed_version, content_sha256, fingerprint,"
            " place_count, name_count, stats, loaded_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'migration:0034')",
            (latest["seed_version"], latest["content_sha256"], fingerprint,
             place_count, name_count, Json(stats)),
        )


def downgrade(conn) -> None:
    """Keep the appended detector revision as immutable provenance.

    This migration changes fingerprint semantics, not schema. The history row
    remains even during a code rollback; deleting it would make the detector
    revision timeline untruthful. The earlier migration owns table teardown.
    """
    return None
