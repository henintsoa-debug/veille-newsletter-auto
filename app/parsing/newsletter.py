"""Extraction des métadonnées et du contenu d'une newsletter (.eml ou .html).

Principe : tout ce qui n'est pas explicitement présent dans la source reste None
(inconnu). Chaque métadonnée est accompagnée de sa source (*_source).
"""

import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlparse

from bs4 import BeautifulSoup

_TRACKING_KEYS = {"mc_eid", "mc_cid", "gclid", "fbclid", "mkt_tok", "_hsenc", "_hsmi"}
_DATE_METAS = ("date", "article:published_time", "dc.date", "dcterms.date", "sent-date")
_PLAIN_URL = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass
class Link:
    url: str
    domaine: str | None
    ancre: str | None
    a_suivi: bool
    position: int


@dataclass
class Image:
    src: str
    alt: str | None
    largeur: str | None
    hauteur: str | None
    lien_url: str | None
    position: int


@dataclass
class Heading:
    niveau: int
    texte: str
    position: int


@dataclass
class ParsedEdition:
    format_source: str  # 'eml' ou 'html'
    objet: str | None = None
    objet_source: str | None = None
    expediteur: str | None = None
    expediteur_source: str | None = None
    date_envoi: str | None = None  # ISO 8601, None si inconnue
    date_source: str | None = None
    message_id: str | None = None
    texte: str | None = None
    html: str | None = None
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)


def _clean(text: str | None) -> str | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text or None


def _parse_date(value: str | None) -> str | None:
    """Date ISO 8601, ou None si la valeur est absente ou illisible."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _has_tracking(url: str) -> bool:
    keys = {k.lower() for k, _ in parse_qsl(urlparse(url).query, keep_blank_values=True)}
    return any(k.startswith("utm_") or k in _TRACKING_KEYS for k in keys)


def _is_web_url(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


def _extract_html(html: str, parsed: ParsedEdition) -> None:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    for position, h in enumerate(soup.find_all(["h1", "h2", "h3"])):
        text = _clean(h.get_text(" "))
        if text:
            parsed.headings.append(Heading(int(h.name[1]), text, position))

    for position, a in enumerate(soup.find_all("a", href=True)):
        url = a["href"].strip()
        if _is_web_url(url):
            parsed.links.append(Link(url, urlparse(url).hostname, _clean(a.get_text(" ")),
                                     _has_tracking(url), position))

    for position, img in enumerate(soup.find_all("img", src=True)):
        parent = img.find_parent("a", href=True)
        lien = parent["href"].strip() if parent is not None else None
        parsed.images.append(Image(img["src"].strip(), _clean(img.get("alt")),
                                   img.get("width"), img.get("height"),
                                   lien if lien and _is_web_url(lien) else None, position))

    parsed.texte = _clean(soup.get_text(" "))


def _meta_content(soup: BeautifulSoup, names: tuple[str, ...]) -> tuple[str, str] | None:
    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or "").lower()
        if key in names and meta.get("content"):
            return key, meta["content"]
    return None


def parse_html(data: bytes) -> ParsedEdition:
    """Fichier .html : objet = <title>, date = balise <meta> explicite, sinon inconnus."""
    html = BeautifulSoup(data, "lxml").decode()  # gère l'encodage déclaré
    parsed = ParsedEdition(format_source="html", html=html)
    soup = BeautifulSoup(html, "lxml")
    if soup.title and _clean(soup.title.get_text()):
        parsed.objet = _clean(soup.title.get_text())
        parsed.objet_source = "balise <title> du HTML"
    meta = _meta_content(soup, _DATE_METAS)
    if meta:
        date = _parse_date(meta[1])
        if date:
            parsed.date_envoi, parsed.date_source = date, f"balise <meta {meta[0]}> du HTML"
    _extract_html(html, parsed)
    return parsed


def parse_eml(data: bytes) -> ParsedEdition:
    """Fichier .eml : en-têtes Subject / From / Date / Message-ID, corps HTML ou texte."""
    msg = BytesParser(policy=policy.default).parsebytes(data)
    parsed = ParsedEdition(format_source="eml")

    if _clean(msg["subject"]):
        parsed.objet, parsed.objet_source = _clean(str(msg["subject"])), "en-tête Subject"
    if _clean(msg["from"]):
        parsed.expediteur, parsed.expediteur_source = _clean(str(msg["from"])), "en-tête From"
    date = _parse_date(str(msg["date"]) if msg["date"] else None)
    if date:
        parsed.date_envoi, parsed.date_source = date, "en-tête Date"
    if _clean(msg["message-id"]):
        parsed.message_id = _clean(str(msg["message-id"]))

    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return parsed
    content = body.get_content()
    if body.get_content_subtype() == "html":
        parsed.html = content
        _extract_html(content, parsed)
    else:
        parsed.texte = _clean(content)
        for position, url in enumerate(dict.fromkeys(_PLAIN_URL.findall(content))):
            parsed.links.append(Link(url, urlparse(url).hostname, None, _has_tracking(url), position))
    return parsed


def parse_file_content(filename: str, data: bytes) -> ParsedEdition:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "eml":
        return parse_eml(data)
    if suffix in ("html", "htm"):
        return parse_html(data)
    raise ValueError(f"Format non pris en charge : .{suffix or '?'} (attendu : .eml, .html)")
