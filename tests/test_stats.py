import pytest

from app.advertisers.repository import set_validation
from app.advertisers.stats import advertiser_history, advertiser_stats
from app.db import repository as repo
from app.db.connection import connect
from app.ingestion.importer import import_bytes
from tests.samples import make_eml


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def comp(conn):
    return repo.add_competitor(conn, "concurrent.example", nom="Concurrent")


def sponsor(name, n, label="Sponsorisé par"):
    domain = f"{name.lower()}-example.com"
    return (f"<html><body><p>Édition numéro {n}.</p><table><tr><td><p>{label} {name}</p>"
            f'<a href="https://{domain}/o">Voir</a></td></tr></table></body></html>')


def add(conn, comp, tmp_path, n, date, *names, ptype="competitor", pid=None):
    body = "".join(sponsor(nm, n) for nm in names) if names else f"<html><body>Édition {n}</body></html>"
    if len(names) > 1:  # deux blocs dans une même édition
        body = f"<html><body><p>Édition numéro {n}.</p>" + "".join(
            sponsor(nm, n).split("<body>")[1].split("</body>")[0].split("</p>", 1)[1] for nm in names
        ) + "</body></html>"
    data = make_eml(date=date, message_id=f"<m{n}@x>", html=body)
    r = import_bytes(conn, f"{n}.eml", data, ptype, pid or comp, tmp_path)
    assert r.statut == "ok", r.message


def by_name(stats):
    return {s.nom: s for s in stats}


@pytest.fixture
def populated(conn, comp, tmp_path):
    add(conn, comp, tmp_path, 1, "Mon, 06 Jan 2025 08:00:00 +0000", "Acme")
    add(conn, comp, tmp_path, 2, "Mon, 03 Feb 2025 08:00:00 +0000", "Acme")
    add(conn, comp, tmp_path, 3, "Mon, 03 Mar 2025 08:00:00 +0000", "Acme", "Beta")
    return conn


def test_recurrent_and_new_without_period(populated):
    s = by_name(advertiser_stats(populated))
    assert s["Acme"].nb_editions == 3 and s["Acme"].recurrent
    assert (s["Acme"].premiere_date, s["Acme"].derniere_date) == ("2025-01-06", "2025-03-03")
    assert s["Beta"].nb_editions == 1 and not s["Beta"].recurrent
    assert s["Acme"].publications == ["Concurrent"]


def test_recurrence_threshold_is_configurable(populated):
    s = by_name(advertiser_stats(populated, seuil=4))
    assert not s["Acme"].recurrent
    s = by_name(advertiser_stats(populated, seuil=1))
    assert s["Beta"].recurrent


def test_new_advertiser_in_period(populated):
    s = by_name(advertiser_stats(populated, date_debut="2025-03-01", date_fin="2025-03-31"))
    assert s["Beta"].nouveaute == "nouveau"
    assert s["Acme"].nouveaute == "non_nouveau"  # vu dès janvier : certain
    assert s["Acme"].nb_editions == 1 and not s["Acme"].recurrent  # comptage limité à la période


def test_advertiser_absent_from_period_is_not_listed(populated):
    s = by_name(advertiser_stats(populated, date_debut="2025-02-01", date_fin="2025-02-28"))
    assert set(s) == {"Acme"}


def test_undated_edition_makes_newness_undetermined(conn, comp, tmp_path):
    add(conn, comp, tmp_path, 1, "Mon, 03 Mar 2025 08:00:00 +0000", "Beta")
    r = import_bytes(conn, "x.html", sponsor("Beta", 2).encode(), "competitor", comp, tmp_path)
    assert r.statut == "ok"
    s = by_name(advertiser_stats(conn, date_debut="2025-03-01", date_fin="2025-03-31"))
    assert s["Beta"].editions_sans_date == 1 and s["Beta"].nouveaute == "indetermine"
    assert s["Beta"].nb_editions == 1  # l'édition sans date n'est pas placée dans la période
    assert by_name(advertiser_stats(conn))["Beta"].nb_editions == 2  # mais compte sans filtre


def test_only_undated_editions_is_undetermined(conn, comp, tmp_path):
    import_bytes(conn, "x.html", sponsor("Beta", 1).encode(), "competitor", comp, tmp_path)
    s = by_name(advertiser_stats(conn))
    assert s["Beta"].premiere_date is None and s["Beta"].nouveaute == "indetermine"


def test_rejected_and_cited_detections_are_not_counted(populated, comp, tmp_path):
    for row in populated.execute("SELECT d.id FROM detections d JOIN advertisers a"
                                 " ON a.id = d.advertiser_id WHERE a.nom = 'Beta'").fetchall():
        set_validation(populated, row["id"], "rejetee")
    add(populated, comp, tmp_path, 4, "Mon, 07 Apr 2025 08:00:00 +0000")  # sans pub
    populated.execute("INSERT INTO editions (publication_type, publication_id, date_statut,"
                      " hash_contenu, fichier_source, format_source, date_envoi) VALUES"
                      " ('competitor', ?, 'source', 'h', 'f', 'eml', '2025-05-01T00:00:00+00:00')", (comp,))
    eid = populated.execute("SELECT MAX(id) FROM editions").fetchone()[0]
    acme = populated.execute("SELECT id FROM advertisers WHERE nom = 'Acme'").fetchone()[0]
    populated.execute("INSERT INTO detections (edition_id, advertiser_id, niveau, score)"
                      " VALUES (?, ?, 'citee', 0)", (eid, acme))
    s = by_name(advertiser_stats(populated))
    assert "Beta" not in s
    assert s["Acme"].nb_editions == 3  # la mention du 1er mai ne compte pas


def test_potential_detections_can_be_excluded(conn, comp, tmp_path):
    add(conn, comp, tmp_path, 1, "Mon, 06 Jan 2025 08:00:00 +0000", "Acme")
    data = make_eml(date="Mon, 03 Feb 2025 08:00:00 +0000", message_id="<p@x>",
                    html='<html><body><p>Autre édition.</p><a href="https://shop-example.com/x">'
                         '<img src="b.png" alt="v"></a></body></html>')
    import_bytes(conn, "p.eml", data, "competitor", comp, tmp_path)
    assert "shop-example.com" in by_name(advertiser_stats(conn))
    assert "shop-example.com" not in by_name(advertiser_stats(conn, inclure_potentielles=False))


def test_two_blocks_in_one_edition_count_one_edition(conn, comp, tmp_path):
    add(conn, comp, tmp_path, 1, "Mon, 06 Jan 2025 08:00:00 +0000", "Acme", "Acme")
    s = by_name(advertiser_stats(conn))
    assert s["Acme"].nb_editions == 1 and s["Acme"].nb_detections == 2


def test_publication_filter(populated, conn, comp, tmp_path):
    other = repo.add_competitor(conn, "autre.example", nom="Autre")
    add(conn, other, tmp_path, 9, "Mon, 10 Mar 2025 08:00:00 +0000", "Acme", pid=other)
    all_stats = by_name(advertiser_stats(conn))
    assert all_stats["Acme"].nb_editions == 4 and all_stats["Acme"].publications == ["Autre", "Concurrent"]
    only = by_name(advertiser_stats(conn, publication_type="competitor", publication_id=other))
    assert set(only) == {"Acme"} and only["Acme"].nb_editions == 1


def test_history_is_chronological_and_includes_rejected(populated):
    acme = populated.execute("SELECT id FROM advertisers WHERE nom = 'Acme'").fetchone()[0]
    first = populated.execute("SELECT id FROM detections WHERE advertiser_id = ? ORDER BY id",
                              (acme,)).fetchone()[0]
    set_validation(populated, first, "rejetee")
    h = advertiser_history(populated, acme)
    assert [x["date_envoi"][:10] for x in h] == ["2025-01-06", "2025-02-03", "2025-03-03"]
    assert h[0]["statut_validation"] == "rejetee" and h[0]["publication_nom"] == "Concurrent"
    assert h[0]["type_bloc"] == "bloc_sponsorise" and h[0]["niveau"] == "confirmee"


def test_history_puts_undated_last(conn, comp, tmp_path):
    import_bytes(conn, "x.html", sponsor("Beta", 1).encode(), "competitor", comp, tmp_path)
    add(conn, comp, tmp_path, 2, "Mon, 03 Mar 2025 08:00:00 +0000", "Beta")
    beta = conn.execute("SELECT id FROM advertisers WHERE nom = 'Beta'").fetchone()[0]
    h = advertiser_history(conn, beta)
    assert [x["date_envoi"] is None for x in h] == [False, True]
