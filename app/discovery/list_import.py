"""Import d'une liste de sites (CSV ou TXT) comme candidats concurrents « à valider ».

CSV : une ligne d'en-tête avec au moins une colonne de domaine ; colonnes facultatives : nom,
url_newsletter, description, theme (séparateur « ; » ou « , », typique d'un export Excel).
TXT : un domaine ou une URL par ligne (les lignes vides et celles qui commencent par # sont ignorées).
Une ligne en erreur n'arrête jamais l'import ; chaque ligne reçoit son résultat.
"""

import csv
import io
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field

from app.discovery.service import KnownDomainError, add_candidate, load_platforms

MAX_BYTES = 2_000_000
MAX_ROWS = 2000
TEMPLATE_CSV = "domaine;nom;url_newsletter;description;theme\r\n".encode("utf-8-sig")

_ALIASES = {
    "domaine": {"domaine", "domain", "site", "siteweb", "url", "adresse"},
    "nom": {"nom", "name", "titre", "newsletter"},
    "url_newsletter": {"urlnewsletter", "lienpage", "urlinscription", "pagenewsletter"},
    "description": {"description", "desc", "commentaire"},
    "theme": {"theme", "thematique", "categorie"},
}


@dataclass
class ImportLine:
    ligne: int
    domaine: str | None
    statut: str  # 'ajoute', 'deja_connu' ou 'erreur'
    message: str


@dataclass
class ImportReport:
    fichier: str
    lignes: list[ImportLine] = field(default_factory=list)

    def count(self, statut: str) -> int:
        return sum(1 for line in self.lignes if line.statut == statut)

    @property
    def ajoutes(self) -> int:
        return self.count("ajoute")

    @property
    def deja_connus(self) -> int:
        return self.count("deja_connu")

    @property
    def erreurs(self) -> int:
        return self.count("erreur")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # export Excel en ANSI


def _key(header: str) -> str:
    text = unicodedata.normalize("NFKD", header.strip().casefold())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)))


def _rows_from_csv(text: str) -> tuple[list[tuple[int, dict]], str | None]:
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = ";" if first.count(";") >= first.count(",") and ";" in first else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = next(reader, None)
    while header is not None and not any(c.strip() for c in header):
        header = next(reader, None)
    if header is None:
        return [], "Fichier vide."
    columns = {}
    for index, name in enumerate(header):
        for canonical, aliases in _ALIASES.items():
            if _key(name) in aliases and canonical not in columns:
                columns[canonical] = index
    if "domaine" not in columns:
        return [], ("Colonne de domaine introuvable. Première ligne attendue : "
                    "domaine;nom;url_newsletter;description;theme (seul « domaine » est obligatoire).")
    rows = []
    for number, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        rows.append((number, {k: (cells[i].strip() if i < len(cells) else "") for k, i in columns.items()}))
    return rows, None


def _rows_from_txt(text: str) -> list[tuple[int, dict]]:
    return [(n, {"domaine": line.strip()}) for n, line in enumerate(text.splitlines(), start=1)
            if line.strip() and not line.strip().startswith("#")]


def import_candidates(conn: sqlite3.Connection, filename: str, data: bytes) -> ImportReport:
    """Lit la liste et propose chaque site comme candidat (jamais comme concurrent)."""
    report = ImportReport(filename)
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if len(data) > MAX_BYTES:
        report.lignes.append(ImportLine(0, None, "erreur", "Fichier trop volumineux (2 Mo maximum)."))
        return report
    if suffix not in ("csv", "txt"):
        report.lignes.append(ImportLine(0, None, "erreur",
                                        f"Format non pris en charge : .{suffix or '?'} (attendu : .csv ou .txt)."))
        return report
    text = _decode(data)
    if suffix == "csv":
        rows, problem = _rows_from_csv(text)
        if problem:
            report.lignes.append(ImportLine(0, None, "erreur", problem))
            return report
    else:
        rows = _rows_from_txt(text)
    if len(rows) > MAX_ROWS:
        report.lignes.append(ImportLine(0, None, "erreur",
                                        f"Trop de lignes ({len(rows)}) : {MAX_ROWS} maximum par fichier."))
        return report

    platforms = load_platforms()
    for number, row in rows:
        raw = row.get("domaine", "")
        try:
            add_candidate(
                conn, raw, row.get("nom"), row.get("url_newsletter"), row.get("description"),
                row.get("theme") or "Liste importée par l'utilisateur", f"Import : {filename}",
                [("liste_fournie_par_l_utilisateur", f"Ligne {number} : {raw}", filename)], platforms)
            report.lignes.append(ImportLine(number, raw, "ajoute", "Ajouté aux candidats."))
        except KnownDomainError as exc:
            report.lignes.append(ImportLine(number, raw, "deja_connu", str(exc)))
        except ValueError as exc:
            report.lignes.append(ImportLine(number, raw, "erreur", str(exc)))
        except Exception as exc:  # une ligne ne doit jamais arrêter l'import
            report.lignes.append(ImportLine(number, raw, "erreur", f"{type(exc).__name__}: {exc}"))
    return report
