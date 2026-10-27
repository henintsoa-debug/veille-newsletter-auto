"""Détection des blocs publicitaires dans le HTML d'une newsletter.

Fonction pure (aucun accès base). Chaque signal conserve sa preuve : règle, extrait
exact de la source et emplacement. Un bloc n'est retenu que si son score atteint le
seuil « potentielle » ; « confirmée » exige un signal fort (libellé explicite).
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from app.db.domains import registrable_domain
from app.detection.rules import Rules
from app.parsing.newsletter import ParsedEdition, _has_tracking

_HTML_MAX = 5000
_TEXT_MAX = 500


@dataclass
class Signal:
    regle: str
    force: str  # 'fort', 'moyen', 'faible'
    extrait: str  # texte ou URL exact, tel qu'il figure dans la source
    emplacement: str


@dataclass
class AdvertiserCandidate:
    nom: str
    nom_source: str  # d'où vient le nom
    domaine: str | None


@dataclass
class AdBlock:
    type: str  # 'bloc_sponsorise', 'banniere', 'lien_commercial'
    ordre: int
    extrait_texte: str
    extrait_html: str
    signals: list[Signal] = field(default_factory=list)
    annonceurs: list[AdvertiserCandidate] = field(default_factory=list)
    score: int = 0
    niveau: str = ""


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _name_from(raw: str | None) -> str | None:
    name = _clean(raw).strip(" .:;-–—|*")
    return name if name and re.search(r"[^\W\d_]", name) else None


def _inside(element, outer) -> bool:
    """True si element est un descendant strict de outer (comparaison par identité)."""
    return any(p is outer for p in element.parents)


class _Analysis:
    def __init__(self, parsed: ParsedEdition, ignored: set[str], rules: Rules):
        self.rules = rules
        self.ignored = ignored | rules.domaines_ignores
        self.soup = BeautifulSoup(parsed.html or "", "lxml")
        for tag in self.soup(["script", "style"]):
            tag.decompose()
        self.processed: set[int] = set()
        self.blocks: list[AdBlock] = []

    # --- utilitaires -------------------------------------------------------
    def external_links(self, element):
        """(balise <a>, domaine) pour les liens vers un domaine externe non ignoré."""
        found = []
        anchors = [element] if element.name == "a" else element.find_all("a", href=True)
        for a in anchors:
            href = (a.get("href") or "").strip()
            if urlparse(href).scheme not in ("http", "https"):
                continue
            domain = registrable_domain(urlparse(href).hostname)
            if domain and domain not in self.ignored:
                found.append((a, domain))
        return found

    def _link_signals(self, links, where: str) -> list[Signal]:
        signals = []
        tracked = next((a for a, _ in links if _has_tracking(a["href"])), None)
        if tracked is not None:
            signals.append(Signal("lien_avec_suivi", "faible", tracked["href"].strip(),
                                  f"{where} (lien)"))
        banner = next((a for a, _ in links if a.find("img") is not None
                       and not _clean(a.get_text(" "))), None)
        if banner is not None:
            signals.append(Signal("banniere_cliquable", "moyen", banner["href"].strip(),
                                  f"{where} (image cliquable)"))
        for a, _ in links:
            texts = [_clean(a.get_text(" ")), *(_clean(i.get("alt")) for i in a.find_all("img"))]
            cta = next((t for t in texts if t and self.rules.appels_a_action.search(t)), None)
            if cta:
                signals.append(Signal("appel_a_action", "faible", cta, f"{where} (lien)"))
                break
        return signals

    def _finish(self, block: AdBlock) -> AdBlock | None:
        block.score, niveau = self.rules.evaluate([s.force for s in block.signals])
        if niveau is None:
            return None
        block.niveau = niveau
        block.ordre = len(self.blocks) + 1
        self.blocks.append(block)
        return block

    # --- 1. blocs introduits par un libellé --------------------------------
    def _label_nodes(self):
        for node in self.soup.find_all(string=True):
            if isinstance(node, Comment):
                continue
            text = _clean(node)
            if not text or len(text) > self.rules.libelle_longueur_max or node.parent is None:
                continue
            for rule in self.rules.libelles:
                m = rule.pattern.match(text)
                if m:
                    yield node, rule, m
                    break

    def _climb(self, element):
        """Plus grand conteneur qui reste « petit » : le bloc publicitaire."""
        while element.parent is not None and element.parent.name not in ("body", "html", "[document]"):
            parent = element.parent
            if len(_clean(parent.get_text(" "))) > self.rules.bloc_texte_max:
                break
            if len({d for _, d in self.external_links(parent)}) > self.rules.bloc_domaines_externes_max:
                break
            element = parent
        return element

    def labelled_blocks(self):
        groups: dict[int, tuple] = {}
        for node, rule, m in self._label_nodes():
            element = self._climb(node.parent)
            entry = groups.setdefault(id(element), (element, []))
            entry[1].append((node, rule, m))
        elements = [e for e, _ in groups.values()]
        outermost = [e for e in elements if not any(_inside(e, o) for o in elements)]
        for key, (element, labels) in list(groups.items()):
            if any(element is o for o in outermost):
                continue
            # bloc imbriqué : ses libellés rejoignent le bloc englobant le plus large
            outer = next(o for o in outermost if _inside(element, o))
            groups[id(outer)][1].extend(labels)
            del groups[key]

        for element, labels in groups.values():
            ordre = len(self.blocks) + 1
            where = f"bloc n°{ordre}"
            signals, names = [], []
            for node, rule, m in labels:
                signals.append(Signal(rule.regle, rule.force, _clean(node), f"{where} (texte)"))
                if m.groupdict().get("nom"):
                    names.append(_name_from(m.group("nom")))
            links = self.external_links(element)
            signals += self._link_signals(links, where)
            self.processed.update(id(a) for a in element.find_all("a"))
            block = AdBlock("bloc_sponsorise", ordre, _clean(element.get_text(" "))[:_TEXT_MAX],
                            str(element)[:_HTML_MAX], signals)
            block.annonceurs = self._advertisers(links, next((n for n in names if n), None))
            self._finish(block)

    def _advertisers(self, links, label_name: str | None) -> list[AdvertiserCandidate]:
        domains = list(dict.fromkeys(d for _, d in links))
        if not domains:
            return [AdvertiserCandidate(label_name, "libellé du bloc", None)] if label_name else []
        result = []
        for domain in domains:
            base = domain.split(".")[0].lower()
            named = label_name and (len(domains) == 1 or base in re.sub(r"\W", "", label_name.lower()))
            if named:
                result.append(AdvertiserCandidate(label_name, "libellé du bloc", domain))
            else:
                result.append(AdvertiserCandidate(domain, "domaine du lien", domain))
        return result

    # --- 2. bannières hors bloc, 3. liens commerciaux -----------------------
    def banners_and_links(self):
        for a in self.soup.find_all("a", href=True):
            if id(a) in self.processed:
                continue
            links = self.external_links(a)
            if not links:
                continue
            self.processed.add(id(a))
            ordre = len(self.blocks) + 1
            where = f"bloc n°{ordre}"
            signals = self._link_signals(links, where)
            is_banner = any(s.regle == "banniere_cliquable" for s in signals)
            parent_text = _clean((a.parent or a).get_text(" "))
            block = AdBlock("banniere" if is_banner else "lien_commercial", ordre,
                            parent_text[:_TEXT_MAX], str(a)[:_HTML_MAX], signals)
            block.annonceurs = self._advertisers(links, None)
            self._finish(block)


def detect(parsed: ParsedEdition, ignored_domains: set[str], rules: Rules) -> list[AdBlock]:
    """Blocs publicitaires (confirmés ou potentiels) d'une édition HTML.

    ignored_domains : domaines de vos publications et du concurrent importé, jamais annonceurs.
    """
    if not parsed.html:
        return []
    analysis = _Analysis(parsed, ignored_domains, rules)
    analysis.labelled_blocks()
    analysis.banners_and_links()
    return analysis.blocks
