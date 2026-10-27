"""Annonceurs récurrents / nouveaux et historique par annonceur.

Ne comptent que les détections 'confirmee' (et 'potentielle' si demandé) non rejetées :
les simples mentions ('citee') et les détections rejetées sont toujours exclues.
Une édition sans date connue n'est jamais datée au hasard : elle empêche de conclure
qu'un annonceur est « nouveau » (statut 'indetermine').
"""

import sqlite3
from dataclasses import dataclass, field

from app.detection.rules import load_rules

PUBLICATION_TABLES = {"own": "own_publications", "competitor": "competitors"}


@dataclass
class AdvertiserStat:
    advertiser_id: int
    nom: str
    domaine: str | None
    nb_editions: int  # éditions distinctes, dans la période demandée
    nb_detections: int
    publications: list[str] = field(default_factory=list)
    premiere_date: str | None = None  # historique complet, éditions datées uniquement
    derniere_date: str | None = None
    editions_sans_date: int = 0  # éditions concernées dont la date est inconnue
    recurrent: bool = False
    nouveaute: str = "indetermine"  # 'nouveau', 'non_nouveau' ou 'indetermine'


def _levels(inclure_potentielles: bool) -> tuple[str, ...]:
    return ("confirmee", "potentielle") if inclure_potentielles else ("confirmee",)


def _day(date_envoi: str | None) -> str | None:
    return date_envoi[:10] if date_envoi else None  # AAAA-MM-JJ, tel qu'écrit dans la source


def advertiser_stats(conn: sqlite3.Connection, date_debut: str | None = None,
                     date_fin: str | None = None, seuil: int | None = None,
                     inclure_potentielles: bool = True, publication_type: str | None = None,
                     publication_id: int | None = None) -> list[AdvertiserStat]:
    """Statistiques par annonceur.

    date_debut / date_fin : 'AAAA-MM-JJ' (inclus). Elles limitent le comptage des éditions ;
    la nouveauté, elle, s'appuie toujours sur tout l'historique de l'annonceur.
    publication_type / publication_id : restreint à une publication ('own' ou 'competitor').
    """
    seuil = seuil if seuil is not None else load_rules().seuil_recurrence
    levels = _levels(inclure_potentielles)
    sql = (
        "SELECT a.id AS adv_id, a.nom, a.domaine, d.id AS det_id, d.edition_id, e.date_envoi,"
        " e.publication_type, e.publication_id FROM detections d"
        " JOIN advertisers a ON a.id = d.advertiser_id JOIN editions e ON e.id = d.edition_id"
        f" WHERE d.niveau IN ({','.join('?' * len(levels))}) AND d.statut_validation != 'rejetee'"
    )
    params: list = list(levels)
    if publication_type:
        sql += " AND e.publication_type = ?"
        params.append(publication_type)
        if publication_id is not None:
            sql += " AND e.publication_id = ?"
            params.append(publication_id)

    names = {t: {r["id"]: r["nom"] for r in conn.execute(f"SELECT id, nom FROM {table}")}
             for t, table in PUBLICATION_TABLES.items()}
    per_adv: dict[int, dict] = {}
    for r in conn.execute(sql, params):
        adv = per_adv.setdefault(r["adv_id"], {
            "nom": r["nom"], "domaine": r["domaine"], "editions": {}, "det_par_edition": {}})
        adv["editions"][r["edition_id"]] = (_day(r["date_envoi"]),
                                            names[r["publication_type"]].get(r["publication_id"]))
        adv["det_par_edition"][r["edition_id"]] = adv["det_par_edition"].get(r["edition_id"], 0) + 1

    in_period = lambda day: day is not None and (not date_debut or day >= date_debut) \
        and (not date_fin or day <= date_fin)
    filtered = bool(date_debut or date_fin)

    stats = []
    for adv_id, adv in per_adv.items():
        days = [d for d, _ in adv["editions"].values() if d]
        undated = sum(1 for d, _ in adv["editions"].values() if d is None)
        counted = {eid: v for eid, v in adv["editions"].items()
                   if not filtered or in_period(v[0])}
        if not counted:
            continue  # aucune activité dans la période
        first = min(days) if days else None
        stat = AdvertiserStat(
            advertiser_id=adv_id, nom=adv["nom"], domaine=adv["domaine"],
            nb_editions=len(counted),
            nb_detections=sum(adv["det_par_edition"][eid] for eid in counted),
            publications=sorted({p for _, p in counted.values() if p}),
            premiere_date=first, derniere_date=max(days) if days else None,
            editions_sans_date=undated, recurrent=len(counted) >= seuil)
        if first is None:
            stat.nouveaute = "indetermine"
        elif not filtered:
            stat.nouveaute = "indetermine" if undated else "non_nouveau"
        elif first < (date_debut or ""):
            stat.nouveaute = "non_nouveau"  # déjà vu avant la période : certain
        elif in_period(first):
            stat.nouveaute = "indetermine" if undated else "nouveau"
        else:
            stat.nouveaute = "indetermine"
        stats.append(stat)
    return sorted(stats, key=lambda s: (-s.nb_editions, s.nom.casefold()))


def advertiser_history(conn: sqlite3.Connection, advertiser_id: int) -> list[sqlite3.Row]:
    """Toutes les détections d'un annonceur (même rejetées ou citées), de la plus ancienne à
    la plus récente. Les éditions sans date sont placées en fin de liste."""
    rows = conn.execute(
        "SELECT d.id AS detection_id, d.niveau, d.score, d.statut_validation,"
        " b.type AS type_bloc, e.id AS edition_id, e.objet, e.date_envoi, e.date_statut,"
        " e.publication_type, e.publication_id, e.fichier_source,"
        " CASE e.publication_type WHEN 'own' THEN (SELECT nom FROM own_publications"
        "   WHERE id = e.publication_id) ELSE (SELECT nom FROM competitors"
        "   WHERE id = e.publication_id) END AS publication_nom"
        " FROM detections d JOIN editions e ON e.id = d.edition_id"
        " LEFT JOIN ad_blocks b ON b.id = d.ad_block_id WHERE d.advertiser_id = ?"
        " ORDER BY e.date_envoi IS NULL, e.date_envoi, d.id", (advertiser_id,)).fetchall()
    return rows
