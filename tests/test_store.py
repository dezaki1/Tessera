from ehds.store import RECORD_TABLES


def test_opted_out_patients_are_removed(store, opted_out_ids):
    assert len(opted_out_ids) == 839
    assert store.opted_out_patients == 839
    assert store.eligible_patients == 20000 - 839
    cur = store.cursor()
    for table in ("patients", *RECORD_TABLES):
        ids = {r[0] for r in cur.execute(f"SELECT DISTINCT patient_id FROM {table}").fetchall()}
        assert not ids & opted_out_ids, table


def test_raw_tables_are_gone(store):
    names = {r[0] for r in store.cursor().execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert not any(n.startswith("raw_") for n in names)


def test_age_uses_reference_year(store):
    row = store.cursor().execute(
        "SELECT count(*) FROM patients WHERE age <> 2025 - year(birth_date)"
    ).fetchone()
    assert row[0] == 0


def test_vocabulary(store):
    v = store.vocabulary
    assert len(v.regions) == 9 and "Wien" in v.regions
    assert v.data_holders == ("Klinikum Nord", "Klinikum Süd", "Klinikum West")
    assert "4548-4" in v.loinc and "E11.9" in v.icd10 and "A10BK03" in v.atc
    assert "S72.0" in v.primary_icd10 and "S72.0" not in v.icd10
