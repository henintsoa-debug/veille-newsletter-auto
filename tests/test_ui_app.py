"""Tests de l'interface complète avec le simulateur de Streamlit (AppTest)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.db import repository as repo
from app.db.connection import connect
from app.ingestion.importer import import_bytes
from app.ui.app import PAGES
from tests.samples import make_eml

APP = str(Path(__file__).resolve().parents[1] / "app" / "ui" / "app.py")


def sponsor(name, n):
    return (f"<html><body><p>Édition {n}.</p><table><tr><td><p>Sponsorisé par {name}</p>"
            f'<a href="https://{name.lower()}-example.com/o">Voir</a></td></tr></table></body></html>')


@pytest.fixture
def app(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setenv("VEILLE_DB", str(path))
    conn = connect(path)
    repo.seed_own_publications(conn)
    comp = repo.add_competitor(conn, "concurrent.example", nom="Concurrent")
    for n, name in enumerate(["Acme", "Beta"], start=1):
        import_bytes(conn, f"{n}.eml", make_eml(message_id=f"<m{n}@x>", html=sponsor(name, n)),
                     "competitor", comp, tmp_path / "arch")
    conn.close()
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at, path


def go(at, page):
    at.sidebar.radio[0].set_value(page).run()
    return at


def add_candidates(path):
    conn = connect(path)
    for n in (1, 2):
        cur = conn.execute(
            "INSERT INTO competitor_candidates (domaine, nom, url_trouvee, requetes, fournisseur, score)"
            " VALUES (?, ?, ?, 'q1', 'Faux', 80)", (f"cand{n}-example.fr", f"cand{n}-example.fr",
                                                    f"https://cand{n}-example.fr/newsletter"))
        conn.execute("INSERT INTO candidate_signals (candidate_id, regle, poids, extrait, source)"
                     " VALUES (?, 'newsletter_dans_l_url', 20, 'https://x/newsletter', 'URL')", (cur.lastrowid,))
    conn.commit()
    conn.close()


def test_discovery_page_lists_shipped_candidates_and_has_no_api_key_ui(app):
    at, path = app
    go(at, "Découverte")
    assert not at.exception
    conn = connect(path)
    shipped = conn.execute("SELECT COUNT(*) FROM competitor_candidates WHERE statut = 'propose'").fetchone()[0]
    conn.close()
    assert shipped >= 1 and len([b for b in at.button if b.key and b.key.startswith("acc_")]) == shipped
    assert any(b.label.startswith("Tout accepter") for b in at.button)
    assert not any("clé" in w.value.lower() for w in at.warning)


def test_propose_a_site_from_the_page_and_duplicate_warning(app):
    at, path = app
    go(at, "Découverte")
    field = next(t for t in at.text_input if t.label.startswith("Domaine"))
    field.set_value("https://www.nouveau-media-example.fr/")
    next(b for b in at.button if b.label == "Proposer ce site").click().run()
    assert not at.exception
    conn = connect(path)
    assert conn.execute("SELECT statut FROM competitor_candidates WHERE domaine = 'nouveau-media-example.fr'"
                        ).fetchone()[0] == "propose"
    assert "nouveau-media-example.fr" not in [r["domaine"] for r in repo.list_competitors(conn)]
    conn.close()
    field = next(t for t in at.text_input if t.label.startswith("Domaine"))
    field.set_value("nouveau-media-example.fr")
    next(b for b in at.button if b.label == "Proposer ce site").click().run()
    assert any("déjà connu" in w.value for w in at.warning)


def test_accept_candidate_from_the_page(app):
    at, path = app
    add_candidates(path)
    go(at, "Découverte")
    btn = next(b for b in at.button if b.key and b.key.startswith("acc_"))
    cand_id = int(btn.key.split("_")[1])
    btn.click().run()
    assert not at.exception
    conn = connect(path)
    row = conn.execute("SELECT domaine, statut FROM competitor_candidates WHERE id = ?", (cand_id,)).fetchone()
    assert row["statut"] == "accepte"
    assert row["domaine"] in [r["domaine"] for r in repo.list_competitors(conn)]
    conn.close()


def test_reject_then_restore_candidate_from_the_page(app):
    at, path = app
    add_candidates(path)
    go(at, "Découverte")
    btn = next(b for b in at.button if b.key and b.key.startswith("rej_"))
    cand_id = int(btn.key.split("_")[1])
    btn.click().run()
    conn = connect(path)
    assert conn.execute("SELECT statut FROM competitor_candidates WHERE id = ?",
                        (cand_id,)).fetchone()[0] == "rejete"
    assert repo.list_competitors(conn)[0]["domaine"] == "concurrent.example"  # aucun ajout
    conn.close()
    next(b for b in at.button if b.key == f"res_{cand_id}").click().run()
    conn = connect(path)
    assert conn.execute("SELECT statut FROM competitor_candidates WHERE id = ?",
                        (cand_id,)).fetchone()[0] == "propose"
    conn.close()


def test_every_page_renders_without_error(app):
    at, _ = app
    assert not at.exception
    for page in PAGES:
        go(at, page)
        assert not at.exception, f"Erreur sur la page {page}: {[e.value for e in at.exception]}"


def test_overview_metrics(app):
    at, _ = app
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Éditions"] == "2" and metrics["Annonceurs"] == "2"
    assert metrics["À valider"] == "2"


def test_french_labels_and_unknown_fields(app):
    at, _ = app
    assert at.title[0].value == "Veille Newsletter Auto"
    go(at, "Concurrents")
    assert any("Inconnu" in str(df.value.to_dict()) for df in at.dataframe)  # URL newsletter inconnue


def test_validation_button_updates_database_and_history(app):
    at, path = app
    go(at, "À valider")
    buttons = [b for b in at.button if b.key and b.key.startswith("validee_")]
    assert len(buttons) == 2
    det_id = int(buttons[0].key.split("_")[1])
    buttons[0].click().run()
    assert not at.exception
    conn = connect(path)
    assert conn.execute("SELECT statut_validation FROM detections WHERE id = ?",
                        (det_id,)).fetchone()[0] == "validee"
    assert conn.execute("SELECT COUNT(*) FROM audit_history WHERE table_nom = 'detections'").fetchone()[0] == 1
    conn.close()
    assert len([b for b in at.button if b.key and b.key.startswith("validee_")]) == 1


def test_reject_button(app):
    at, path = app
    go(at, "À valider")
    btn = next(b for b in at.button if b.key and b.key.startswith("rejetee_"))
    det_id = int(btn.key.split("_")[1])
    btn.click().run()
    conn = connect(path)
    assert conn.execute("SELECT statut_validation FROM detections WHERE id = ?",
                        (det_id,)).fetchone()[0] == "rejetee"
    conn.close()


def test_period_filter_and_empty_levels_do_not_crash(app):
    at, _ = app
    go(at, "Détections")
    at.sidebar.checkbox[0].check().run()  # Filtrer par période
    assert not at.exception
    at.sidebar.multiselect[0].set_value([]).run()
    assert not at.exception and any("Aucun niveau" in w.value for w in at.sidebar.warning)


def test_add_competitor_from_the_form_and_duplicate_error(app):
    at, path = app
    go(at, "Concurrents")
    # le premier « Domaine » appartient au formulaire « mes newsletters », le second aux concurrents
    domaines = [t for t in at.text_input if t.label.startswith("Domaine")]
    assert len(domaines) == 2
    domaines[1].set_value("https://www.nouveau-concurrent.fr/")
    [b for b in at.button if b.label == "Ajouter"][1].click().run()
    assert not at.exception
    conn = connect(path)
    assert "nouveau-concurrent.fr" in [r["domaine"] for r in repo.list_competitors(conn)]
    conn.close()
    # même domaine une seconde fois : message d'erreur, rien n'est ajouté
    domaines = [t for t in at.text_input if t.label.startswith("Domaine")]
    domaines[1].set_value("nouveau-concurrent.fr")
    [b for b in at.button if b.label == "Ajouter"][1].click().run()
    assert any("existe déjà" in e.value for e in at.error)
    conn = connect(path)
    assert len(repo.list_competitors(conn)) == 2
    conn.close()
