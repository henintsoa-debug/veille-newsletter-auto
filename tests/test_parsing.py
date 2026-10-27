import pytest

from app.parsing.newsletter import parse_eml, parse_file_content, parse_html
from tests.samples import HTML_BODY, make_eml


def test_eml_metadata_and_sources():
    p = parse_eml(make_eml())
    assert p.objet == "Édition fictive n°1" and p.objet_source == "en-tête Subject"
    assert p.expediteur == "Rédaction <redaction@example.net>"
    assert p.date_envoi == "2025-06-10T08:30:00+02:00" and p.date_source == "en-tête Date"
    assert p.message_id == "<abc123@example.net>"


def test_eml_missing_headers_are_unknown_not_invented():
    p = parse_eml(make_eml(subject=None, sender=None, date=None, message_id=None))
    assert p.objet is None and p.expediteur is None
    assert p.date_envoi is None and p.date_source is None and p.message_id is None


def test_eml_invalid_date_is_unknown():
    assert parse_eml(make_eml(date="pas une date")).date_envoi is None


def test_eml_headings_links_images():
    p = parse_eml(make_eml())
    assert [(h.niveau, h.texte) for h in p.headings] == [
        (1, "Grande une fictive"), (2, "Deuxième sujet")]
    urls = [l.url for l in p.links]
    assert urls == ["https://example.org/a?utm_source=nl&id=3", "https://sponsor.example.com/promo"]
    assert p.links[0].a_suivi and not p.links[1].a_suivi
    assert p.links[0].domaine == "example.org" and p.links[0].ancre == "Lire la suite"
    banner = p.images[0]
    assert banner.alt == "Bannière fictive" and banner.largeur == "600"
    assert banner.lien_url == "https://sponsor.example.com/promo"
    assert p.images[1].src == "cid:logo123" and p.images[1].lien_url is None
    assert "var x" not in p.texte and "Texte de l'article A." in p.texte


def test_eml_plain_text_only():
    p = parse_eml(make_eml(html=None))
    assert p.html is None and "Texte brut fictif" in p.texte
    assert [l.url for l in p.links] == ["https://example.org/plain"]


def test_html_file_title_and_no_invented_metadata():
    p = parse_html(HTML_BODY.encode())
    assert p.objet == "Titre fictif" and p.objet_source == "balise <title> du HTML"
    assert p.expediteur is None and p.date_envoi is None and p.message_id is None


def test_html_date_only_from_explicit_meta():
    html = '<html><head><meta name="date" content="2025-03-01T10:00:00Z"></head><body>x</body></html>'
    p = parse_html(html.encode())
    assert p.date_envoi == "2025-03-01T10:00:00+00:00"
    assert p.date_source == "balise <meta date> du HTML"


def test_unsupported_format():
    with pytest.raises(ValueError):
        parse_file_content("fichier.pdf", b"x")
