"""Annonceurs et validation humaine des détections."""

import re
import sqlite3

DOMAIN_SOURCE = "domaine du lien"


def normalize_name(name: str) -> str:
    return re.sub(r"[\W_]+", "", name.casefold())


def get_or_create_advertiser(conn: sqlite3.Connection, nom: str, nom_source: str,
                             domaine: str | None) -> int:
    """Retrouve l'annonceur par domaine (sinon par nom), ou le crée. Ne fusionne rien d'autre."""
    if domaine:
        row = conn.execute("SELECT * FROM advertisers WHERE domaine = ?", (domaine,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM advertisers WHERE domaine IS NULL AND nom_normalise = ?",
                           (normalize_name(nom),)).fetchone()
    if row:
        # Un vrai nom lu dans un libellé remplace un simple nom de domaine (tracé dans l'historique).
        if row["nom_source"] == DOMAIN_SOURCE and nom_source != DOMAIN_SOURCE:
            conn.execute("UPDATE advertisers SET nom = ?, nom_source = ?, nom_normalise = ?"
                         " WHERE id = ?", (nom, nom_source, normalize_name(nom), row["id"]))
            conn.execute("INSERT INTO audit_history (table_nom, ligne_id, champ, ancienne_valeur,"
                         " nouvelle_valeur) VALUES ('advertisers', ?, 'nom', ?, ?)",
                         (row["id"], row["nom"], nom))
        return row["id"]
    cur = conn.execute("INSERT INTO advertisers (nom, nom_source, nom_normalise, domaine)"
                       " VALUES (?, ?, ?, ?)", (nom, nom_source, normalize_name(nom), domaine))
    return cur.lastrowid


def set_validation(conn: sqlite3.Connection, detection_id: int, statut: str) -> None:
    """Validation humaine ('validee', 'rejetee' ou retour à 'a_valider'). Le niveau calculé
    n'est jamais modifié ; l'ancien statut est conservé dans audit_history."""
    if statut not in ("a_valider", "validee", "rejetee"):
        raise ValueError(f"Statut de validation inconnu : {statut!r}")
    row = conn.execute("SELECT statut_validation FROM detections WHERE id = ?",
                       (detection_id,)).fetchone()
    if row is None:
        raise LookupError(f"Détection {detection_id} introuvable.")
    if row["statut_validation"] == statut:
        return
    with conn:
        conn.execute("UPDATE detections SET statut_validation = ?, valide_le ="
                     " CASE WHEN ? = 'a_valider' THEN NULL ELSE datetime('now') END WHERE id = ?",
                     (statut, statut, detection_id))
        conn.execute("INSERT INTO audit_history (table_nom, ligne_id, champ, ancienne_valeur,"
                     " nouvelle_valeur) VALUES ('detections', ?, 'statut_validation', ?, ?)",
                     (detection_id, row["statut_validation"], statut))
