import pytest

from app.db import repository as repo
from app.db.connection import connect
from app.ingestion.importer import import_bytes
from app.ui import queries as q
from tests.samples import make_eml


def sponsor(name, n, label="Sponsorisé par"):
    return (f"<html><body><p>Édition {n}.</p><table><tr><td><p>{label} {name}</p>"
            f'<a href="https://{name.lower()}-example.com/o">Voir</a></td></tr></table></body></html>')


@pytest.fixture
def db(tmp_path):
    conn = connect(":memory:")
    repo.seed_own_publications(conn)
    comp = repo.add_competitor(conn, "concurrent.example", nom="Concurrent")
    own = repo.list_own_publications(conn)[0]["id"]

    def add(n, date, name, ptype, pid):
        data = make_eml(date=date, message_id=f"<m{n}@x>", html=sponsor(name, n))
        assert import_bytes(conn, f"{n}.eml", data, ptype, pid, tmp_path).statut == "ok"

    add(1, "Mon, 06 Jan 2025 08:00:00 +0000", "Acme", "competitor", comp)
    add(2, "Mon, 03 Feb 2025 08:00:00 +0000", "Acme", "competitor", comp)
    add(3, "Mon, 03 Mar 2025 08:00:00 +0000", "Beta", "competitor", comp)
    add(4, "Mon, 03 Mar 2025 09:00:00 +0000", "Acme", "own", own)
    add(5, "Mon, 03 Mar 2025 10:00:00 +0000", "Gamma", "own", own)
    import_bytes(conn, "sans_date.html", sponsor("Delta", 6).encode(), "competitor", comp, tmp_path)
    yield conn, comp, own
    conn.close()


def names(rows, key="annonceur"):
    return sorted({r[key] for r in rows})


def test_detections_without_filter(db):
    conn, *_ = db
    assert names(q.detections_table(conn, q.Filters())) == ["Acme", "Beta", "Delta", "Gamma"]


def test_filter_by_publication(db):
    conn, comp, own = db
    f = q.Filters(publication_type="own", publication_id=own)
    assert names(q.detections_table(conn, f)) == ["Acme", "Gamma"]
    f = q.Filters(publication_type="competitor")
    assert names(q.detections_table(conn, f)) == ["Acme", "Beta", "Delta"]


def test_filter_by_period_excludes_unknown_dates_and_reports_them(db):
    conn, *_ = db
    f = q.Filters(date_debut="2025-03-01", date_fin="2025-03-31")
    assert names(q.detections_table(conn, f)) == ["Acme", "Beta", "Gamma"]
    assert q.undated_excluded(conn, f) == 1
    assert q.undated_excluded(conn, q.Filters()) == 0  # sans période, rien n'est exclu


def test_filter_by_advertiser(db):
    conn, *_ = db
    acme = next(a["id"] for a in q.list_advertisers(conn) if a["nom"] == "Acme")
    rows = q.detections_table(conn, q.Filters(advertiser_id=acme))
    assert len(rows) == 3 and names(rows) == ["Acme"]
    eds = q.editions_table(conn, q.Filters(advertiser_id=acme))
    assert len(eds) == 3 and all(e["nb_detections"] == 1 for e in eds)


def test_filter_by_confidence_level(db):
    conn, *_ = db
    assert q.detections_table(conn, q.Filters(niveaux=("citee",))) == []
    assert len(q.detections_table(conn, q.Filters(niveaux=("confirmee",)))) == 6


def test_rejected_hidden_by_default_but_selectable(db):
    from app.advertisers.repository import set_validation
    conn, *_ = db
    row = q.detections_table(conn, q.Filters())[0]
    set_validation(conn, row["detection_id"], "rejetee")
    assert row["detection_id"] not in [r["detection_id"] for r in q.detections_table(conn, q.Filters())]
    shown = q.detections_table(conn, q.Filters(statuts=("a_valider", "validee", "rejetee")))
    assert row["detection_id"] in [r["detection_id"] for r in shown]


def test_editions_table_and_detail(db):
    conn, *_ = db
    eds = q.editions_table(conn, q.Filters())
    assert len(eds) == 6 and eds[-1]["date_envoi"] is None  # sans date en dernier
    d = q.edition_detail(conn, eds[0]["edition_id"])
    assert d["edition"]["publication"] and d["detections"]
    det = d["detections"][0]
    assert det["preuves"] and det["preuves"][0]["extrait"]
    assert q.edition_detail(conn, 9999) is None


def test_pending_validation_prioritises_confirmed_and_excludes_mentions(db):
    conn, *_ = db
    items, total = q.pending_validation(conn, limit=3)
    assert total == 6 and len(items) == 3
    assert all(i["niveau"] != "citee" and i["preuves"] for i in items)


def test_overview_counts(db):
    conn, *_ = db
    o = q.overview(conn, q.Filters())
    assert o["editions"] == 6 and o["editions_sans_date"] == 1
    assert o["annonceurs"] == 4 and o["a_valider"] == 6
    assert sum(m["n"] for m in o["editions_par_mois"]) == 5
    assert q.overview(conn, q.Filters(publication_type="own"))["editions"] == 2


def test_compare_own_vs_competitors(db):
    conn, *_ = db
    c = q.compare_own_vs_competitors(conn, q.Filters())
    assert names(c["communs"]) == ["Acme"]
    assert names(c["concurrents_seulement"]) == ["Beta", "Delta"]
    assert names(c["chez_moi_seulement"]) == ["Gamma"]
    acme = c["communs"][0]
    assert acme["editions_chez_moi"] == 1 and acme["editions_chez_concurrents"] == 2
    assert acme["concurrents"] == ["Concurrent"]
