import pytest

from app.db import repository as repo
from app.db.connection import connect
from app.ingestion.importer import import_bytes, import_file, import_many
from tests.samples import HTML_BODY, make_eml


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def comp(conn):
    return repo.add_competitor(conn, "concurrent.example")


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_import_eml_stores_edition_and_children(conn, comp, tmp_path):
    r = import_bytes(conn, "a.eml", make_eml(), "competitor", comp, tmp_path)
    assert r.statut == "ok" and r.edition_id
    ed = conn.execute("SELECT * FROM editions").fetchone()
    assert ed["objet"] == "Édition fictive n°1" and ed["date_statut"] == "source"
    assert ed["publication_type"] == "competitor" and ed["publication_id"] == comp
    assert count(conn, "edition_headings") == 2
    assert count(conn, "edition_links") == 2
    assert count(conn, "edition_images") == 2
    assert (tmp_path / f"{ed['hash_contenu']}.eml").exists()


def test_import_html_marks_date_unknown(conn, comp, tmp_path):
    r = import_bytes(conn, "a.html", HTML_BODY.encode(), "competitor", comp, tmp_path)
    assert r.statut == "ok"
    ed = conn.execute("SELECT * FROM editions").fetchone()
    assert ed["date_envoi"] is None and ed["date_statut"] == "inconnue"
    assert ed["expediteur"] is None


def test_same_file_twice_is_duplicate(conn, comp, tmp_path):
    data = make_eml()
    first = import_bytes(conn, "a.eml", data, "competitor", comp, tmp_path)
    second = import_bytes(conn, "a.eml", data, "competitor", comp, tmp_path)
    assert second.statut == "doublon" and second.edition_id == first.edition_id
    assert count(conn, "editions") == 1 and count(conn, "edition_links") == 2


def test_duplicate_by_message_id_even_if_content_differs(conn, comp, tmp_path):
    import_bytes(conn, "a.eml", make_eml(), "competitor", comp, tmp_path)
    r = import_bytes(conn, "b.eml", make_eml(html="<p>autre contenu</p>"), "competitor", comp, tmp_path)
    assert r.statut == "doublon" and count(conn, "editions") == 1


def test_duplicate_by_content_without_message_id(conn, comp, tmp_path):
    import_bytes(conn, "a.eml", make_eml(message_id=None), "competitor", comp, tmp_path)
    r = import_bytes(conn, "b.eml", make_eml(message_id=None, subject="Autre objet"),
                     "competitor", comp, tmp_path)
    assert r.statut == "doublon"


def test_same_content_for_other_publication_is_not_duplicate(conn, comp, tmp_path):
    other = repo.add_competitor(conn, "autre.example")
    import_bytes(conn, "a.eml", make_eml(), "competitor", comp, tmp_path)
    r = import_bytes(conn, "a.eml", make_eml(), "competitor", other, tmp_path)
    assert r.statut == "ok" and count(conn, "editions") == 2


def test_bad_files_do_not_stop_the_batch(conn, comp, tmp_path):
    good = tmp_path / "bon.eml"
    good.write_bytes(make_eml())
    bad_format = tmp_path / "doc.pdf"
    bad_format.write_bytes(b"%PDF")
    missing = tmp_path / "absent.eml"
    results = import_many(conn, [bad_format, missing, good], "competitor", comp, tmp_path / "arch")
    assert [r.statut for r in results] == ["erreur", "erreur", "ok"]
    assert "Format non pris en charge" in results[0].message
    assert count(conn, "editions") == 1
    assert count(conn, "import_log") == 3


def test_unknown_publication_is_reported_as_error(conn, tmp_path):
    r = import_bytes(conn, "a.eml", make_eml(), "competitor", 999, tmp_path)
    assert r.statut == "erreur" and count(conn, "editions") == 0


def test_import_into_own_publication(conn, tmp_path):
    repo.seed_own_publications(conn)
    pub = repo.list_own_publications(conn)[0]
    r = import_bytes(conn, "a.eml", make_eml(), "own", pub["id"], tmp_path)
    assert r.statut == "ok"
    assert conn.execute("SELECT publication_type FROM editions").fetchone()[0] == "own"


def test_import_file_reads_from_disk(conn, comp, tmp_path):
    f = tmp_path / "x.eml"
    f.write_bytes(make_eml())
    assert import_file(conn, f, "competitor", comp, tmp_path / "arch").statut == "ok"
