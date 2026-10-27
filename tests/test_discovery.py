import pytest

from app.db import repository as repo
from app.db.connection import connect
from app.discovery import list_import
from app.discovery import service as disc
from app.discovery.list_import import import_candidates
from app.discovery.service import KnownDomainError


@pytest.fixture
def conn():
    c = connect(":memory:")
    repo.seed_own_publications(c)
    yield c
    c.close()


def domains(conn, statut="propose"):
    return [c["domaine"] for c in disc.list_candidates(conn, statut)]


# --- ajout d'un candidat -------------------------------------------------------------------

def test_add_candidate_keeps_unknown_fields_unknown(conn):
    cid = disc.add_candidate(conn, "https://www.Exemple-NL.fr/accueil")
    (c,) = disc.list_candidates(conn)
    assert c["id"] == cid and c["domaine"] == "exemple-nl.fr" and c["nom"] == "exemple-nl.fr"
    assert c["description"] is None and c["url_newsletter"] is None
    assert c["statut"] == "propose" and c["score"] == 0 and c["mode_decouverte"] == "recherche_manuelle"
    assert c["fournisseur"] == "Saisie utilisateur" and c["requetes"] == "Thème non précisé"


def test_add_candidate_never_creates_a_competitor(conn):
    disc.add_candidate(conn, "exemple-nl.fr")
    assert repo.list_competitors(conn) == []


def test_add_candidate_cleans_fields(conn):
    disc.add_candidate(conn, "exemple-nl.fr", nom="  Mon média ", url_newsletter="ftp://x.fr/nl",
                       description="d" * 500, theme=" IA ")
    c = disc.list_candidates(conn)[0]
    assert c["nom"] == "Mon média" and c["url_newsletter"] is None  # URL non http : inconnue
    assert len(c["description"]) == 300 and c["requetes"] == "IA"
    assert c["url_trouvee"] == "https://exemple-nl.fr"


def test_add_candidate_rejects_invalid_and_known_domains(conn):
    with pytest.raises(ValueError):
        disc.add_candidate(conn, "pas un domaine")
    with pytest.raises(KnownDomainError):
        disc.add_candidate(conn, "https://www.evenement.fr/x")  # une de vos newsletters
    repo.add_competitor(conn, "concurrent-example.fr")
    with pytest.raises(KnownDomainError):
        disc.add_candidate(conn, "concurrent-example.fr")
    disc.add_candidate(conn, "nouveau-example.fr")
    with pytest.raises(KnownDomainError):
        disc.add_candidate(conn, "NOUVEAU-example.fr")
    assert domains(conn) == ["nouveau-example.fr"]


def test_hosted_platforms_are_compared_by_full_host(conn):
    disc.add_candidate(conn, "a.substack.com")
    disc.add_candidate(conn, "b.substack.com")  # autre newsletter sur la même plateforme
    with pytest.raises(KnownDomainError):
        disc.add_candidate(conn, "https://a.substack.com/p/x")
    assert domains(conn) == ["a.substack.com", "b.substack.com"]


# --- acceptation, rejet ----------------------------------------------------------------------

def test_accept_creates_traced_competitor(conn):
    disc.add_candidate(conn, "exemple-nl.fr", nom="Mon média", url_newsletter="https://exemple-nl.fr/nl",
                       description="desc", theme="IA")
    c = disc.list_candidates(conn)[0]
    cid = disc.accept_candidate(conn, c["id"])
    comp = repo.list_competitors(conn)[0]
    assert comp["id"] == cid and comp["nom"] == "Mon média" and comp["description"] == "desc"
    assert comp["url_newsletter"] == "https://exemple-nl.fr/nl"
    assert "Saisie utilisateur" in comp["source_info"] and "accepté par l'utilisateur" in comp["source_info"]
    done = disc.list_candidates(conn, "accepte")[0]
    assert done["competitor_id"] == cid and done["decide_le"] and domains(conn) == []
    hist = conn.execute("SELECT ancienne_valeur, nouvelle_valeur FROM audit_history"
                        " WHERE table_nom = 'competitor_candidates'").fetchall()
    assert [(h[0], h[1]) for h in hist] == [("propose", "accepte")]
    with pytest.raises(ValueError):
        disc.accept_candidate(conn, c["id"])


def test_accept_fails_cleanly_if_domain_became_a_competitor(conn):
    disc.add_candidate(conn, "exemple-nl.fr")
    c = disc.list_candidates(conn)[0]
    repo.add_competitor(conn, "exemple-nl.fr")  # ajouté à la main entre-temps
    with pytest.raises(ValueError):
        disc.accept_candidate(conn, c["id"])
    assert domains(conn) == ["exemple-nl.fr"]  # reste proposé


def test_reject_restore_and_unknown_candidate(conn):
    disc.add_candidate(conn, "exemple-nl.fr")
    c = disc.list_candidates(conn)[0]
    disc.reject_candidate(conn, c["id"])
    assert domains(conn) == [] and domains(conn, "rejete") == ["exemple-nl.fr"]
    with pytest.raises(ValueError):
        disc.accept_candidate(conn, c["id"])  # un rejeté ne s'accepte pas sans être remis en file
    with pytest.raises(KnownDomainError):
        disc.add_candidate(conn, "exemple-nl.fr")  # et n'est pas reproposé
    disc.restore_candidate(conn, c["id"])
    assert domains(conn) == ["exemple-nl.fr"]
    with pytest.raises(LookupError):
        disc.reject_candidate(conn, 999)


# --- import de liste ----------------------------------------------------------------------------

def csv_bytes(text, encoding="utf-8-sig"):
    return text.encode(encoding)


def test_import_csv_semicolon_with_accented_headers(conn):
    data = csv_bytes("Domaine;Nom;URL newsletter;Description;Thématique\r\n"
                     "https://www.alpha-example.fr/;Alpha;https://alpha-example.fr/nl;Une description;IA\r\n"
                     "beta-example.fr;;;;\r\n")
    r = import_candidates(conn, "liste.csv", data)
    assert (r.ajoutes, r.deja_connus, r.erreurs) == (2, 0, 0)
    alpha, beta = disc.list_candidates(conn)
    assert (alpha["domaine"], alpha["nom"], alpha["url_newsletter"], alpha["requetes"]) == (
        "alpha-example.fr", "Alpha", "https://alpha-example.fr/nl", "IA")
    assert beta["nom"] == "beta-example.fr" and beta["url_newsletter"] is None and beta["description"] is None
    assert beta["requetes"] == "Liste importée par l'utilisateur" and beta["fournisseur"] == "Import : liste.csv"
    assert alpha["signaux"][0]["extrait"].startswith("Ligne 2 : ") and alpha["signaux"][0]["source"] == "liste.csv"
    assert repo.list_competitors(conn) == []  # jamais d'ajout direct


def test_import_csv_comma_and_ansi_encoding(conn):
    data = csv_bytes("domaine,nom,description\nalpha-example.fr,Média événementiel,Événements\n", "cp1252")
    r = import_candidates(conn, "ansi.csv", data)
    assert r.ajoutes == 1
    c = disc.list_candidates(conn)[0]
    assert c["nom"] == "Média événementiel" and c["description"] == "Événements"


def test_import_txt_ignores_blank_lines_and_comments(conn):
    data = b"# ma liste\nalpha-example.fr\n\n   \nhttps://www.beta-example.fr/nl\n#ignoree.fr\n"
    r = import_candidates(conn, "liste.txt", data)
    assert [(l.ligne, l.domaine, l.statut) for l in r.lignes] == [
        (2, "alpha-example.fr", "ajoute"), (5, "https://www.beta-example.fr/nl", "ajoute")]
    assert domains(conn) == ["alpha-example.fr", "beta-example.fr"]


def test_import_reports_each_line_and_continues_after_errors(conn):
    repo.add_competitor(conn, "connu-example.fr")
    data = csv_bytes("domaine;nom\nalpha-example.fr;\nPAS UN DOMAINE;\nconnu-example.fr;\nevenement.fr;\n"
                     "alpha-example.fr;\n\n;Sans domaine\nbeta-example.fr;\n")
    r = import_candidates(conn, "mix.csv", data)
    status = [(l.ligne, l.statut) for l in r.lignes]
    assert status == [(2, "ajoute"), (3, "erreur"), (4, "deja_connu"), (5, "deja_connu"),
                      (6, "deja_connu"), (8, "erreur"), (9, "ajoute")]
    assert (r.ajoutes, r.deja_connus, r.erreurs) == (2, 3, 2)
    assert domains(conn) == ["alpha-example.fr", "beta-example.fr"]
    assert "obligatoire" in r.lignes[5].message.lower()  # ligne 8 : domaine vide


def test_import_rejected_domain_is_not_reproposed(conn):
    import_candidates(conn, "a.txt", b"alpha-example.fr\n")
    disc.reject_candidate(conn, disc.list_candidates(conn)[0]["id"])
    r = import_candidates(conn, "b.txt", b"alpha-example.fr\n")
    assert r.deja_connus == 1 and domains(conn) == []


@pytest.mark.parametrize("name, data, expected", [
    ("vide.csv", b"", "vide"),
    ("sans_domaine.csv", b"nom;theme\nA;IA\n", "Colonne de domaine introuvable"),
    ("liste.xlsx", b"PK", "Format non pris en charge"),
    ("sans_extension", b"x", "Format non pris en charge"),
])
def test_import_file_level_problems_are_reported_not_raised(conn, name, data, expected):
    r = import_candidates(conn, name, data)
    assert r.ajoutes == 0 and r.erreurs == 1 and expected.lower() in r.lignes[0].message.lower()
    assert domains(conn) == []


def test_import_size_and_row_limits(conn, monkeypatch):
    monkeypatch.setattr(list_import, "MAX_BYTES", 10)
    assert "volumineux" in import_candidates(conn, "a.txt", b"x" * 11).lignes[0].message
    monkeypatch.setattr(list_import, "MAX_BYTES", 2_000_000)
    monkeypatch.setattr(list_import, "MAX_ROWS", 2)
    r = import_candidates(conn, "a.txt", b"a-example.fr\nb-example.fr\nc-example.fr\n")
    assert r.ajoutes == 0 and "Trop de lignes" in r.lignes[0].message and domains(conn) == []


def test_template_is_a_valid_empty_import(conn):
    assert list_import.TEMPLATE_CSV.startswith(b"\xef\xbb\xbfdomaine;nom;url_newsletter;")
    r = import_candidates(conn, "modele.csv", list_import.TEMPLATE_CSV)
    assert r.lignes == [] and domains(conn) == []  # en-tête seul : rien à importer, aucune erreur


def test_import_then_accept_all_with_one_failure(conn):
    import_candidates(conn, "l.txt", b"alpha-example.fr\nbeta-example.fr\n")
    repo.add_competitor(conn, "alpha-example.fr")  # ajouté à la main entre-temps
    accepted, errors = disc.accept_all(conn)
    assert accepted == 1 and len(errors) == 1 and "alpha-example.fr" in errors[0]
    assert {r["domaine"] for r in repo.list_competitors(conn)} == {"alpha-example.fr", "beta-example.fr"}


def test_brave_search_code_is_gone():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert not (root / "app" / "discovery" / "providers.py").exists()
    assert not (root / ".env.example").exists()
    assert "requests" not in (root / "requirements.txt").read_text()
