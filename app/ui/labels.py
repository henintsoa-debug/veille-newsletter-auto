"""Libellés français affichés dans le tableau de bord."""

NIVEAUX = {"confirmee": "Confirmée", "potentielle": "Potentielle", "citee": "Citée (mention)"}
STATUTS = {"a_valider": "À valider", "validee": "Validée", "rejetee": "Rejetée"}
TYPES_BLOC = {"bloc_sponsorise": "Bloc sponsorisé", "banniere": "Bannière",
              "lien_commercial": "Lien commercial"}
NOUVEAUTE = {"nouveau": "Nouveau", "non_nouveau": "Déjà connu", "indetermine": "Indéterminé"}
FORCES = {"fort": "Fort", "moyen": "Moyen", "faible": "Faible", "aucun": "Aucun (simple mention)"}
INCONNU = "Inconnu"


def day(value: str | None) -> str:
    """Jour AAAA-MM-JJ tel qu'écrit dans la source, ou « Inconnue »."""
    return value[:10] if value else "Inconnue"


def fr(value, mapping: dict | None = None) -> str:
    """Affiche « Inconnu » pour une valeur absente, jamais une valeur devinée."""
    if value is None or value == "":
        return INCONNU
    return mapping.get(value, str(value)) if mapping else str(value)
