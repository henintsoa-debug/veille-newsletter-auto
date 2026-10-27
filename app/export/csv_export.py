"""Exports CSV lisibles dans Excel (français) : UTF-8 avec BOM, séparateur « ; ».

Les valeurs absentes sont écrites « Inconnu » (jamais laissées vides ni devinées).
Les textes qui commencent par = + - @ sont préfixés d'une apostrophe : Excel ne les
exécute pas comme des formules (contenu issu de newsletters, donc non fiable).
"""

import csv
import io

from app.ui import queries as q
from app.ui.labels import FORCES, INCONNU, NIVEAUX, STATUTS, TYPES_BLOC, day, fr

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    if value is None or value == "":
        return INCONNU
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def to_csv_bytes(columns: dict[str, str], rows: list[dict]) -> bytes:
    """columns : {clé: en-tête français}. Renvoie le CSV (BOM UTF-8, ';', fin de ligne CRLF)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(columns.values())
    for row in rows:
        writer.writerow([_cell(row.get(key)) for key in columns])
    return buffer.getvalue().encode("utf-8-sig")


DETECTION_COLUMNS = {
    "date": "Date", "publication": "Publication", "objet": "Objet de l'édition",
    "annonceur": "Annonceur", "domaine": "Domaine", "niveau_fr": "Niveau de confiance",
    "score": "Score", "type_fr": "Type de bloc", "statut_fr": "Validation"}
PREUVE_COLUMNS = {
    "date": "Date", "publication": "Publication", "objet": "Objet de l'édition",
    "annonceur": "Annonceur", "niveau_fr": "Niveau de confiance", "statut_fr": "Validation",
    "regle": "Règle déclenchée", "force_fr": "Force", "poids": "Poids",
    "extrait": "Extrait exact de la source", "emplacement": "Emplacement"}
EDITION_COLUMNS = {
    "date": "Date", "date_source": "Source de la date", "publication": "Publication",
    "objet": "Objet", "objet_source": "Source de l'objet", "expediteur": "Expéditeur",
    "expediteur_source": "Source de l'expéditeur", "format_source": "Format",
    "fichier_source": "Fichier importé", "nb_detections": "Détections"}
ANNONCEUR_COLUMNS = {
    "nom": "Annonceur", "domaine": "Domaine", "nb_editions": "Éditions",
    "nb_detections": "Détections", "publications": "Publications", "premiere": "Première vue",
    "derniere": "Dernière vue", "recurrent": "Récurrent", "nouveaute": "Nouveauté",
    "sans_date": "Éditions sans date"}


def _decorate(row: dict) -> dict:
    row = dict(row)
    row["date"] = day(row.get("date_envoi"))
    row["niveau_fr"] = fr(row.get("niveau"), NIVEAUX)
    row["statut_fr"] = fr(row.get("statut_validation"), STATUTS)
    row["type_fr"] = fr(row.get("type_bloc"), TYPES_BLOC) if row.get("type_bloc") else "Mention hors bloc publicitaire"
    return row


def export_detections(conn, f: q.Filters) -> bytes:
    return to_csv_bytes(DETECTION_COLUMNS, [_decorate(r) for r in q.detections_table(conn, f)])


def export_preuves(conn, f: q.Filters) -> bytes:
    """Une ligne par preuve : permet de contrôler chaque détection dans Excel."""
    rows = []
    for det in q.detections_table(conn, f):
        det = _decorate(det)
        proofs = conn.execute(
            "SELECT regle, force, poids, extrait, emplacement FROM evidences"
            " WHERE detection_id = ? ORDER BY poids DESC, id", (det["detection_id"],)).fetchall()
        for p in proofs:
            rows.append({**det, **dict(p), "force_fr": fr(p["force"], FORCES)})
    return to_csv_bytes(PREUVE_COLUMNS, rows)


def export_editions(conn, f: q.Filters) -> bytes:
    rows = [{**r, "date": day(r["date_envoi"])} for r in q.editions_table(conn, f)]
    return to_csv_bytes(EDITION_COLUMNS, rows)


def export_annonceurs(conn, f: q.Filters) -> bytes:
    return to_csv_bytes(ANNONCEUR_COLUMNS, q.stat_rows(q.advertiser_stats_filtered(conn, f)))
