import pytest

from app.advertisers.repository import set_validation
from app.db import repository as repo
from app.db.connection import connect
from app.detection.detector import detect
from app.detection.rules import load_rules
from app.ingestion import importer
from app.parsing.newsletter import parse_html
from tests.samples import make_eml

RULES = load_rules()
IGNORED = {"moi-example.fr"}


def run(html: str, ignored=IGNORED):
    return detect(parse_html(html.encode()), ignored, RULES)


def page(body: str) -> str:
    return f"<html><head><title>t</title></head><body><p>Intro éditoriale.</p>{body}</body></html>"


SPONSOR = ("<table><tr><td><p>Sponsorisé par Acme</p>"
           '<a href="https://www.acme-example.com/offre?utm_source=nl">Découvrez notre offre</a>'
           "</td></tr></table>")


# --- détection pure ---------------------------------------------------------

def test_strong_label_gives_confirmed_with_explainable_evidence():
    (b,) = run(page(SPONSOR))
    assert b.type == "bloc_sponsorise" and b.niveau == "confirmee" and b.score == 80
    assert {(s.regle, s.force) for s in b.signals} == {
        ("libellé « sponsoris[ée]e?\\s+par »", "fort"),
        ("lien_avec_suivi", "faible"), ("appel_a_action", "faible")}
    label = next(s for s in b.signals if s.force == "fort")
    assert label.extrait == "Sponsorisé par Acme"
    (adv,) = b.annonceurs
    assert (adv.nom, adv.nom_source, adv.domaine) == ("Acme", "libellé du bloc", "acme-example.com")


def test_editorial_text_about_advertising_is_not_an_ad():
    html = page('<p>La publicité en ligne évolue. Voir <a href="https://autre-example.fr/x">'
                "cet article</a> sur la publicité.</p><h2>Publicité en ligne : ce qui change</h2>")
    assert run(html) == []


def test_brand_cited_in_article_is_not_an_ad():
    assert run(page('<p>Acme lance un produit, <a href="https://acme-example.com/">source</a>.</p>')) == []


def test_medium_label_alone_is_only_potential():
    html = page('<table><tr><td><p>Partenaire</p><a href="https://p-example.com/">Notre site</a>'
                "</td></tr></table>")
    (b,) = run(html)
    assert b.niveau == "potentielle" and b.score == 25
    assert b.annonceurs[0].nom == "p-example.com" and b.annonceurs[0].nom_source == "domaine du lien"


def test_confirmed_requires_a_strong_signal():
    html = page('<table><tr><td><p>Partenaire</p><a href="https://p-example.com/?utm_medium=x">'
                '<img src="b.png" alt="visuel"></a></td></tr></table>')
    (b,) = run(html)
    assert b.score == 60 and b.niveau == "potentielle"  # 25 + 25 + 10, aucun signal fort


def test_banner_outside_labelled_block():
    html = page('<a href="https://shop-example.com/x"><img src="b.png" alt="visuel"></a>')
    (b,) = run(html)
    assert b.type == "banniere" and b.niveau == "potentielle"
    assert b.signals[0].regle == "banniere_cliquable" and b.signals[0].extrait == "https://shop-example.com/x"


def test_commercial_link_needs_tracking_and_call_to_action():
    both = page('<p>Texte <a href="https://shop-example.com/?utm_source=nl">Profitez de -20% maintenant</a></p>')
    (b,) = run(both)
    assert b.type == "lien_commercial" and b.niveau == "potentielle" and b.score == 20
    cta_only = page('<p>Texte <a href="https://shop-example.com/">Profitez de -20% maintenant</a></p>')
    tracking_only = page('<p>Texte <a href="https://shop-example.com/?utm_source=nl">notre source</a></p>')
    assert run(cta_only) == [] and run(tracking_only) == []


def test_own_and_ignored_domains_are_never_advertisers():
    html = page('<table><tr><td><p>Publicité</p><a href="https://www.moi-example.fr/promo">Découvrez</a>'
                '<a href="https://www.linkedin.com/company/x">Suivez-nous</a></td></tr></table>')
    (b,) = run(html)
    assert b.niveau == "confirmee" and b.annonceurs == []  # bloc visible, annonceur inconnu


def test_label_with_name_but_no_link_keeps_the_name_only():
    (b,) = run(page("<table><tr><td><p>Publicité : Acme</p><p>Texte promo.</p></td></tr></table>"))
    (adv,) = b.annonceurs
    assert adv.nom == "Acme" and adv.domaine is None


def test_no_html_means_no_detection():
    from app.parsing.newsletter import ParsedEdition
    assert detect(ParsedEdition(format_source="eml", texte="Sponsorisé"), set(), RULES) == []


# --- stockage, preuves, annonceurs, mentions, validation --------------------

@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def comp(conn):
    return repo.add_competitor(conn, "concurrent.example")


def do_import(conn, comp, tmp_path, html, n):
    return importer.import_bytes(conn, f"{n}.eml", make_eml(message_id=f"<m{n}@x>", html=html),
                                 "competitor", comp, tmp_path)


def rows(conn, sql):
    return conn.execute(sql).fetchall()


def test_import_stores_blocks_detections_and_evidence(conn, comp, tmp_path):
    r = do_import(conn, comp, tmp_path, page(SPONSOR), 1)
    assert r.statut == "ok" and "1 bloc(s)" in r.message
    (d,) = rows(conn, "SELECT * FROM detections")
    assert d["niveau"] == "confirmee" and d["statut_validation"] == "a_valider"
    ev = rows(conn, "SELECT * FROM evidences")
    assert {e["force"]: e["poids"] for e in ev} == {"fort": 60, "faible": 10}
    edition = rows(conn, "SELECT texte FROM editions")[0]["texte"]
    html = page(SPONSOR)
    assert all(e["extrait"] in edition or e["extrait"] in html for e in ev)  # preuves = source exacte
    assert rows(conn, "SELECT nom, domaine FROM advertisers")[0]["domaine"] == "acme-example.com"


def test_advertiser_is_reused_and_name_upgraded(conn, comp, tmp_path):
    first = page('<table><tr><td><p>Partenaire</p><a href="https://www.acme-example.com/">Notre site</a>'
                 "</td></tr></table>")
    do_import(conn, comp, tmp_path, first, 1)
    assert rows(conn, "SELECT nom FROM advertisers")[0]["nom"] == "acme-example.com"
    do_import(conn, comp, tmp_path, page(SPONSOR), 2)
    advs = rows(conn, "SELECT nom, nom_source FROM advertisers")
    assert len(advs) == 1 and advs[0]["nom"] == "Acme" and advs[0]["nom_source"] == "libellé du bloc"
    assert len(rows(conn, "SELECT * FROM detections")) == 2
    assert rows(conn, "SELECT ancienne_valeur, nouvelle_valeur FROM audit_history"
                      " WHERE table_nom = 'advertisers'")[0]["ancienne_valeur"] == "acme-example.com"


def test_known_advertiser_merely_cited_is_level_cited_not_ad(conn, comp, tmp_path):
    do_import(conn, comp, tmp_path, page(SPONSOR), 1)
    do_import(conn, comp, tmp_path, page("<p>Acme annonce une levée de fonds cette semaine.</p>"), 2)
    cited = rows(conn, "SELECT * FROM detections WHERE niveau = 'citee'")
    assert len(cited) == 1 and cited[0]["score"] == 0 and cited[0]["ad_block_id"] is None
    ev = rows(conn, f"SELECT * FROM evidences WHERE detection_id = {cited[0]['id']}")[0]
    assert ev["force"] == "aucun" and "Acme" in ev["extrait"]
    assert len(rows(conn, "SELECT * FROM ad_blocks")) == 1  # la mention n'a créé aucun bloc pub


def test_rejected_advertiser_is_not_cited_again(conn, comp, tmp_path):
    do_import(conn, comp, tmp_path, page(SPONSOR), 1)
    did = rows(conn, "SELECT id FROM detections")[0]["id"]
    set_validation(conn, did, "rejetee")
    do_import(conn, comp, tmp_path, page("<p>Acme annonce une levée de fonds.</p>"), 2)
    assert rows(conn, "SELECT * FROM detections WHERE niveau = 'citee'") == []


def test_validation_is_audited_and_keeps_computed_level(conn, comp, tmp_path):
    do_import(conn, comp, tmp_path, page(SPONSOR), 1)
    did = rows(conn, "SELECT id FROM detections")[0]["id"]
    set_validation(conn, did, "validee")
    d = rows(conn, "SELECT * FROM detections")[0]
    assert d["statut_validation"] == "validee" and d["valide_le"] and d["niveau"] == "confirmee"
    h = rows(conn, "SELECT * FROM audit_history WHERE table_nom = 'detections'")
    assert [(x["ancienne_valeur"], x["nouvelle_valeur"]) for x in h] == [("a_valider", "validee")]
    with pytest.raises(ValueError):
        set_validation(conn, did, "n_importe_quoi")
    with pytest.raises(LookupError):
        set_validation(conn, 999, "validee")


def test_own_domains_ignored_through_import(conn, comp, tmp_path):
    repo.seed_own_publications(conn)
    html = page('<table><tr><td><p>Sponsorisé</p><a href="https://evenement.fr/promo">Découvrez</a>'
                "</td></tr></table>")
    do_import(conn, comp, tmp_path, html, 1)
    assert len(rows(conn, "SELECT * FROM ad_blocks")) == 1
    assert rows(conn, "SELECT * FROM advertisers") == []


def test_analysis_failure_does_not_cancel_import(conn, comp, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("panne simulée")
    monkeypatch.setattr(importer, "analyze_edition", boom)
    r = do_import(conn, comp, tmp_path, page(SPONSOR), 1)
    assert r.statut == "ok" and "Analyse publicitaire en échec" in r.message
    assert len(rows(conn, "SELECT * FROM editions")) == 1


def test_rules_file_is_valid_json_with_expected_keys():
    assert RULES.seuil_confirmee > RULES.seuil_potentielle > 0 and RULES.libelles
