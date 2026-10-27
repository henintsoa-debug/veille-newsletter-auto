import csv
import io

import pytest

from app.db import repository as repo
from app.db.connection import connect
from app.export.csv_export import (export_annonceurs, export_detections, export_editions,
                                   export_preuves, to_csv_bytes)
from app.ingestion.importer import import_bytes
from app.ui import queries as q
from tests.samples import make_eml


def parse(data: bytes) -> list[list[str]]:
    assert data.startswith(b"\xef\xbb\xbf")  # BOM UTF-8 : Excel reconnaît les accents
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=";"))


def test_csv_format_for_excel():
    data = to_csv_bytes({"a": "Nom", "b": "Texte"}, [{"a": "Éditions à venir", "b": "ligne1\nligne2; suite"}])
    assert data.startswith(b"\xef\xbb\xbfNom;Texte\r\n")
    assert parse(data) == [["Nom", "Texte"], ["Éditions à venir", "ligne1\nligne2; suite"]]


def test_unknown_values_written_as_inconnu():
    rows = parse(to_csv_bytes({"a": "A", "b": "B", "c": "C"}, [{"a": None, "b": "", "c": 0}]))
    assert rows[1] == ["Inconnu", "Inconnu", "0"]  # 0 est une vraie valeur, pas un inconnu


@pytest.mark.parametrize("danger", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "=HYPERLINK(\"http://x\")"])
def test_formula_injection_is_neutralised(danger):
    rows = parse(to_csv_bytes({"a": "A"}, [{"a": danger}]))
    assert rows[1][0] == "'" + danger


def test_normal_text_is_untouched():
    assert parse(to_csv_bytes({"a": "A"}, [{"a": "2025-03-01"}, {"a": "https://x.example"}]))[1:] == [
        ["2025-03-01"], ["https://x.example"]]


@pytest.fixture
def db(tmp_path):
    conn = connect(":memory:")
    comp = repo.add_competitor(conn, "concurrent.example", nom="Concurrent; =cmd")
    html = ("<html><body><p>Édition 1.</p><table><tr><td><p>Sponsorisé par Acme</p>"
            "<a href='https://acme-example.com/o?utm_source=nl'>Découvrez</a></td></tr></table></body></html>")
    import_bytes(conn, "a.eml", make_eml(date="Mon, 06 Jan 2025 08:00:00 +0000", subject="=Objet piégé",
                                         message_id="<1@x>", html=html), "competitor", comp, tmp_path)
    import_bytes(conn, "b.html", html.replace("Édition 1", "Édition 2").encode(), "competitor", comp, tmp_path)
    yield conn
    conn.close()


def test_export_detections_content_and_filters(db):
    rows = parse(export_detections(db, q.Filters()))
    assert rows[0][:5] == ["Date", "Publication", "Objet de l'édition", "Annonceur", "Domaine"]
    assert len(rows) == 3
    first = dict(zip(rows[0], rows[1]))
    assert first["Annonceur"] == "Acme" and first["Niveau de confiance"] == "Confirmée"
    assert first["Validation"] == "À valider" and first["Type de bloc"] == "Bloc sponsorisé"
    dates = {dict(zip(rows[0], r))["Date"] for r in rows[1:]}
    assert dates == {"2025-01-06", "Inconnue"}  # la date inconnue n'est pas inventée
    filtered = parse(export_detections(db, q.Filters(date_debut="2025-01-01", date_fin="2025-01-31")))
    assert len(filtered) == 2  # l'édition sans date est exclue quand une période est demandée


def test_export_neutralises_dangerous_newsletter_content(db):
    rows = parse(export_detections(db, q.Filters()))
    objets = {dict(zip(rows[0], r))["Objet de l'édition"] for r in rows[1:]}
    assert "'=Objet piégé" in objets
    pubs = {dict(zip(rows[0], r))["Publication"] for r in rows[1:]}
    assert pubs == {"Concurrent; =cmd"}  # contient « ; » mais ne commence pas par « = »


def test_export_proofs_are_exact_source_excerpts(db):
    rows = parse(export_preuves(db, q.Filters()))
    header = rows[0]
    assert "Extrait exact de la source" in header and len(rows) > 1
    records = [dict(zip(header, r)) for r in rows[1:]]
    forts = [r for r in records if r["Force"] == "Fort"]
    assert forts and all(r["Extrait exact de la source"] == "Sponsorisé par Acme" for r in forts)
    assert all(r["Poids"].isdigit() for r in records)


def test_export_editions_keeps_sources(db):
    rows = parse(export_editions(db, q.Filters()))
    records = {r[rows[0].index("Fichier importé")]: dict(zip(rows[0], r)) for r in rows[1:]}
    assert records["a.eml"]["Source de la date"] == "en-tête Date"
    assert records["b.html"]["Source de la date"] == "Inconnu"
    assert records["b.html"]["Expéditeur"] == "Inconnu"


def test_export_advertisers(db):
    rows = parse(export_annonceurs(db, q.Filters()))
    rec = dict(zip(rows[0], rows[1]))
    assert rec["Annonceur"] == "Acme" and rec["Éditions"] == "2" and rec["Récurrent"] == "Non"
    assert rec["Éditions sans date"] == "1" and rec["Nouveauté"] == "Indéterminé"


def test_empty_database_exports_only_headers():
    conn = connect(":memory:")
    for fn in (export_detections, export_preuves, export_editions, export_annonceurs):
        assert len(parse(fn(conn, q.Filters()))) == 1
    conn.close()
