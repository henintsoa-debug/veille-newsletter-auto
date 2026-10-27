# Veille Newsletter Auto : guide utilisateur

Application **locale** de veille concurrentielle : elle archive les newsletters que vous avez reçues
légitimement, repère les publicités et identifie les annonceurs. Aucune donnée ne quitte votre ordinateur.

## 1. Démarrer

1. Double-cliquez sur `lancer.bat` (dossier du projet).
2. Le tableau de bord s'ouvre dans votre navigateur à l'adresse `http://localhost:8501`.
3. Pour arrêter : fermez la fenêtre noire, ou faites `Ctrl + C` dedans.

Vos données sont dans le dossier `data/` :
- `data/veille.db` : la base (SQLite) ;
- `data/editions/` : une copie de chaque fichier importé.

**Sauvegarde** : copiez simplement le dossier `data/`. L'application ne supprime jamais vos données.

Au premier lancement, vos 4 newsletters (mammam-ia.com, newsletter.lebigdata.fr, evenement.fr,
cyberdefense.net) sont créées. Leur nom est leur domaine, et l'URL et la thématique sont « Inconnu » tant
que vous ne les renseignez pas.

## 2. Principes à connaître

- **Rien n'est inventé.** Une donnée absente de la source s'affiche **Inconnu** ou **Inconnue**.
- **Chaque donnée garde sa source** (par exemple « en-tête Date », « balise `<title>` »).
- **Une marque citée n'est pas un annonceur.** Seuls les signaux publicitaires comptent.
- **Vous décidez.** Chaque détection peut être validée ou rejetée, et l'historique est conservé.

## 3. Les niveaux de confiance

| Niveau | Signification |
|---|---|
| **Confirmée** | Au moins un signal fort : un libellé explicite comme « Sponsorisé par… » ou « Publicité » |
| **Potentielle** | Des signaux moyens ou faibles seulement (bannière cliquable, « Partenaire », lien de suivi avec appel à l'action…). **À vérifier.** |
| **Citée (mention)** | Un annonceur déjà connu est simplement mentionné dans un article, hors bloc publicitaire. Ce n'est **pas** une publicité. |

Chaque détection affiche ses **preuves** : la règle déclenchée, sa force, son poids, l'**extrait exact** de
la newsletter et son emplacement. Le score (0 à 100) est la somme des poids des signaux. Un bloc ne devient
« Confirmée » que s'il contient un signal fort.

Les règles se modifient dans `config/detection_rules.json` (libellés, poids, seuils, domaines ignorés,
seuil de récurrence). Elles s'appliquent aux **prochains imports** ; les éditions déjà importées ne sont pas
recalculées.

## 4. Importer des newsletters

Page **Import**.

1. Choisissez la publication concernée : une de vos newsletters ou un concurrent.
2. Déposez un ou plusieurs fichiers `.eml` (e-mail enregistré) ou `.html`.
3. Cliquez sur **Importer**. Un résultat s'affiche pour chaque fichier :
   - **Importée** : l'édition est archivée et analysée ;
   - **Doublon ignoré** : cette édition existait déjà (même `Message-ID` ou même texte) ;
   - **Erreur** : le motif est indiqué, et les autres fichiers sont traités normalement.

Bon à savoir :
- Un `.html` n'a ni expéditeur, ni date (sauf balise `<meta>` explicite) : ces champs seront **Inconnu**.
  Préférez le format `.eml`, qui contient les en-têtes.
- Les e-mails en texte brut (sans HTML) sont archivés, mais **aucune publicité n'y est détectée**.
- N'importez que des newsletters reçues légitimement ou publiques.

## 5. Les pages du tableau de bord

La barre latérale gauche contient le menu et les **filtres** : publication ou concurrent, période, annonceur
et niveau de confiance. Les détections **rejetées** sont masquées par défaut (case à cocher pour les voir).

| Page | À quoi elle sert |
|---|---|
| **Vue d'ensemble** | Indicateurs : éditions, annonceurs, récurrents, nouveaux, à valider ; éditions par mois |
| **Détections** | Liste de toutes les détections avec leur niveau, leur score et leur statut |
| **Éditions** | Liste des éditions ; choisissez-en une pour voir ses métadonnées, ses sources, ses titres et ses détections avec les preuves |
| **Annonceurs** | Pour chaque annonceur : nombre d'éditions, première et dernière vue, récurrent ou non, nouveau ou non ; historique complet d'un annonceur |
| **À valider** | File de validation (voir §6) |
| **Comparatif** | Annonceurs présents chez les concurrents **seulement**, **communs** avec vos newsletters, ou chez vous seulement |
| **Export** | Téléchargements CSV (voir §8) |
| **Import** | Voir §4 |
| **Découverte** | Recherche de nouveaux concurrents sur le Web (voir §10) |
| **Concurrents** | Gestion des concurrents et de vos newsletters (ajout, modification, activation) |

**Période et dates inconnues.** Quand vous filtrez par période, les éditions sans date connue sont
écartées, car on ne peut pas les placer dans le temps. Un message indique leur nombre.

## 6. Valider les détections

Page **À valider** : les plus importantes d'abord (confirmées, puis potentielles par score décroissant).

1. Ouvrez une détection et lisez l'extrait du bloc et les preuves.
2. Cliquez sur **Valider** si c'est bien une publicité de cet annonceur, sinon sur **Rejeter**.

Effets :
- Le niveau calculé (Confirmée, Potentielle) **ne change pas** : votre décision s'ajoute.
- Une détection **rejetée** n'est plus comptée dans les annonceurs récurrents ou nouveaux, et son annonceur
  n'est plus signalé comme « cité ».
- Chaque décision est enregistrée avec son ancienne valeur (historique conservé).

## 7. Annonceurs récurrents et nouveaux

Ne comptent que les détections **confirmées ou potentielles non rejetées** ; les mentions « citées » ne
comptent jamais.

- **Récurrent** : présent dans au moins **3 éditions distinctes** (réglable dans `config/detection_rules.json`,
  clé `seuil_recurrence_editions`). Plusieurs blocs du même annonceur dans une même édition comptent pour une.
- **Nouveau** : première apparition connue dans la période choisie.
- **Déjà connu** : vu avant la période (c'est certain).
- **Indéterminé** : on ne peut pas conclure, car une édition sans date pourrait être plus ancienne, ou
  aucune date n'est connue.

## 8. Exporter en CSV

Page **Export** : quatre fichiers, qui respectent les filtres de la barre latérale.

| Fichier | Contenu |
|---|---|
| `detections.csv` | Une ligne par annonceur détecté |
| `preuves.csv` | Une ligne par preuve (règle, extrait exact, emplacement) |
| `editions.csv` | Une ligne par édition, avec la source de chaque donnée |
| `annonceurs.csv` | Récurrence, nouveauté, première et dernière vue |

Les fichiers s'ouvrent directement dans Excel en français (accents corrects, colonnes séparées). Si vous
voyez des caractères étranges ou une seule colonne, utilisez *Données > À partir d'un fichier texte/CSV* et
choisissez l'encodage UTF-8 et le séparateur point-virgule. Les textes qui commencent par `=`, `+`, `-` ou `@`
sont précédés d'une apostrophe pour qu'Excel ne les exécute pas comme des formules.

## 9. Gérer les concurrents et vos newsletters

Page **Concurrents**.

- **Ajouter un concurrent** : seul le domaine (ou l'URL) est obligatoire. Le nom, l'URL de la newsletter et
  la description sont facultatifs et restent « Inconnu » si vous ne les donnez pas.
- **Ajouter une de mes newsletters** : même principe, pour augmenter la veille avec une cinquième
  newsletter ou davantage.
- **Désactiver** : décochez « Actif ». Rien n'est supprimé, l'élément n'apparaît plus dans les choix d'import.
- Un domaine déjà présent est refusé (y compris sous une autre forme, par exemple avec `www.`).

## 10. Découvrir de nouveaux concurrents

Page **Découverte**. Les concurrents candidats y arrivent de trois façons, sans clé ni abonnement. **Rien
n'est ajouté à vos concurrents sans votre clic.**

### 1. Candidats déjà proposés (recherche vérifiée)

Au lancement, l'application charge les fichiers `config/candidats_*.json` : des concurrents candidats
trouvés par recherche sur des pages publiques, à partir de la thématique de vos 4 newsletters, et
vérifiés en lisant la page de chaque site. Ils apparaissent dans **Candidats proposés**, avec le thème, les
constats (par exemple « Sponsorisez le brief quotidien ») et la page consultée. Les extraits viennent d'une
lecture automatique des pages : vérifiez-les si vous voulez les citer. Ces candidats n'ont pas de score.

### 2. Proposer un site

Ouvrez **Proposer un site** : seul le domaine (ou l'URL) est obligatoire. Le nom, l'URL de newsletter, la
description et le thème sont facultatifs et restent « Inconnu » si vous ne les donnez pas.

### 3. Importer une liste (quand vous aurez le temps)

Ouvrez **Importer une liste (CSV ou TXT)**.

- **CSV** : première ligne `domaine;nom;url_newsletter;description;theme` (seul `domaine` est obligatoire ;
  séparateur `;` ou `,`). Le bouton **Télécharger le modèle CSV** fournit un fichier vide à remplir dans Excel
  (enregistrez-le au format CSV).
- **TXT** : un domaine ou une URL par ligne ; les lignes vides et celles qui commencent par `#` sont ignorées.
- Limites : 2 Mo et 2 000 lignes par fichier.

L'import affiche, ligne par ligne, **Ajouté**, **Déjà connu** (vos newsletters, concurrents, candidats, y
compris rejetés, ou doublon dans le fichier) ou **Erreur** (domaine invalide ou vide). Une ligne en erreur
n'arrête pas les autres. Les sites importés arrivent comme candidats avec la source « Import : nom du fichier »
et la ligne d'origine comme preuve.

### Accepter ou rejeter

- **Accepter** crée le concurrent, avec sa source (« … accepté par l'utilisateur ») ; **Tout accepter** valide
  toute la file d'un coup.
- **Rejeter** retire le candidat de la file : il ne sera jamais reproposé ni réimporté. Un candidat rejeté peut
  être remis dans la file.
- Le nom d'un candidat est son domaine s'il n'en a pas reçu d'autre ; l'URL de newsletter reste « Inconnu »
  si elle n'est pas fournie.

## 11. Limites actuelles

- Les règles de détection sont un point de départ : elles doivent être ajustées sur vos vraies
  newsletters. Vérifiez les premières détections dans **À valider**.
- Les images intégrées aux e-mails (`cid:`) et les pièces jointes ne sont pas extraites.
- Deux fiches d'un même annonceur sous des domaines différents (par exemple `.com` et `.fr`) ne sont pas
  fusionnées automatiquement.
- La découverte ne cherche pas seule sur le Web : les candidats viennent des fichiers vérifiés fournis, de
  vos propositions et de vos imports de liste. L'événementiel est un secteur peu couvert par les candidats
  fournis : vos propres listes y seront probablement plus pertinentes.
- Les newsletters des concurrents ne sont pas récupérées automatiquement : vous importez les `.eml` ou
  `.html` que vous avez reçus.

## 12. Données personnelles et conformité

- N'importez que des newsletters reçues légitimement ou publiques ; respectez les conditions d'utilisation
  des sources.
- Les e-mails contiennent des adresses (expéditeur, parfois liens personnalisés). Les données restent sur votre
  ordinateur : protégez le dossier `data/` comme tout fichier contenant des données personnelles, et ne
  partagez pas les exports sans nécessité.
- L'application ne demande ni mot de passe de messagerie, ni clé API, et n'envoie aucune donnée à un
  service externe.
- Les listes de sites que vous importez restent sur votre ordinateur ; ne les partagez pas si elles contiennent
  des informations confidentielles.

## 13. En cas de problème

| Symptôme | Que faire |
|---|---|
| `lancer.bat` ne démarre pas | Vérifiez que le dossier `.venv` existe ; sinon, demandez de réinstaller l'environnement |
| La page reste vide au début | Patientez quelques secondes, le chargement est parfois lent la première fois |
| « Erreur » à l'import | Lisez le détail : format non pris en charge (seuls `.eml` et `.html` sont acceptés), fichier illisible… |
| Un annonceur manque | Il n'avait peut-être aucun signal publicitaire : les marques simplement citées ne sont pas des annonceurs |
| L'import d'une liste de sites affiche « Colonne de domaine introuvable » | La première ligne du CSV doit contenir une colonne `domaine` ; téléchargez le modèle depuis la page Découverte |
| Les accents sont illisibles après l'import d'un CSV | Enregistrez le CSV depuis Excel en « CSV UTF-8 » ; le format ANSI est aussi reconnu |
| Un site importé est « Déjà connu » | Il est déjà une de vos newsletters, un concurrent ou un candidat (y compris rejeté) |
| Trop de faux positifs | Rejetez-les, puis ajustez `config/detection_rules.json` pour les prochains imports |
