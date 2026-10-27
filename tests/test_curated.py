import json
from pathlib import Path

import pytest

from app.db import repository as repo
from app.db.connection import connect
from app.discovery import service as disc

CURATED = sorted((Path(__file__).resolve().parents[1] / "config").glob("candidats_*.json"))


@pytest.fixture
def conn():
    c = connect(":memory:")
    repo.seed_own_publications(c)
    yield c
    c.close()


def sample(tmp_path, domains=("a-example.fr", "b-example.fr")):
    data = {"fournisseur": "Recherche test", "date": "2026-10-27", "candidats": [
        {"domaine": d, "nom": f"Nom {d}", "url_newsletter": f"https://{d}/nl", "description": "desc",
         "theme": "Thème test", "signaux": [["theme_identique", "extrait exact", f"https://{d}/nl"]]}
        for d in domains]}
    path = tmp_path / "candidats_test.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_adds_candidates_only_never_competitors(conn, tmp_path):
    assert disc.load_curated(conn, sample(tmp_path)) == (2, 0)
    cands = disc.list_candidates(conn)
    assert {c["domaine"] for c in cands} == {"a-example.fr", "b-example.fr"}
    assert repo.list_competitors(conn) == []
    c = cands[0]
    assert c["mode_decouverte"] == "recherche_manuelle" and c["score"] == 0 and c["statut"] == "propose"
    sig = c["signaux"][0]
    assert sig["extrait"] == "extrait exact" and "2026-10-27" in sig["source"] and sig["poids"] == 0


def test_load_is_idempotent_and_skips_known_domains(conn, tmp_path):
    path = sample(tmp_path, ("a-example.fr", "evenement.fr"))  # evenement.fr = une de vos newsletters
    assert disc.load_curated(conn, path) == (1, 1)
    assert disc.load_curated(conn, path) == (0, 2)
    assert len(disc.list_candidates(conn)) == 1


def test_rejected_candidate_is_not_reloaded(conn, tmp_path):
    path = sample(tmp_path)
    disc.load_curated(conn, path)
    disc.reject_candidate(conn, disc.list_candidates(conn)[0]["id"])
    assert disc.load_curated(conn, path) == (0, 2)


def test_accept_uses_candidate_name_and_traces_source(conn, tmp_path):
    disc.load_curated(conn, sample(tmp_path))
    c = disc.list_candidates(conn)[0]
    disc.accept_candidate(conn, c["id"])
    comp = repo.list_competitors(conn)[0]
    assert comp["nom"] == c["nom"] and comp["url_newsletter"] == c["url_newsletter"]
    assert "Recherche test" in comp["source_info"] and "Thème test" in comp["source_info"]
    assert "accepté par l'utilisateur" in comp["source_info"]


def test_accept_all_continues_after_an_error(conn, tmp_path):
    disc.load_curated(conn, sample(tmp_path))
    repo.add_competitor(conn, "a-example.fr")  # ajouté à la main entre-temps : l'acceptation échouera
    accepted, errors = disc.accept_all(conn)
    assert accepted == 1 and len(errors) == 1 and "a-example.fr" in errors[0]
    assert {r["domaine"] for r in repo.list_competitors(conn)} == {"a-example.fr", "b-example.fr"}


@pytest.mark.skipif(not CURATED, reason="aucun fichier de candidats fourni")
def test_shipped_candidate_files_are_well_formed(conn):
    own = {r["domaine"] for r in repo.list_own_publications(conn)}
    for path in CURATED:
        data = json.loads(path.read_text(encoding="utf-8"))
        domains = [c["domaine"] for c in data["candidats"]]
        assert len(domains) == len(set(domains)) and not (set(domains) & own)
        for c in data["candidats"]:
            assert c["signaux"] and all(len(s) == 3 and all(s) for s in c["signaux"])
            assert c["url_newsletter"].startswith("https://") and c["theme"]
        added, ignored = disc.load_curated(conn, path)
        assert added == len(domains) and ignored == 0
