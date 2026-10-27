"""Requêtes de lecture du tableau de bord (aucun affichage ici, donc testables)."""

import sqlite3
from dataclasses import dataclass

from app.advertisers.stats import advertiser_stats
from app.ui.labels import NOUVEAUTE, fr

ALL_NIVEAUX = ("confirmee", "potentielle", "citee")
_PUB_NAME = ("CASE e.publication_type WHEN 'own' THEN (SELECT nom FROM own_publications"
             " WHERE id = e.publication_id) ELSE (SELECT nom FROM competitors"
             " WHERE id = e.publication_id) END")


@dataclass
class Filters:
    publication_type: str | None = None  # 'own' ou 'competitor' ; None = toutes
    publication_id: int | None = None
    date_debut: str | None = None  # AAAA-MM-JJ
    date_fin: str | None = None
    advertiser_id: int | None = None
    niveaux: tuple[str, ...] = ALL_NIVEAUX
    statuts: tuple[str, ...] = ("a_valider", "validee")  # les rejetées sont masquées par défaut

    @property
    def period_active(self) -> bool:
        return bool(self.date_debut or self.date_fin)


def _edition_where(f: Filters) -> tuple[str, list]:
    where, params = [], []
    if f.publication_type:
        where.append("e.publication_type = ?")
        params.append(f.publication_type)
        if f.publication_id is not None:
            where.append("e.publication_id = ?")
            params.append(f.publication_id)
    if f.date_debut:
        where.append("substr(e.date_envoi, 1, 10) >= ?")  # date inconnue : exclue
        params.append(f.date_debut)
    if f.date_fin:
        where.append("substr(e.date_envoi, 1, 10) <= ?")
        params.append(f.date_fin)
    return where, params


def _detection_where(f: Filters) -> tuple[str, list]:
    where, params = _edition_where(f)
    where.append(f"d.niveau IN ({','.join('?' * len(f.niveaux))})")
    params += list(f.niveaux)
    where.append(f"d.statut_validation IN ({','.join('?' * len(f.statuts))})")
    params += list(f.statuts)
    if f.advertiser_id is not None:
        where.append("d.advertiser_id = ?")
        params.append(f.advertiser_id)
    return " AND ".join(where), params


def list_publications(conn) -> list[dict]:
    """Choix du filtre « publication » : vos newsletters puis les concurrents."""
    out = []
    for r in conn.execute("SELECT id, nom, actif FROM own_publications ORDER BY nom COLLATE NOCASE"):
        out.append({"type": "own", "id": r["id"], "label": f"Mes newsletters · {r['nom']}",
                    "actif": bool(r["actif"])})
    for r in conn.execute("SELECT id, nom, actif FROM competitors ORDER BY nom COLLATE NOCASE"):
        out.append({"type": "competitor", "id": r["id"], "label": f"Concurrent · {r['nom']}",
                    "actif": bool(r["actif"])})
    return out


def list_advertisers(conn) -> list[dict]:
    rows = conn.execute("SELECT id, nom, domaine FROM advertisers ORDER BY nom COLLATE NOCASE")
    return [dict(r) for r in rows]


def detections_table(conn, f: Filters) -> list[dict]:
    where, params = _detection_where(f)
    rows = conn.execute(
        f"SELECT d.id AS detection_id, e.id AS edition_id, e.date_envoi, {_PUB_NAME} AS publication,"
        " e.objet, a.id AS advertiser_id, a.nom AS annonceur, a.domaine, d.niveau, d.score,"
        " d.statut_validation, b.type AS type_bloc FROM detections d"
        " JOIN editions e ON e.id = d.edition_id JOIN advertisers a ON a.id = d.advertiser_id"
        " LEFT JOIN ad_blocks b ON b.id = d.ad_block_id"
        f" WHERE {where} ORDER BY e.date_envoi IS NULL, e.date_envoi DESC, d.id DESC", params)
    return [dict(r) for r in rows]


def undated_excluded(conn, f: Filters) -> int:
    """Éditions écartées parce que leur date est inconnue alors qu'une période est demandée."""
    if not f.period_active:
        return 0
    g = Filters(f.publication_type, f.publication_id)
    where, params = _edition_where(g)
    where.append("e.date_envoi IS NULL")
    return conn.execute("SELECT COUNT(*) FROM editions e WHERE " + " AND ".join(where), params).fetchone()[0]


def editions_table(conn, f: Filters) -> list[dict]:
    where, params = _edition_where(f)
    on = [f"d.niveau IN ({','.join('?' * len(f.niveaux))})",
          f"d.statut_validation IN ({','.join('?' * len(f.statuts))})"]
    on_params = list(f.niveaux) + list(f.statuts)
    having = ""
    if f.advertiser_id is not None:
        on.append("d.advertiser_id = ?")
        on_params.append(f.advertiser_id)
        having = " HAVING COUNT(d.id) > 0"
    sql = (f"SELECT e.id AS edition_id, e.date_envoi, e.date_statut, e.date_source,"
           f" {_PUB_NAME} AS publication, e.objet, e.objet_source, e.expediteur, e.expediteur_source,"
           " e.format_source, e.fichier_source, COUNT(d.id) AS nb_detections FROM editions e"
           f" LEFT JOIN detections d ON d.edition_id = e.id AND {' AND '.join(on)}"
           f" {'WHERE ' + ' AND '.join(where) if where else ''} GROUP BY e.id{having}"
           " ORDER BY e.date_envoi IS NULL, e.date_envoi DESC, e.id DESC")
    return [dict(r) for r in conn.execute(sql, on_params + params)]


def edition_detail(conn, edition_id: int) -> dict | None:
    ed = conn.execute(f"SELECT e.*, {_PUB_NAME} AS publication FROM editions e WHERE e.id = ?",
                      (edition_id,)).fetchone()
    if ed is None:
        return None
    detections = []
    for d in conn.execute(
            "SELECT d.*, a.nom AS annonceur, a.domaine, b.type AS type_bloc, b.extrait_texte"
            " FROM detections d JOIN advertisers a ON a.id = d.advertiser_id"
            " LEFT JOIN ad_blocks b ON b.id = d.ad_block_id WHERE d.edition_id = ?"
            " ORDER BY d.niveau = 'citee', d.score DESC, d.id", (edition_id,)):
        item = dict(d)
        item["preuves"] = [dict(p) for p in conn.execute(
            "SELECT regle, force, poids, extrait, emplacement FROM evidences"
            " WHERE detection_id = ? ORDER BY poids DESC, id", (d["id"],))]
        detections.append(item)
    return {
        "edition": dict(ed), "detections": detections,
        "titres": [dict(r) for r in conn.execute(
            "SELECT niveau, texte FROM edition_headings WHERE edition_id = ? ORDER BY position", (edition_id,))],
        "nb_liens": conn.execute("SELECT COUNT(*) FROM edition_links WHERE edition_id = ?", (edition_id,)).fetchone()[0],
        "nb_images": conn.execute("SELECT COUNT(*) FROM edition_images WHERE edition_id = ?", (edition_id,)).fetchone()[0],
    }


def pending_validation(conn, limit: int = 20) -> tuple[list[dict], int]:
    """Détections à valider (confirmées d'abord, puis par score) et leur nombre total."""
    total = conn.execute("SELECT COUNT(*) FROM detections WHERE statut_validation = 'a_valider'"
                         " AND niveau != 'citee'").fetchone()[0]
    rows = conn.execute(
        f"SELECT d.id AS detection_id, d.niveau, d.score, a.nom AS annonceur, a.domaine,"
        f" e.id AS edition_id, e.objet, e.date_envoi, {_PUB_NAME} AS publication, b.type AS type_bloc,"
        " b.extrait_texte FROM detections d JOIN advertisers a ON a.id = d.advertiser_id"
        " JOIN editions e ON e.id = d.edition_id LEFT JOIN ad_blocks b ON b.id = d.ad_block_id"
        " WHERE d.statut_validation = 'a_valider' AND d.niveau != 'citee'"
        " ORDER BY d.niveau = 'potentielle', d.score DESC, d.id LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["preuves"] = [dict(p) for p in conn.execute(
            "SELECT regle, force, poids, extrait, emplacement FROM evidences"
            " WHERE detection_id = ? ORDER BY poids DESC, id", (r["detection_id"],))]
        out.append(item)
    return out, total


def overview(conn, f: Filters) -> dict:
    where, params = _edition_where(f)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    editions = conn.execute(f"SELECT COUNT(*) FROM editions e {clause}", params).fetchone()[0]
    by_month = conn.execute(
        f"SELECT substr(e.date_envoi, 1, 7) AS mois, COUNT(*) AS n FROM editions e {clause}"
        f"{' AND' if where else 'WHERE'} e.date_envoi IS NOT NULL GROUP BY mois ORDER BY mois", params)
    stats = advertiser_stats(
        conn, f.date_debut, f.date_fin, inclure_potentielles="potentielle" in f.niveaux,
        publication_type=f.publication_type, publication_id=f.publication_id)
    a_valider = conn.execute("SELECT COUNT(*) FROM detections WHERE statut_validation = 'a_valider'"
                             " AND niveau != 'citee'").fetchone()[0]
    return {
        "editions": editions,
        "editions_sans_date": conn.execute(
            f"SELECT COUNT(*) FROM editions e {clause}{' AND' if where else 'WHERE'} e.date_envoi IS NULL",
            params).fetchone()[0],
        "annonceurs": len(stats),
        "recurrents": sum(s.recurrent for s in stats),
        "nouveaux": sum(s.nouveaute == "nouveau" for s in stats),
        "a_valider": a_valider,
        "editions_par_mois": [dict(r) for r in by_month],
    }


def advertiser_stats_filtered(conn, f: Filters) -> list:
    """Statistiques par annonceur selon les filtres globaux."""
    stats = advertiser_stats(
        conn, f.date_debut, f.date_fin, inclure_potentielles="potentielle" in f.niveaux,
        publication_type=f.publication_type, publication_id=f.publication_id)
    if f.advertiser_id is not None:
        stats = [s for s in stats if s.advertiser_id == f.advertiser_id]
    return stats


def stat_rows(stats) -> list[dict]:
    """Lignes affichables (libellés français, « Inconnue » pour une date absente)."""
    return [{"nom": s.nom, "domaine": s.domaine, "nb_editions": s.nb_editions,
             "nb_detections": s.nb_detections, "publications": ", ".join(s.publications),
             "premiere": s.premiere_date or "Inconnue", "derniere": s.derniere_date or "Inconnue",
             "recurrent": "Oui" if s.recurrent else "Non", "nouveaute": fr(s.nouveaute, NOUVEAUTE),
             "sans_date": s.editions_sans_date} for s in stats]


def compare_own_vs_competitors(conn, f: Filters) -> dict[str, list[dict]]:
    """Annonceurs présents chez les concurrents seulement, chez vous seulement, ou des deux côtés."""
    kw = dict(date_debut=f.date_debut, date_fin=f.date_fin,
              inclure_potentielles="potentielle" in f.niveaux)
    own = {s.advertiser_id: s for s in advertiser_stats(conn, publication_type="own", **kw)}
    comp = {s.advertiser_id: s for s in advertiser_stats(conn, publication_type="competitor", **kw)}

    def row(adv_id):
        s = own.get(adv_id) or comp.get(adv_id)
        return {"annonceur": s.nom, "domaine": s.domaine,
                "editions_chez_moi": own[adv_id].nb_editions if adv_id in own else 0,
                "editions_chez_concurrents": comp[adv_id].nb_editions if adv_id in comp else 0,
                "concurrents": comp[adv_id].publications if adv_id in comp else []}

    return {
        "concurrents_seulement": [row(i) for i in comp if i not in own],
        "communs": [row(i) for i in comp if i in own],
        "chez_moi_seulement": [row(i) for i in own if i not in comp],
    }
