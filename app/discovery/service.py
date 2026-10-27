"""Candidats concurrents : proposition, validation humaine, acceptation ou rejet.

Un candidat n'est jamais un concurrent tant que l'utilisateur ne l'a pas accepté. Les
candidats viennent de fichiers vérifiés (config/candidats_*.json), de la saisie d'un site ou
de l'import d'une liste (voir list_import.py). Nom = domaine si aucun nom n'est fourni ; les
autres champs restent inconnus s'ils ne sont pas donnés. Aucune donnée n'est inventée.
"""

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from app.db import repository as repo
from app.db.domains import normalize_domain, registrable_domain

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "discovery.json"
MANUAL = "recherche_manuelle"


class KnownDomainError(ValueError):
    """Le domaine est déjà une de vos newsletters, un concurrent ou un candidat."""


def load_platforms(path: Path | str | None = None) -> set[str]:
    data = json.loads(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    return set(data["plateformes_hebergees"])


def domain_key(host: str | None, platforms: set[str]) -> str | None:
    """Clé de comparaison : l'hôte complet pour une plateforme d'hébergement, sinon le domaine de base."""
    if not host:
        return None
    host = host.lower().removeprefix("www.")
    reg = registrable_domain(host)
    return host if reg in platforms else reg


def _known_keys(conn, platforms: set[str]) -> set[str]:
    keys = set()
    for table in ("own_publications", "competitors", "competitor_candidates"):
        for row in conn.execute(f"SELECT domaine FROM {table}"):
            keys.add(domain_key(row["domaine"], platforms))
    return keys


def _http_url(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if urlparse(value).scheme in ("http", "https") and urlparse(value).hostname else None


def add_candidate(conn: sqlite3.Connection, domaine: str, nom: str | None = None,
                  url_newsletter: str | None = None, description: str | None = None,
                  theme: str | None = None, fournisseur: str = "Saisie utilisateur",
                  signaux: list[tuple[str, str, str]] | None = None,
                  platforms: set[str] | None = None) -> int:
    """Ajoute un candidat « à valider ». ValueError si le domaine est invalide,
    KnownDomainError s'il est déjà connu. signaux : (constat, extrait, source)."""
    domain = normalize_domain(domaine)
    platforms = load_platforms() if platforms is None else platforms
    if domain_key(domain, platforms) in _known_keys(conn, platforms):
        raise KnownDomainError(f"{domain} est déjà connu (vos newsletters, concurrents ou candidats).")
    url = _http_url(url_newsletter)
    description = (description or "").strip()[:300] or None
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO competitor_candidates (domaine, nom, url_trouvee, url_newsletter,"
                " description, requetes, fournisseur, score, mode_decouverte)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (domain, (nom or "").strip() or domain, url or f"https://{domain}", url, description,
                 (theme or "").strip() or "Thème non précisé", fournisseur, MANUAL))
            conn.executemany(
                "INSERT INTO candidate_signals (candidate_id, regle, poids, extrait, source)"
                " VALUES (?, ?, 0, ?, ?)", [(cur.lastrowid, *s) for s in (signaux or [])])
    except sqlite3.IntegrityError as exc:
        raise KnownDomainError(f"{domain} est déjà connu.") from exc
    return cur.lastrowid


def load_curated(conn, path: Path | str) -> tuple[int, int]:
    """Charge un fichier de candidats vérifiés (config/candidats_*.json). Renvoie (ajoutés, ignorés).
    Les domaines déjà connus (y compris les candidats rejetés) sont ignorés."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    platforms = load_platforms()
    added = ignored = 0
    for c in data["candidats"]:
        try:
            add_candidate(
                conn, c["domaine"], c.get("nom"), c.get("url_newsletter"), c.get("description"),
                c["theme"], f"{data['fournisseur']}, {data['date']}",
                [(regle, extrait, f"{url} (page consultée le {data['date']})")
                 for regle, extrait, url in c["signaux"]], platforms)
            added += 1
        except KnownDomainError:
            ignored += 1
    return added, ignored


def list_candidates(conn, statut: str = "propose") -> list[dict]:
    out = []
    for row in conn.execute("SELECT * FROM competitor_candidates WHERE statut = ?"
                            " ORDER BY id", (statut,)):
        item = dict(row)
        item["signaux"] = [dict(s) for s in conn.execute(
            "SELECT regle, poids, extrait, source FROM candidate_signals WHERE candidate_id = ?"
            " ORDER BY id", (row["id"],))]
        out.append(item)
    return out


def _candidate(conn, candidate_id: int, expected: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM competitor_candidates WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise LookupError(f"Candidat {candidate_id} introuvable.")
    if row["statut"] != expected:
        raise ValueError(f"Ce candidat est déjà « {row['statut']} ».")
    return row


def _set_status(conn, row, statut: str, competitor_id: int | None = None) -> None:
    conn.execute("UPDATE competitor_candidates SET statut = ?, competitor_id = ?,"
                 " decide_le = CASE WHEN ? = 'propose' THEN NULL ELSE datetime('now') END WHERE id = ?",
                 (statut, competitor_id, statut, row["id"]))
    conn.execute("INSERT INTO audit_history (table_nom, ligne_id, champ, ancienne_valeur,"
                 " nouvelle_valeur) VALUES ('competitor_candidates', ?, 'statut', ?, ?)",
                 (row["id"], row["statut"], statut))


def accept_candidate(conn, candidate_id: int, nom: str | None = None) -> int:
    """Crée le concurrent (source tracée) après validation humaine ; renvoie son identifiant."""
    row = _candidate(conn, candidate_id, "propose")
    source = f"{row['fournisseur']} ; thème : {row['requetes']} ; accepté par l'utilisateur"
    competitor_id = repo.add_competitor(  # ValueError si le domaine existe déjà
        conn, row["domaine"], nom or row["nom"], row["url_newsletter"], row["description"],
        source_info=source)
    with conn:
        _set_status(conn, row, "accepte", competitor_id)
    return competitor_id


def accept_all(conn) -> tuple[int, list[str]]:
    """Accepte tous les candidats en attente (action volontaire de l'utilisateur).

    Renvoie (acceptés, messages d'erreur) : un candidat en échec n'arrête pas les autres."""
    accepted, errors = 0, []
    for c in list_candidates(conn, "propose"):
        try:
            accept_candidate(conn, c["id"])
            accepted += 1
        except ValueError as exc:
            errors.append(f"{c['domaine']} : {exc}")
    return accepted, errors


def reject_candidate(conn, candidate_id: int) -> None:
    row = _candidate(conn, candidate_id, "propose")
    with conn:
        _set_status(conn, row, "rejete")


def restore_candidate(conn, candidate_id: int) -> None:
    """Remet un candidat rejeté dans la file (aucune donnée perdue)."""
    row = _candidate(conn, candidate_id, "rejete")
    with conn:
        _set_status(conn, row, "propose")
