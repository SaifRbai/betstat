"""Ce que la regle de selection a REELLEMENT rapporte, mesure hors echantillon.

paris/valeur.py annonce une esperance a chaque pari retenu. Cette esperance est
une prevision du modele sur lui-meme : elle vaut ce que vaut le modele. Ce
module la confronte au seul juge qui compte, les resultats passes.

La methode :
  1. predictions walk-forward (`modele.calibration`) — le modele ne voit jamais
     le futur, il est reestime au fil des journees ;
  2. probabilites de marche deviggees a partir des cotes historiques du CSV ;
  3. la meme regle que paris/valeur.py appliquee a chaque selection ;
  4. ROI realise, avec intervalle de confiance par bootstrap.

Deux conditions de valeur.py ne sont pas reproductibles ici : « cote meilleure
que la mediane des books » et « au moins trois bookmakers », que les CSV ne
permettent pas de reconstituer. Le CSV fournit en revanche la MEILLEURE cote du
marche, celle qu'on ne decroche pas toujours en pratique. La mesure est donc
plutot optimiste : le resultat reel serait au mieux egal, plus probablement pire.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
from modele import backtest, calibration

_journal = logging.getLogger("foot_edge.performance")

SELECTIONS = ("1", "N", "2")


@dataclass
class Performance:
    """Verdict mesure d'une regle de selection sur un championnat."""

    championnat: str
    a_melange: float           # poids accorde au modele face au marche
    nb_selections: int          # selections analysees
    nb_paris: int               # celles que la regle retient
    roi: float                  # rendement realise, en fraction de la mise
    roi_bas: float              # borne basse a 95 %
    roi_haut: float             # borne haute a 95 %
    esperance_annoncee: float   # ce que la regle promettait
    roi_favori_marche: float    # baseline : parier le favori des bookmakers
    roi_tout_parier: float      # baseline : parier toutes les selections
    matchs: int
    saisons: int
    calcule_le: str

    @property
    def significatif(self) -> bool:
        """Vrai quand l'intervalle de confiance exclut zero."""
        return self.roi_haut < 0 or self.roi_bas > 0

    @property
    def verdict(self) -> str:
        if not self.significatif:
            return ("Sur cet historique, la regle n'est ni gagnante ni perdante de "
                    "facon mesurable : l'intervalle de confiance contient zero.")
        if self.roi_haut < 0:
            return (f"Sur cet historique, la regle a PERDU {abs(self.roi):.1%} par pari "
                    f"alors qu'elle annoncait {self.esperance_annoncee:+.1%}. "
                    "L'ecart n'est pas du hasard : l'intervalle de confiance a 95 % "
                    "reste entierement negatif.")
        return (f"Sur cet historique, la regle a gagne {self.roi:+.1%} par pari, "
                f"intervalle de confiance entierement positif.")


# --------------------------------------------------------------------------
# Mesure
# --------------------------------------------------------------------------

def _table_selections(predictions: pd.DataFrame, a_melange: float = 0.3) -> pd.DataFrame:
    """Une ligne par selection : proba modele, proba marche, melange, cote, issue."""
    colonnes = ["cote_1", "cote_N", "cote_2", "cote_1_max", "cote_N_max", "cote_2_max"]
    utilisables = predictions.dropna(subset=colonnes).reset_index(drop=True)
    if utilisables.empty:
        return pd.DataFrame()

    devigge = np.array([
        calibration.deviguer([ligne.cote_1, ligne.cote_N, ligne.cote_2], "shin")
        for ligne in utilisables.itertuples()])

    morceaux = []
    for rang, selection in enumerate(SELECTIONS):
        morceaux.append(pd.DataFrame({
            "match": utilisables.index,
            "selection": selection,
            "p_modele": utilisables[f"p_modele_{selection}"].to_numpy(),
            "p_marche": devigge[:, rang],
            "cote": utilisables[f"cote_{selection}_max"].to_numpy(),
            "realise": (utilisables["issue"] == selection).astype(int).to_numpy(),
        }))
    table = pd.concat(morceaux, ignore_index=True)

    table["p_finale"] = [
        float(calibration.melange_geometrique(
            np.array([ligne.p_modele, 1 - ligne.p_modele]),
            np.array([ligne.p_marche, 1 - ligne.p_marche]), a_melange)[0])
        for ligne in table.itertuples()]
    table["esperance"] = table["p_finale"] * table["cote"] - 1.0
    table["ecart"] = (table["p_modele"] - table["p_marche"]).abs()
    return table


def _roi(sous_table: pd.DataFrame) -> float:
    if sous_table.empty:
        return float("nan")
    return float((sous_table["realise"] * sous_table["cote"] - 1).mean())


def mesurer(matchs: pd.DataFrame, championnat: str, saisons: int = 4,
            seuil_esperance: float | None = None,
            seuil_divergence: float | None = None,
            frequence_reentrainement: int = 5,
            a_melange: float | None = None) -> Performance:
    """Applique la regle a l'historique et renvoie son rendement reel.

    Le poids du melange est celui que l'application utilisera reellement pour
    ce championnat : mesurer avec un poids et parier avec un autre donnerait un
    bilan qui ne correspond a rien.
    """
    a_melange = (calibration.poids_melange(championnat) if a_melange is None
                 else a_melange)
    seuil_esperance = (config.SEUIL_ESPERANCE_DEFAUT if seuil_esperance is None
                       else seuil_esperance)
    seuil_divergence = (config.SEUIL_DIVERGENCE_ALERTE if seuil_divergence is None
                        else seuil_divergence)

    predictions = calibration.predictions_walk_forward(
        matchs, frequence_reentrainement=frequence_reentrainement)
    table = _table_selections(predictions, a_melange)
    if table.empty:
        raise ValueError(
            f"Aucune cote historique exploitable pour {championnat} : les CSV "
            "football-data.co.uk ne fournissent pas les colonnes de cotes sur "
            "toutes les saisons. La regle ne peut pas etre mesuree ici.")

    retenus = table[(table["esperance"] > seuil_esperance)
                    & (table["ecart"] <= seuil_divergence)]
    if retenus.empty:
        raise ValueError(f"La regle ne retient aucun pari sur l'historique de {championnat}.")

    roi, bas, haut = backtest.intervalle_bootstrap_roi(
        (retenus["realise"] * retenus["cote"]).to_numpy(), np.ones(len(retenus)))

    favoris = table.loc[table.groupby("match")["p_marche"].idxmax()]
    return Performance(
        championnat=championnat.upper(), a_melange=float(a_melange),
        nb_selections=int(len(table)),
        nb_paris=int(len(retenus)),
        roi=float(roi - 1.0), roi_bas=float(bas - 1.0), roi_haut=float(haut - 1.0),
        esperance_annoncee=float(retenus["esperance"].mean()),
        roi_favori_marche=_roi(favoris),
        roi_tout_parier=_roi(table),
        matchs=int(len(predictions)), saisons=saisons,
        calcule_le=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# --------------------------------------------------------------------------
# Persistance
# --------------------------------------------------------------------------

def chemin(championnat: str) -> Path:
    return config.DOSSIER_CALIBRATION / f"performance_regle_{championnat.upper()}.json"


def enregistrer(performance: Performance) -> Path:
    config.creer_dossiers()
    destination = chemin(performance.championnat)
    destination.write_text(json.dumps(asdict(performance), indent=2), encoding="utf-8")
    return destination


def charger(championnat: str) -> Performance | None:
    """Mesure deja calculee, ou None. Ne calcule jamais : c'est trop long pour
    une requete web, et c'est le role de `cli.py performance`."""
    fichier = chemin(championnat)
    if not fichier.exists():
        return None
    try:
        charge = json.loads(fichier.read_text(encoding="utf-8"))
        # Mesures anterieures a l'ajout du poids : elles ont ete produites avec
        # la valeur codee en dur, on le dit plutot que de refuser de les lire.
        charge.setdefault("a_melange", config.POIDS_MELANGE_DEFAUT)
        return Performance(**charge)
    except (json.JSONDecodeError, TypeError) as erreur:
        _journal.info("Mesure de performance illisible pour %s : %s", championnat, erreur)
        return None
