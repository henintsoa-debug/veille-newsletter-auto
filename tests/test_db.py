import pytest

from app.db import repository as repo
from app.db.connection import connect
from app.db.domains import normalize_domain


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.mark.parametrize("raw, expected", [
    ("Mammam-IA.com", "mammam-ia.com"),
    ("https://www.evenement.fr/newsletter?x=1", "evenement.fr"),
    ("newsletter.lebigdata.fr/", "newsletter.lebigdata.fr"),
])
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "localhost", "a b.com", "http://"])
def test_normalize_domain_invalid(raw):
    with pytest.raises(ValueError):
        normalize_domain(raw)


def test_seed_loads_four_publications_without_inventing_data(conn):
    assert repo.seed_own_publications(conn) == 4
    pubs = repo.list_own_publications(conn)
    assert {p["domaine"] for p in pubs} == set(repo.OWN_PUBLICATION_DOMAINS)
    assert all(p["url_newsletter"] is None and p["thematique"] is None for p in pubs)


def test_seed_is_idempotent_and_keeps_user_edits(conn):
    repo.seed_own_publications(conn)
    pub = repo.list_own_publications(conn)[0]
    repo.update_own_publication(conn, pub["id"], nom="Mon titre")
    assert repo.seed_own_publications(conn) == 0
    assert len(repo.list_own_publications(conn)) == 4
    assert repo.list_own_publications(conn)[0]["nom"] != ""
    assert any(p["nom"] == "Mon titre" for p in repo.list_own_publications(conn))


def test_user_can_add_a_fifth_publication(conn):
    repo.seed_own_publications(conn)
    repo.add_own_publication(conn, "https://www.exemple-nl.fr", thematique="Marketing")
    assert len(repo.list_own_publications(conn)) == 5


def test_add_competitor_unknown_fields_stay_null(conn):
    cid = repo.add_competitor(conn, "https://www.concurrent.com/")
    row = repo.list_competitors(conn)[0]
    assert row["id"] == cid
    assert row["domaine"] == "concurrent.com"
    assert row["nom"] == "concurrent.com"
    assert row["url_newsletter"] is None and row["description"] is None
    assert row["actif"] == 1


def test_duplicate_competitor_rejected(conn):
    repo.add_competitor(conn, "concurrent.com")
    with pytest.raises(ValueError):
        repo.add_competitor(conn, "https://WWW.concurrent.com/page")
    assert len(repo.list_competitors(conn)) == 1


def test_update_is_audited_and_deactivation_keeps_row(conn):
    cid = repo.add_competitor(conn, "concurrent.com", description="v1")
    repo.update_competitor(conn, cid, description="v2", actif=False)
    row = repo.list_competitors(conn)[0]
    assert row["description"] == "v2" and row["actif"] == 0
    assert repo.list_competitors(conn, only_active=True) == []
    changes = {(h["champ"], h["ancienne_valeur"], h["nouvelle_valeur"]) for h in repo.history(conn, "competitors", cid)}
    assert changes == {("description", "v1", "v2"), ("actif", "1", "0")}


def test_update_without_change_writes_no_history(conn):
    cid = repo.add_competitor(conn, "concurrent.com", description="v1")
    repo.update_competitor(conn, cid, description="v1")
    assert repo.history(conn, "competitors", cid) == []


def test_update_rejects_bad_input(conn):
    cid = repo.add_competitor(conn, "concurrent.com")
    with pytest.raises(ValueError):
        repo.update_competitor(conn, cid, domaine="autre.com")
    with pytest.raises(ValueError):
        repo.update_competitor(conn, cid, nom="  ")
    with pytest.raises(LookupError):
        repo.update_competitor(conn, 999, nom="x")


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "t.db"
    c1 = connect(path)
    repo.add_competitor(c1, "concurrent.com")
    c1.close()
    c2 = connect(path)  # réouverture : ne doit rien détruire
    assert len(repo.list_competitors(c2)) == 1
    c2.close()
