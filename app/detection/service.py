"""Analyse d'une édition importée : détection, stockage, preuves, simples mentions."""

import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlparse

from app.advertisers.repository import get_or_create_advertiser
from app.db.domains import registrable_domain
from app.detection.detector import detect
from app.detection.rules import Rules, load_rules
from app.parsing.newsletter import ParsedEdition


@dataclass
class AnalysisSummary:
    blocs: int = 0
    detections: int = 0
    mentions: int = 0


def _store_blocks(conn, edition_id: int, blocks, poids: dict[str, int]) -> tuple[int, set[int]]:
    detections, advertiser_ids = 0, set()
    for b in blocks:
        cur = conn.execute(
            "INSERT INTO ad_blocks (edition_id, type, ordre, extrait_texte, extrait_html, score,"
            " niveau) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (edition_id, b.type, b.ordre, b.extrait_texte, b.extrait_html, b.score, b.niveau))
        block_id = cur.lastrowid
        for cand in b.annonceurs:
            adv_id = get_or_create_advertiser(conn, cand.nom, cand.nom_source, cand.domaine)
            det = conn.execute(
                "INSERT OR IGNORE INTO detections (edition_id, ad_block_id, advertiser_id, niveau,"
                " score) VALUES (?, ?, ?, ?, ?)", (edition_id, block_id, adv_id, b.niveau, b.score))
            if det.rowcount:
                conn.executemany(
                    "INSERT INTO evidences (detection_id, regle, force, poids, extrait, emplacement)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [(det.lastrowid, s.regle, s.force, poids[s.force], s.extrait, s.emplacement)
                     for s in b.signals])
                detections += 1
            advertiser_ids.add(adv_id)
    return detections, advertiser_ids


def _store_mentions(conn, edition_id: int, parsed: ParsedEdition, already: set[int]) -> int:
    """Annonceurs déjà connus (détectés ailleurs, non rejetés) simplement cités ici hors publicité.
    Niveau 'citee', score 0 : ce n'est jamais une publicité."""
    known = conn.execute(
        "SELECT DISTINCT a.id, a.nom, a.domaine FROM advertisers a JOIN detections d"
        " ON d.advertiser_id = a.id WHERE d.niveau IN ('confirmee', 'potentielle')"
        " AND d.statut_validation != 'rejetee'").fetchall()
    text = parsed.texte or ""
    count = 0
    for adv in known:
        if adv["id"] in already:
            continue
        evidence = None
        if len(adv["nom"]) >= 3:
            m = re.search(rf"(?<!\w){re.escape(adv['nom'])}(?!\w)", text, re.IGNORECASE)
            if m:
                evidence = ("nom_cite_dans_le_texte", text[max(0, m.start() - 40):m.end() + 40], "texte")
        if evidence is None and adv["domaine"]:
            link = next((l for l in parsed.links
                         if registrable_domain(urlparse(l.url).hostname) == adv["domaine"]), None)
            if link:
                evidence = ("lien_editorial", link.url, "lien hors bloc publicitaire")
        if evidence is None:
            continue
        det = conn.execute(
            "INSERT OR IGNORE INTO detections (edition_id, ad_block_id, advertiser_id, niveau,"
            " score) VALUES (?, NULL, ?, 'citee', 0)", (edition_id, adv["id"]))
        if det.rowcount:
            conn.execute("INSERT INTO evidences (detection_id, regle, force, poids, extrait,"
                         " emplacement) VALUES (?, ?, 'aucun', 0, ?, ?)",
                         (det.lastrowid, evidence[0], evidence[1], evidence[2]))
            count += 1
    return count


def analyze_edition(conn: sqlite3.Connection, edition_id: int, parsed: ParsedEdition,
                    ignored_domains: set[str], rules: Rules | None = None) -> AnalysisSummary:
    """Détecte et enregistre les blocs publicitaires d'une édition (une seule transaction)."""
    rules = rules or load_rules()
    blocks = detect(parsed, ignored_domains, rules)
    with conn:
        detections, advertiser_ids = _store_blocks(conn, edition_id, blocks, rules.poids)
        mentions = _store_mentions(conn, edition_id, parsed, advertiser_ids)
    return AnalysisSummary(len(blocks), detections, mentions)
