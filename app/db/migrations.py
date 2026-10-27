"""Migrations du schéma SQLite, suivies via PRAGMA user_version.

Règle : on ajoute des migrations à la fin de la liste, on ne modifie jamais
une migration déjà appliquée (aucune donnée existante n'est détruite).
"""

import sqlite3

MIGRATIONS: list[str] = [
    # 1 : publications de l'utilisateur, concurrents, historique des changements
    """
    CREATE TABLE own_publications (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        nom            TEXT NOT NULL,
        domaine        TEXT NOT NULL UNIQUE,
        url_newsletter TEXT,
        thematique     TEXT,
        actif          INTEGER NOT NULL DEFAULT 1 CHECK (actif IN (0, 1)),
        source_info    TEXT NOT NULL,
        cree_le        TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE competitors (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        nom            TEXT NOT NULL,
        domaine        TEXT NOT NULL UNIQUE,
        url_newsletter TEXT,
        description    TEXT,
        actif          INTEGER NOT NULL DEFAULT 1 CHECK (actif IN (0, 1)),
        source_info    TEXT NOT NULL,
        cree_le        TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE audit_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        table_nom       TEXT NOT NULL,
        ligne_id        INTEGER NOT NULL,
        champ           TEXT NOT NULL,
        ancienne_valeur TEXT,
        nouvelle_valeur TEXT,
        modifie_le      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 2 : éditions importées, leurs éléments extraits, journal d'import.
    # Chaque donnée extraite garde sa source (colonnes *_source).
    # NULL = inconnu : rien n'est jamais déduit ni inventé.
    """
    CREATE TABLE editions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        publication_type  TEXT NOT NULL CHECK (publication_type IN ('own', 'competitor')),
        publication_id    INTEGER NOT NULL,
        objet             TEXT,
        objet_source      TEXT,
        expediteur        TEXT,
        expediteur_source TEXT,
        date_envoi        TEXT,
        date_statut       TEXT NOT NULL CHECK (date_statut IN ('source', 'inconnue')),
        date_source       TEXT,
        texte             TEXT,
        message_id        TEXT,
        hash_contenu      TEXT NOT NULL,
        fichier_source    TEXT NOT NULL,
        format_source     TEXT NOT NULL CHECK (format_source IN ('eml', 'html')),
        archive_chemin    TEXT,
        importe_le        TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_editions_hash
        ON editions (publication_type, publication_id, hash_contenu);
    CREATE UNIQUE INDEX ux_editions_message_id
        ON editions (publication_type, publication_id, message_id)
        WHERE message_id IS NOT NULL;

    CREATE TABLE edition_headings (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        edition_id INTEGER NOT NULL REFERENCES editions (id),
        niveau     INTEGER NOT NULL,
        texte      TEXT NOT NULL,
        position   INTEGER NOT NULL
    );

    CREATE TABLE edition_links (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        edition_id  INTEGER NOT NULL REFERENCES editions (id),
        url         TEXT NOT NULL,
        domaine     TEXT,
        ancre       TEXT,
        a_suivi     INTEGER NOT NULL DEFAULT 0 CHECK (a_suivi IN (0, 1)),
        position    INTEGER NOT NULL
    );

    CREATE TABLE edition_images (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        edition_id INTEGER NOT NULL REFERENCES editions (id),
        src        TEXT NOT NULL,
        alt        TEXT,
        largeur    TEXT,
        hauteur    TEXT,
        lien_url   TEXT,
        position   INTEGER NOT NULL
    );

    CREATE TABLE import_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        fichier    TEXT NOT NULL,
        statut     TEXT NOT NULL CHECK (statut IN ('ok', 'doublon', 'erreur')),
        message    TEXT,
        edition_id INTEGER REFERENCES editions (id),
        date_import TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 3 : détection publicitaire. Une détection = un annonceur dans un bloc (ou une
    # simple mention éditoriale, ad_block_id NULL). Les preuves sont dans evidences.
    # La validation humaine ne modifie jamais le niveau calculé : elle s'ajoute.
    """
    CREATE TABLE advertisers (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        nom           TEXT NOT NULL,
        nom_source    TEXT NOT NULL,
        nom_normalise TEXT NOT NULL,
        domaine       TEXT,
        cree_le       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_advertisers_domaine ON advertisers (domaine)
        WHERE domaine IS NOT NULL;
    CREATE UNIQUE INDEX ux_advertisers_nom ON advertisers (nom_normalise)
        WHERE domaine IS NULL;

    CREATE TABLE ad_blocks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        edition_id    INTEGER NOT NULL REFERENCES editions (id),
        type          TEXT NOT NULL CHECK (type IN
                          ('bloc_sponsorise', 'banniere', 'lien_commercial')),
        ordre         INTEGER NOT NULL,
        extrait_texte TEXT,
        extrait_html  TEXT,
        score         INTEGER NOT NULL,
        niveau        TEXT NOT NULL CHECK (niveau IN ('confirmee', 'potentielle')),
        cree_le       TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE detections (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        edition_id        INTEGER NOT NULL REFERENCES editions (id),
        ad_block_id       INTEGER REFERENCES ad_blocks (id),
        advertiser_id     INTEGER NOT NULL REFERENCES advertisers (id),
        niveau            TEXT NOT NULL CHECK (niveau IN ('confirmee', 'potentielle', 'citee')),
        score             INTEGER NOT NULL,
        statut_validation TEXT NOT NULL DEFAULT 'a_valider'
                          CHECK (statut_validation IN ('a_valider', 'validee', 'rejetee')),
        valide_le         TEXT,
        cree_le           TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_detections
        ON detections (edition_id, COALESCE(ad_block_id, 0), advertiser_id);

    CREATE TABLE evidences (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        detection_id INTEGER NOT NULL REFERENCES detections (id),
        regle        TEXT NOT NULL,
        force        TEXT NOT NULL CHECK (force IN ('fort', 'moyen', 'faible', 'aucun')),
        poids        INTEGER NOT NULL,
        extrait      TEXT NOT NULL,
        emplacement  TEXT
    );
    """,
    # 4 : découverte de concurrents. Un candidat n'est jamais un concurrent tant que
    # l'utilisateur ne l'a pas accepté ; ses signaux de proximité gardent l'extrait exact.
    """
    CREATE TABLE competitor_candidates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        domaine         TEXT NOT NULL UNIQUE,
        nom             TEXT NOT NULL,
        url_trouvee     TEXT NOT NULL,
        url_newsletter  TEXT,
        description     TEXT,
        requetes        TEXT NOT NULL,
        fournisseur     TEXT NOT NULL,
        score           INTEGER NOT NULL,
        statut          TEXT NOT NULL DEFAULT 'propose'
                        CHECK (statut IN ('propose', 'accepte', 'rejete')),
        competitor_id   INTEGER REFERENCES competitors (id),
        cree_le         TEXT NOT NULL DEFAULT (datetime('now')),
        decide_le       TEXT
    );

    CREATE TABLE candidate_signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL REFERENCES competitor_candidates (id),
        regle        TEXT NOT NULL,
        poids        INTEGER NOT NULL,
        extrait      TEXT NOT NULL,
        source       TEXT NOT NULL
    );
    """,
    # 5 : mode de découverte. 'recherche_manuelle' = candidats trouvés et vérifiés sur
    # pages publiques ; leur score reste 0 et n'est pas affiché (aucun chiffre inventé).
    """
    ALTER TABLE competitor_candidates
        ADD COLUMN mode_decouverte TEXT NOT NULL DEFAULT 'recherche_web'
        CHECK (mode_decouverte IN ('recherche_web', 'recherche_manuelle'));
    """,
]


def migrate(conn: sqlite3.Connection) -> int:
    """Applique les migrations manquantes et renvoie la version atteinte."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    return len(MIGRATIONS)
