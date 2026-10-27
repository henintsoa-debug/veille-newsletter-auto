"""Tableau de bord Veille Newsletter Auto (lancement : voir lancer.bat)."""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # racine du projet (Streamlit ne l'ajoute pas)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.advertisers.repository import set_validation  # noqa: E402
from app.advertisers.stats import advertiser_history, advertiser_stats  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.connection import DEFAULT_DB_PATH, connect  # noqa: E402
from app.discovery import service as disc  # noqa: E402
from app.discovery import list_import  # noqa: E402
from app.discovery.service import KnownDomainError  # noqa: E402
from app.export.csv_export import (  # noqa: E402
    export_annonceurs, export_detections, export_editions, export_preuves)
from app.ingestion.importer import import_bytes  # noqa: E402
from app.ui import queries as q  # noqa: E402
from app.ui.labels import FORCES, NIVEAUX, STATUTS, TYPES_BLOC, day, fr  # noqa: E402


@st.cache_resource
def get_conn(path: str):
    conn = connect(path, check_same_thread=False)
    repo.seed_own_publications(conn)  # vos 4 newsletters, sans écraser ce que vous avez modifié
    for path in sorted((Path(__file__).resolve().parents[2] / "config").glob("candidats_*.json")):
        disc.load_curated(conn, path)  # candidats vérifiés : proposés seulement, jamais ajoutés d'office
    return conn


def flash(message: str, kind: str = "success") -> None:
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    kind, message = st.session_state.pop("flash", (None, None))
    if message:
        getattr(st, kind)(message)


def table(rows: list[dict], columns: dict[str, str], empty: str = "Aucune donnée pour ces filtres.") -> None:
    """Affiche un tableau avec en-têtes français ; les valeurs absentes deviennent « Inconnu »."""
    if not rows:
        st.info(empty)
        return
    df = pd.DataFrame([{label: row.get(key) for key, label in columns.items()} for row in rows])
    st.dataframe(df.fillna("Inconnu"), width="stretch", hide_index=True)


def prepare(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        r["date"] = day(r.get("date_envoi"))
        r["niveau_fr"] = fr(r.get("niveau"), NIVEAUX)
        r["statut_fr"] = fr(r.get("statut_validation"), STATUTS)
        r["type_fr"] = fr(r.get("type_bloc"), TYPES_BLOC)
        out.append(r)
    return out


def preuves_table(preuves: list[dict]) -> None:
    rows = [{**p, "force_fr": fr(p["force"], FORCES)} for p in preuves]
    table(rows, {"regle": "Règle", "force_fr": "Force", "poids": "Poids", "extrait": "Extrait exact",
                 "emplacement": "Emplacement"}, "Aucune preuve enregistrée.")


# --- filtres globaux ----------------------------------------------------------

def sidebar_filters(conn) -> q.Filters:
    st.sidebar.header("Filtres")
    pubs = q.list_publications(conn)
    pub = st.sidebar.selectbox("Publication / concurrent", [None] + pubs,
                               format_func=lambda p: "Toutes" if p is None else p["label"])
    f = q.Filters()
    if pub:
        f.publication_type, f.publication_id = pub["type"], pub["id"]
    if st.sidebar.checkbox("Filtrer par période"):
        start = st.sidebar.date_input("Du", value=date.today() - timedelta(days=90))
        end = st.sidebar.date_input("Au", value=date.today())
        f.date_debut, f.date_fin = start.isoformat(), end.isoformat()
    advs = q.list_advertisers(conn)
    adv = st.sidebar.selectbox("Annonceur", [None] + list(advs),
                               format_func=lambda a: "Tous" if a is None else
                               f"{a['nom']} ({a['domaine']})" if a["domaine"] else a["nom"])
    if adv:
        f.advertiser_id = adv["id"]
    f.niveaux = tuple(st.sidebar.multiselect(
        "Niveau de confiance", list(NIVEAUX), default=list(q.ALL_NIVEAUX), format_func=NIVEAUX.get))
    if st.sidebar.checkbox("Afficher aussi les détections rejetées"):
        f.statuts = ("a_valider", "validee", "rejetee")
    if not f.niveaux:
        st.sidebar.warning("Aucun niveau sélectionné : rien ne sera affiché.")
    return f


# --- pages ----------------------------------------------------------------------

def page_overview(conn, f: q.Filters) -> None:
    st.header("Vue d'ensemble")
    o = q.overview(conn, f)
    cols = st.columns(5)
    cols[0].metric("Éditions", o["editions"], help="Éditions archivées")
    cols[1].metric("Annonceurs", o["annonceurs"], help="Annonceurs détectés")
    cols[2].metric("Récurrents", o["recurrents"], help="Annonceurs présents dans au moins 3 éditions")
    cols[3].metric("Nouveaux", o["nouveaux"], help="Annonceurs vus pour la première fois dans la période")
    cols[4].metric("À valider", o["a_valider"], help="Détections en attente de validation humaine")
    if o["editions_sans_date"]:
        st.caption(f"{o['editions_sans_date']} édition(s) sans date connue : non placées dans les graphiques.")
    if o["editions_par_mois"]:
        st.subheader("Éditions par mois")
        df = pd.DataFrame(o["editions_par_mois"]).set_index("mois")
        st.bar_chart(df["n"])
    st.subheader("Annonceurs les plus présents")
    page_advertiser_table(conn, f, with_history=False)


def page_detections(conn, f: q.Filters) -> None:
    st.header("Détections publicitaires")
    rows = prepare(q.detections_table(conn, f))
    excluded = q.undated_excluded(conn, f)
    if excluded:
        st.caption(f"{excluded} édition(s) sans date connue exclue(s) car une période est sélectionnée.")
    st.caption(f"{len(rows)} détection(s).")
    table(rows, {"date": "Date", "publication": "Publication", "objet": "Objet de l'édition",
                 "annonceur": "Annonceur", "domaine": "Domaine", "niveau_fr": "Niveau",
                 "score": "Score", "type_fr": "Type", "statut_fr": "Validation"})


def page_editions(conn, f: q.Filters) -> None:
    st.header("Éditions")
    rows = prepare(q.editions_table(conn, f))
    excluded = q.undated_excluded(conn, f)
    if excluded:
        st.caption(f"{excluded} édition(s) sans date connue exclue(s) car une période est sélectionnée.")
    table(rows, {"date": "Date", "publication": "Publication", "objet": "Objet", "expediteur": "Expéditeur",
                 "format_source": "Format", "nb_detections": "Détections"})
    if not rows:
        return
    choice = st.selectbox("Voir le détail d'une édition", rows, format_func=lambda r: (
        f"{r['date']} · {r['publication']} · {fr(r['objet'])}"))
    d = q.edition_detail(conn, choice["edition_id"])
    ed = d["edition"]
    st.subheader(fr(ed["objet"]))
    st.markdown(
        f"- **Publication** : {fr(ed['publication'])}\n"
        f"- **Objet** : {fr(ed['objet'])} (source : {fr(ed['objet_source'])})\n"
        f"- **Expéditeur** : {fr(ed['expediteur'])} (source : {fr(ed['expediteur_source'])})\n"
        f"- **Date** : {day(ed['date_envoi'])} (source : {fr(ed['date_source'])})\n"
        f"- **Fichier importé** : {ed['fichier_source']} ({ed['format_source']}) · "
        f"{d['nb_liens']} lien(s) · {d['nb_images']} image(s)")
    if d["titres"]:
        with st.expander(f"Titres ({len(d['titres'])})"):
            for t in d["titres"]:
                st.write(f"{'  ' * (t['niveau'] - 1)}• {t['texte']}")
    st.subheader("Détections de cette édition")
    if not d["detections"]:
        st.info("Aucune détection publicitaire dans cette édition.")
    for det in d["detections"]:
        title = (f"{det['annonceur']} · {fr(det['niveau'], NIVEAUX)} · score {det['score']}"
                 f" · {fr(det['statut_validation'], STATUTS)}")
        with st.expander(title):
            st.write(f"Type : {fr(det['type_bloc'], TYPES_BLOC) if det['type_bloc'] else 'Mention hors bloc publicitaire'}")
            if det["extrait_texte"]:
                st.caption("Extrait du bloc : " + det["extrait_texte"])
            preuves_table(det["preuves"])


def page_advertiser_table(conn, f: q.Filters, with_history: bool = True) -> None:
    stats = q.advertiser_stats_filtered(conn, f)
    table(q.stat_rows(stats), {"nom": "Annonceur", "domaine": "Domaine", "nb_editions": "Éditions",
                 "nb_detections": "Détections", "publications": "Publications",
                 "premiere": "Première vue", "derniere": "Dernière vue", "recurrent": "Récurrent",
                 "nouveaute": "Nouveauté", "sans_date": "Éditions sans date"})
    if not (with_history and stats):
        return
    st.caption("Seules les détections confirmées/potentielles non rejetées sont comptées ; "
               "« Indéterminé » signifie qu'une date inconnue empêche de conclure.")
    choice = st.selectbox("Historique d'un annonceur", stats, format_func=lambda s: s.nom)
    hist = prepare([dict(h) for h in advertiser_history(conn, choice.advertiser_id)])
    table(hist, {"date": "Date", "publication_nom": "Publication", "objet": "Objet de l'édition",
                 "niveau_fr": "Niveau", "score": "Score", "type_fr": "Type", "statut_fr": "Validation"})


def page_advertisers(conn, f: q.Filters) -> None:
    st.header("Annonceurs")
    page_advertiser_table(conn, f)


def page_validation(conn, f: q.Filters) -> None:
    st.header("À valider")
    st.write("Vérifiez chaque détection avec ses preuves. Valider ou rejeter ne modifie pas le niveau "
             "calculé ; la décision est enregistrée et historisée.")
    items, total = q.pending_validation(conn)
    if not items:
        st.success("Aucune détection en attente de validation.")
        return
    st.caption(f"{total} détection(s) en attente ; les {len(items)} plus prioritaires sont affichées.")
    for it in items:
        title = (f"{it['annonceur']} · {fr(it['niveau'], NIVEAUX)} (score {it['score']}) · "
                 f"{fr(it['publication'])} · {day(it['date_envoi'])}")
        with st.expander(title):
            st.write(f"Édition : {fr(it['objet'])} · Type : {fr(it['type_bloc'], TYPES_BLOC)}")
            if it["extrait_texte"]:
                st.caption("Extrait du bloc : " + it["extrait_texte"])
            preuves_table(it["preuves"])
            c1, c2, _ = st.columns([1, 1, 4])
            for col, statut, label in ((c1, "validee", "Valider"), (c2, "rejetee", "Rejeter")):
                if col.button(label, key=f"{statut}_{it['detection_id']}"):
                    set_validation(conn, it["detection_id"], statut)
                    flash(f"Détection {it['annonceur']} : {STATUTS[statut].lower()}.")
                    st.rerun()


def page_compare(conn, f: q.Filters) -> None:
    st.header("Comparatif : mes newsletters et les concurrents")
    cmp = q.compare_own_vs_competitors(conn, f)
    cols = {"annonceur": "Annonceur", "domaine": "Domaine", "editions_chez_moi": "Éditions chez moi",
            "editions_chez_concurrents": "Éditions chez les concurrents", "concurrents_txt": "Concurrents"}
    for key, title, hint in (
            ("concurrents_seulement", "Chez les concurrents seulement", "Annonceurs que vous n'avez pas."),
            ("communs", "Communs", "Annonceurs présents des deux côtés."),
            ("chez_moi_seulement", "Chez moi seulement", "Annonceurs absents chez les concurrents suivis.")):
        st.subheader(title)
        st.caption(hint)
        rows = [{**r, "concurrents_txt": ", ".join(r["concurrents"])} for r in cmp[key]]
        table(rows, cols, "Aucun annonceur dans cette catégorie.")


def page_export(conn, f: q.Filters) -> None:
    st.header("Export CSV")
    st.write("Les exports respectent les filtres de la barre latérale. Format : UTF-8 avec BOM et "
             "séparateur « ; », à ouvrir directement dans Excel (français). Les valeurs absentes "
             "sont écrites « Inconnu ».")
    excluded = q.undated_excluded(conn, f)
    if excluded:
        st.caption(f"{excluded} édition(s) sans date connue ne figurent pas dans les exports "
                   "car une période est sélectionnée.")
    exports = (
        ("Détections", "detections.csv", export_detections, "Une ligne par annonceur détecté."),
        ("Preuves", "preuves.csv", export_preuves,
         "Une ligne par preuve : règle, extrait exact de la source, emplacement."),
        ("Éditions", "editions.csv", export_editions, "Une ligne par édition, avec la source de chaque donnée."),
        ("Annonceurs", "annonceurs.csv", export_annonceurs, "Récurrence, nouveauté, première et dernière vue."),
    )
    for label, filename, build, hint in exports:
        col1, col2 = st.columns([1, 3])
        col1.download_button(f"Télécharger : {label}", build(conn, f), file_name=filename,
                             mime="text/csv", key=f"dl_{filename}")
        col2.caption(hint)


def page_import(conn, f: q.Filters) -> None:
    st.header("Importer des newsletters")
    st.write("Importez des fichiers `.eml` ou `.html` reçus légitimement. Les doublons sont ignorés ; "
             "un fichier en erreur n'arrête pas les autres.")
    pubs = [p for p in q.list_publications(conn) if p["actif"]]
    pub = st.selectbox("Rattacher à", pubs, format_func=lambda p: p["label"])
    files = st.file_uploader("Fichiers", type=["eml", "html", "htm"], accept_multiple_files=True)
    if st.button("Importer", disabled=not (files and pub)):
        results = [import_bytes(conn, fl.name, fl.getvalue(), pub["type"], pub["id"]) for fl in files]
        labels = {"ok": "Importée", "doublon": "Doublon ignoré", "erreur": "Erreur"}
        table([{"fichier": r.fichier, "statut": labels[r.statut], "message": r.message} for r in results],
              {"fichier": "Fichier", "statut": "Résultat", "message": "Détail"})
    with st.expander("Derniers imports"):
        rows = [dict(r) for r in conn.execute(
            "SELECT fichier, statut, message, date_import FROM import_log ORDER BY id DESC LIMIT 30")]
        table(rows, {"date_import": "Date", "fichier": "Fichier", "statut": "Statut", "message": "Détail"},
              "Aucun import pour l'instant.")


def _edit_form(conn, kind: str) -> None:
    own = kind == "own"
    rows = [dict(r) for r in (repo.list_own_publications(conn) if own else repo.list_competitors(conn))]
    if not rows:
        return
    row = st.selectbox("Modifier", rows, format_func=lambda r: f"{r['nom']} ({r['domaine']})", key=f"sel_{kind}")
    with st.form(f"edit_{kind}_{row['id']}"):
        nom = st.text_input("Nom", row["nom"])
        url = st.text_input("URL de la newsletter", row["url_newsletter"] or "")
        extra_label = "Thématique" if own else "Description"
        extra_key = "thematique" if own else "description"
        extra = st.text_input(extra_label, row[extra_key] or "")
        actif = st.checkbox("Actif", bool(row["actif"]))
        st.caption("Rien n'est supprimé : désactivez un élément pour le masquer des imports.")
        if st.form_submit_button("Enregistrer"):
            try:
                update = repo.update_own_publication if own else repo.update_competitor
                update(conn, row["id"], nom=nom, url_newsletter=url, actif=actif, **{extra_key: extra})
                flash("Modifications enregistrées.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _add_form(conn, kind: str) -> None:
    own = kind == "own"
    with st.form(f"add_{kind}", clear_on_submit=True):
        domaine = st.text_input("Domaine ou URL (obligatoire)")
        nom = st.text_input("Nom (facultatif)")
        url = st.text_input("URL de la newsletter (facultatif)")
        extra = st.text_input("Thématique (facultatif)" if own else "Description (facultatif)")
        if st.form_submit_button("Ajouter"):
            try:
                if own:
                    repo.add_own_publication(conn, domaine, nom, url, extra)
                else:
                    repo.add_competitor(conn, domaine, nom, url, extra)
                flash("Ajouté.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _propose_form(conn) -> None:
    with st.form("propose_site", clear_on_submit=True):
        domaine = st.text_input("Domaine ou URL (obligatoire)")
        nom = st.text_input("Nom (facultatif)")
        url = st.text_input("URL de la newsletter (facultatif)")
        description = st.text_input("Description (facultatif)")
        theme = st.text_input("Thème (facultatif)")
        if st.form_submit_button("Proposer ce site"):
            try:
                disc.add_candidate(conn, domaine, nom, url, description, theme)
                flash("Site ajouté aux candidats.")
                st.rerun()
            except KnownDomainError as exc:
                st.warning(str(exc))
            except ValueError as exc:
                st.error(str(exc))


def _import_list(conn) -> None:
    st.write("Importez une liste de sites : **CSV** (première ligne `domaine;nom;url_newsletter;description;theme`, "
             "seul `domaine` est obligatoire) ou **TXT** (un domaine ou une URL par ligne). Les sites arrivent "
             "comme candidats : vous les acceptez ensuite. Une ligne en erreur n'arrête pas les autres.")
    st.download_button("Télécharger le modèle CSV", list_import.TEMPLATE_CSV,
                       file_name="modele_import_concurrents.csv", mime="text/csv")
    files = st.file_uploader("Fichiers", type=["csv", "txt"], accept_multiple_files=True, key="disc_files")
    if st.button("Importer la liste", disabled=not files):
        labels = {"ajoute": "Ajouté", "deja_connu": "Déjà connu", "erreur": "Erreur"}
        for fl in files:
            report = list_import.import_candidates(conn, fl.name, fl.getvalue())
            st.write(f"**{fl.name}** : {report.ajoutes} ajouté(s), {report.deja_connus} déjà connu(s), "
                     f"{report.erreurs} erreur(s).")
            table([{"ligne": l.ligne or "", "domaine": l.domaine, "statut": labels[l.statut], "message": l.message}
                   for l in report.lignes],
                  {"ligne": "Ligne", "domaine": "Domaine", "statut": "Résultat", "message": "Détail"})


def page_discovery(conn, f: q.Filters) -> None:
    st.header("Découverte de concurrents")
    st.write("Les candidats viennent de recherches vérifiées sur pages publiques, d'un site que vous proposez "
             "ou d'une liste que vous importez. **Rien n'est ajouté aux concurrents sans votre accord.**")
    with st.expander("Proposer un site"):
        _propose_form(conn)
    with st.expander("Importer une liste (CSV ou TXT)"):
        _import_list(conn)

    st.subheader("Candidats proposés")
    candidates = disc.list_candidates(conn, "propose")
    if not candidates:
        st.info("Aucun candidat en attente.")
    if len(candidates) > 1 and st.button(f"Tout accepter ({len(candidates)} candidats)"):
        accepted, errors = disc.accept_all(conn)
        flash(f"{accepted} concurrent(s) ajouté(s)." + (f" Non ajoutés : {' ; '.join(errors)}" if errors else ""),
              "warning" if errors else "success")
        st.rerun()
    for c in candidates:
        with st.expander(f"{c['nom']} ({c['domaine']})"):
            st.write(f"**Description** : {fr(c['description'])}")
            st.write(f"**Page** : {c['url_trouvee']}")
            st.write(f"**URL de newsletter** : {fr(c['url_newsletter'])}")
            st.write(f"**Thème** : {c['requetes']}")
            st.caption(f"Source : {c['fournisseur']}. Pas de score de proximité.")
            table(c["signaux"], {"regle": "Constat", "extrait": "Extrait", "source": "Source"},
                  "Aucun constat enregistré.")
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Accepter", key=f"acc_{c['id']}"):
                try:
                    disc.accept_candidate(conn, c["id"])
                    flash(f"{c['domaine']} ajouté aux concurrents.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if c2.button("Rejeter", key=f"rej_{c['id']}"):
                disc.reject_candidate(conn, c["id"])
                flash(f"{c['domaine']} rejeté : il ne sera plus proposé.")
                st.rerun()
    rejected = disc.list_candidates(conn, "rejete")
    if rejected:
        with st.expander(f"Candidats rejetés ({len(rejected)})"):
            for c in rejected:
                st.write(c["domaine"])
                if st.button("Remettre dans la file", key=f"res_{c['id']}"):
                    disc.restore_candidate(conn, c["id"])
                    st.rerun()


def page_competitors(conn, f: q.Filters) -> None:
    st.header("Concurrents et mes newsletters")
    st.subheader("Mes newsletters")
    own_rows = [dict(r, actif_txt="Oui" if r["actif"] else "Non") for r in repo.list_own_publications(conn)]
    table(own_rows, {"nom": "Nom", "domaine": "Domaine", "url_newsletter": "URL newsletter",
                     "thematique": "Thématique", "actif_txt": "Actif"})
    with st.expander("Ajouter une de mes newsletters"):
        _add_form(conn, "own")
    with st.expander("Modifier une de mes newsletters"):
        _edit_form(conn, "own")
    st.subheader("Concurrents")
    rows = [dict(r, actif_txt="Oui" if r["actif"] else "Non") for r in repo.list_competitors(conn)]
    table(rows, {"nom": "Nom", "domaine": "Domaine", "url_newsletter": "URL newsletter",
                 "description": "Description", "actif_txt": "Actif", "source_info": "Source"},
          "Aucun concurrent pour l'instant. Ajoutez-en un ci-dessous.")
    with st.expander("Ajouter un concurrent"):
        _add_form(conn, "competitor")
    with st.expander("Modifier un concurrent"):
        _edit_form(conn, "competitor")


PAGES = {
    "Vue d'ensemble": page_overview,
    "Détections": page_detections,
    "Éditions": page_editions,
    "Annonceurs": page_advertisers,
    "À valider": page_validation,
    "Comparatif": page_compare,
    "Export": page_export,
    "Import": page_import,
    "Découverte": page_discovery,
    "Concurrents": page_competitors,
}


def main() -> None:
    st.set_page_config(page_title="Veille Newsletter Auto", page_icon="📰", layout="wide")
    conn = get_conn(os.environ.get("VEILLE_DB", str(DEFAULT_DB_PATH)))
    st.title("Veille Newsletter Auto")
    page = st.sidebar.radio("Navigation", list(PAGES))
    filters = sidebar_filters(conn)
    show_flash()
    PAGES[page](conn, filters)


main()

