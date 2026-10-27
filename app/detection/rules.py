"""Chargement des règles de détection (config/detection_rules.json) et calcul du niveau."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "detection_rules.json"
_FLAGS = re.IGNORECASE


@dataclass
class LabelRule:
    force: str  # 'fort' ou 'moyen'
    pattern: re.Pattern  # groupes : label, nom (facultatif)
    regle: str  # nom lisible, affiché comme preuve


@dataclass
class Rules:
    poids: dict[str, int]
    seuil_confirmee: int
    seuil_potentielle: int
    confirmee_exige_fort: bool
    libelle_longueur_max: int
    bloc_texte_max: int
    bloc_domaines_externes_max: int
    libelles: list[LabelRule]
    appels_a_action: re.Pattern
    domaines_ignores: set[str]
    seuil_recurrence: int = 3  # éditions distinctes à partir desquelles un annonceur est récurrent

    def evaluate(self, forces: list[str]) -> tuple[int, str | None]:
        """Score (plafonné à 100) et niveau ('confirmee', 'potentielle' ou None)."""
        score = min(100, sum(self.poids[f] for f in forces))
        if "fort" in forces and score >= self.seuil_confirmee:
            return score, "confirmee"
        if not self.confirmee_exige_fort and score >= self.seuil_confirmee:
            return score, "confirmee"
        if score >= self.seuil_potentielle:
            return score, "potentielle"
        return score, None


def _label_rules(force: str, seuls: list[str], avec_nom: list[str]) -> list[LabelRule]:
    rules = []
    for alt in seuls:
        # libellé seul, ou « Libellé : Nom »
        pat = rf"^\W*(?P<label>{alt})(?:\s*[:\-–—|]\s*(?P<nom>.{{1,60}}))?\W*$"
        rules.append(LabelRule(force, re.compile(pat, _FLAGS), f"libellé « {alt} »"))
    for alt in avec_nom:
        # « Libellé Nom » : le nom suit directement
        pat = rf"^\W*(?P<label>{alt})\s*(?P<nom>\S.{{0,59}})\W*$"
        rules.append(LabelRule(force, re.compile(pat, _FLAGS), f"libellé « {alt} »"))
    return rules


def load_rules(path: Path | str | None = None) -> Rules:
    data = json.loads(Path(path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
    return Rules(
        poids=data["poids"],
        seuil_confirmee=data["seuils"]["confirmee"],
        seuil_potentielle=data["seuils"]["potentielle"],
        confirmee_exige_fort=data["confirmee_exige_signal_fort"],
        libelle_longueur_max=data["libelle_longueur_max"],
        bloc_texte_max=data["bloc_texte_max"],
        bloc_domaines_externes_max=data["bloc_domaines_externes_max"],
        libelles=(_label_rules("fort", data["libelles_forts_seuls"], data["libelles_forts_avec_nom"])
                  + _label_rules("moyen", data["libelles_moyens_seuls"], data["libelles_moyens_avec_nom"])),
        appels_a_action=re.compile("|".join(data["appels_a_action"]), _FLAGS),
        domaines_ignores=set(data["domaines_ignores"]),
        seuil_recurrence=data.get("seuil_recurrence_editions", 3),
    )
