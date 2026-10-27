"""Accès aux données : publications de l'utilisateur et concurrents.

Aucune fonction de suppression : on désactive (actif = 0). Chaque modification
est tracée dans audit_history. Les champs inconnus restent NULL (jamais déduits).
"""

import sqlite3

from app.db.domains import normalize_domain

# Vos newsletters. Nom = domaine, le reste est inconnu : rien n'est inventé.
OWN_PUBLICATION_DOMAINS = [
    "mammam-ia.com",
    "newsletter.lebigdata.fr",
    "evenement.fr",
    "cyberdefense.net",
]

_FIELDS = {
    "own_publications": {"nom", "url_newsletter", "thematique", "actif"},
    "competitors": {"nom", "url_newsletter", "description", "actif"},
}


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _insert(conn, table: str, nom: str | None, domaine: str, source_info: str, **extra) -> int:
    domain = normalize_domain(domaine)
    name = _clean(nom) or domain
    columns = ["nom", "domaine", "source_info", *extra]
    values = [name, domain, source_info, *(_clean(v) for v in extra.values())]
    marks = ", ".join("?" for _ in columns)
    try:
        with conn:
            cur = conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", values
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Le domaine {domain} existe déjà.") from exc
    return cur.lastrowid


def _update(conn, table: str, row_id: int, **changes) -> None:
    unknown = set(changes) - _FIELDS[table]
    if unknown:
        raise ValueError(f"Champs non modifiables : {sorted(unknown)}")
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise LookupError(f"Ligne {row_id} introuvable dans {table}.")
    with conn:
        for field, new in changes.items():
            if field != "actif":
                new = _clean(new)
                if field == "nom" and new is None:
                    raise ValueError("Le nom ne peut pas être vide.")
            else:
                new = 1 if new else 0
            old = row[field]
            if old == new:
                continue
            conn.execute(f"UPDATE {table} SET {field} = ? WHERE id = ?", (new, row_id))
            conn.execute(
                "INSERT INTO audit_history (table_nom, ligne_id, champ, ancienne_valeur,"
                " nouvelle_valeur) VALUES (?, ?, ?, ?, ?)",
                (table, row_id, field, None if old is None else str(old), str(new) if new is not None else None),
            )


def _list(conn, table: str, only_active: bool) -> list[sqlite3.Row]:
    where = " WHERE actif = 1" if only_active else ""
    return conn.execute(f"SELECT * FROM {table}{where} ORDER BY nom COLLATE NOCASE").fetchall()


# --- Vos publications -------------------------------------------------------

def add_own_publication(conn, domaine, nom=None, url_newsletter=None, thematique=None,
                        source_info="Saisie utilisateur") -> int:
    return _insert(conn, "own_publications", nom, domaine, source_info,
                   url_newsletter=url_newsletter, thematique=thematique)


def update_own_publication(conn, pub_id: int, **changes) -> None:
    _update(conn, "own_publications", pub_id, **changes)


def list_own_publications(conn, only_active=False):
    return _list(conn, "own_publications", only_active)


def seed_own_publications(conn) -> int:
    """Pré-charge vos 4 newsletters si absentes. Renvoie le nombre ajouté."""
    added = 0
    for domain in OWN_PUBLICATION_DOMAINS:
        try:
            add_own_publication(conn, domain, source_info="Liste fournie par l'utilisateur")
            added += 1
        except ValueError:
            pass  # déjà présente : on ne touche à rien
    return added


# --- Concurrents ------------------------------------------------------------

def add_competitor(conn, domaine, nom=None, url_newsletter=None, description=None,
                   source_info="Saisie utilisateur") -> int:
    return _insert(conn, "competitors", nom, domaine, source_info,
                   url_newsletter=url_newsletter, description=description)


def update_competitor(conn, competitor_id: int, **changes) -> None:
    _update(conn, "competitors", competitor_id, **changes)


def list_competitors(conn, only_active=False):
    return _list(conn, "competitors", only_active)


def history(conn, table: str, row_id: int):
    return conn.execute(
        "SELECT * FROM audit_history WHERE table_nom = ? AND ligne_id = ? ORDER BY id",
        (table, row_id),
    ).fetchall()
