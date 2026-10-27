"""Import de newsletters (.eml / .html) dans SQLite, sans doublons.

Un fichier en erreur est consigné dans import_log et n'interrompt jamais le lot.
Un doublon est détecté par Message-ID (si présent) ou par hash du texte normalisé.
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.db.domains import registrable_domain
from app.detection.service import analyze_edition
from app.parsing.newsletter import ParsedEdition, parse_file_content

DEFAULT_ARCHIVE_DIR = Path("data") / "editions"
_TABLES = {"own": "own_publications", "competitor": "competitors"}


@dataclass
class ImportResult:
    fichier: str
    statut: str  # 'ok', 'doublon' ou 'erreur'
    message: str
    edition_id: int | None = None


def _content_hash(parsed: ParsedEdition, raw: bytes) -> str:
    """Hash du texte normalisé (insensible aux liens de suivi propres à chaque destinataire)."""
    text = re.sub(r"\s+", " ", parsed.texte or "").strip().lower()
    return hashlib.sha256(text.encode() if text else raw).hexdigest()


def _ignored_domains(conn, publication_domain: str) -> set[str]:
    """Domaines qui ne peuvent pas être annonceurs : vos publications et la publication importée."""
    domains = {r["domaine"] for r in conn.execute("SELECT domaine FROM own_publications")}
    domains.add(publication_domain)
    return {registrable_domain(d) for d in domains}


def _log(conn, fichier: str, statut: str, message: str, edition_id=None) -> ImportResult:
    with conn:
        conn.execute(
            "INSERT INTO import_log (fichier, statut, message, edition_id) VALUES (?, ?, ?, ?)",
            (fichier, statut, message, edition_id),
        )
    return ImportResult(fichier, statut, message, edition_id)


def _find_duplicate(conn, ptype, pid, parsed, content_hash) -> int | None:
    if parsed.message_id:
        row = conn.execute(
            "SELECT id FROM editions WHERE publication_type = ? AND publication_id = ?"
            " AND message_id = ?", (ptype, pid, parsed.message_id)).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        "SELECT id FROM editions WHERE publication_type = ? AND publication_id = ?"
        " AND hash_contenu = ?", (ptype, pid, content_hash)).fetchone()
    return row["id"] if row else None


def _store(conn, ptype, pid, filename, parsed, content_hash, archive_path) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO editions (publication_type, publication_id, objet, objet_source,"
            " expediteur, expediteur_source, date_envoi, date_statut, date_source, texte,"
            " message_id, hash_contenu, fichier_source, format_source, archive_chemin)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ptype, pid, parsed.objet, parsed.objet_source, parsed.expediteur,
             parsed.expediteur_source, parsed.date_envoi,
             "source" if parsed.date_envoi else "inconnue", parsed.date_source, parsed.texte,
             parsed.message_id, content_hash, filename, parsed.format_source,
             str(archive_path) if archive_path else None))
        edition_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO edition_headings (edition_id, niveau, texte, position) VALUES (?, ?, ?, ?)",
            [(edition_id, h.niveau, h.texte, h.position) for h in parsed.headings])
        conn.executemany(
            "INSERT INTO edition_links (edition_id, url, domaine, ancre, a_suivi, position)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(edition_id, l.url, l.domaine, l.ancre, int(l.a_suivi), l.position) for l in parsed.links])
        conn.executemany(
            "INSERT INTO edition_images (edition_id, src, alt, largeur, hauteur, lien_url, position)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(edition_id, i.src, i.alt, i.largeur, i.hauteur, i.lien_url, i.position) for i in parsed.images])
    return edition_id


def import_bytes(conn: sqlite3.Connection, filename: str, data: bytes, publication_type: str,
                 publication_id: int, archive_dir: Path | str = DEFAULT_ARCHIVE_DIR) -> ImportResult:
    """Importe un fichier en mémoire. Ne lève jamais d'exception liée au contenu."""
    try:
        if publication_type not in _TABLES:
            raise ValueError(f"Type de publication inconnu : {publication_type!r}")
        found = conn.execute(f"SELECT domaine FROM {_TABLES[publication_type]} WHERE id = ?",
                             (publication_id,)).fetchone()
        if not found:
            raise LookupError(f"Publication {publication_type}#{publication_id} introuvable.")

        parsed = parse_file_content(filename, data)
        content_hash = _content_hash(parsed, data)
        duplicate = _find_duplicate(conn, publication_type, publication_id, parsed, content_hash)
        if duplicate:
            return _log(conn, filename, "doublon",
                        f"Déjà importée (édition n°{duplicate}), ignorée.", duplicate)

        # Archivage de l'original, nommé d'après le hash : l'écriture est idempotente.
        archive_dir = Path(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{content_hash}.{parsed.format_source}"
        archive_path.write_bytes(data)

        edition_id = _store(conn, publication_type, publication_id, filename, parsed,
                            content_hash, archive_path)
        message = "Importée."
        try:  # une analyse en échec ne remet pas en cause l'import déjà enregistré
            ignored = _ignored_domains(conn, found["domaine"])
            summary = analyze_edition(conn, edition_id, parsed, ignored)
            message += (f" Analyse : {summary.blocs} bloc(s) publicitaire(s),"
                        f" {summary.detections} détection(s), {summary.mentions} mention(s).")
        except Exception as exc:
            message += f" Analyse publicitaire en échec ({type(exc).__name__}: {exc})."
        return _log(conn, filename, "ok", message, edition_id)
    except Exception as exc:  # une erreur d'import ne doit pas arrêter l'application
        return _log(conn, filename, "erreur", f"{type(exc).__name__}: {exc}")


def import_file(conn, path: Path | str, publication_type: str, publication_id: int,
                archive_dir: Path | str = DEFAULT_ARCHIVE_DIR) -> ImportResult:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return _log(conn, path.name, "erreur", f"Lecture impossible : {exc}")
    return import_bytes(conn, path.name, data, publication_type, publication_id, archive_dir)


def import_many(conn, paths, publication_type: str, publication_id: int,
                archive_dir: Path | str = DEFAULT_ARCHIVE_DIR) -> list[ImportResult]:
    return [import_file(conn, p, publication_type, publication_id, archive_dir) for p in paths]
