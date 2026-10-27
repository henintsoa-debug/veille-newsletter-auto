# Veille Newsletter Auto

Application locale (Python, SQLite, Streamlit) de veille concurrentielle sur les newsletters :
import `.eml` / `.html`, détection des publicités et des annonceurs avec preuves, tableau de bord en
français, export CSV pour Excel.

**Utilisation** : voir [docs/guide_utilisateur_fr.md](docs/guide_utilisateur_fr.md).

## Développement

Prérequis : Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest
lancer.bat
```

Un seul test : `.venv\Scripts\python.exe -m pytest tests/test_detection.py::test_strong_label_gives_confirmed_with_explainable_evidence`

## Organisation

- `app/db/` : schéma SQLite versionné (`migrations.py`, on n'ajoute que des migrations), accès aux données.
- `app/parsing/` : extraction des métadonnées et du contenu d'un `.eml` ou `.html`.
- `app/ingestion/` : import, déduplication, journal d'import.
- `app/detection/` : détection publicitaire (`config/detection_rules.json` pour les règles).
- `app/advertisers/` : annonceurs, validation humaine, récurrents et nouveaux.
- `app/ui/` : tableau de bord (`queries.py` : lecture, `app.py` : affichage).
- `app/export/` : exports CSV.
- `app/discovery/` : candidats concurrents (fichiers vérifiés `config/candidats_*.json`, saisie d'un site,
  import de liste CSV/TXT dans `list_import.py`), puis acceptation ou rejet par l'utilisateur. Aucun
  accès réseau ni clé API.

Règles de conception : rien n'est inventé (inconnu = `NULL`, affiché « Inconnu »), chaque donnée garde sa
source, aucune suppression de données.
