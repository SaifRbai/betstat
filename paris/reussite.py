"""Taux de reussite MESURE des pronos, par type de pari et par niveau de confiance.

Une probabilite de 75 % n'est qu'une promesse. Ce module la confronte au passe :
sur les dernieres saisons d'un championnat, le modele predit chaque journee
sans connaitre le futur (walk-forward, `calibration.predictions_walk_forward`),
et l'on compte, pour chaque type de pari et chaque tranche de dix points de
probabilite, combien de ces pronos sont reellement passes.

Le resultat repond a la seule question qui compte devant un « prono sur » :
« les pronos de ce type, annonces a ce niveau, passaient-ils vraiment ? »

Tous les pronos du catalogue sont comptes, pas seulement ceux qu'on aurait
retenus : c'est une mesure de calibration, independante de la regle de choix.
Le calcul coute de 5 a 25 secondes par championnat ; il est garde sept jours.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

import config

_journal = logging.getLogger(__name__)

DUREE_VALIDITE_SECONDES = 7 * 24 * 3600
FREQUENCE_REENTRAINEMENT = 15          # journees entre deux entrainements
MIN_PRONOS_MESURE = 30                 # en dessous, la mesure ne dit rien
TRANCHES = (0.5, 0.6, 0.7, 0.8, 0.9)   # bornes basses ; la derniere va a 100 %

FAMILLES = ("1N2", "double chance", "totaux 2.5")


def _dossier() -> Path:
    return config.DOSSIER_CACHE / "reussite"


def _chemin(championnat: str) -> Path:
    return _dossier() / f"{championnat.upper()}.json"


def tranche(probabilite: float) -> float | None:
    """Borne basse de la tranche de dix points, ou None sous 50 %."""
    if probabilite is None or not np.isfinite(probabilite) or probabilite < TRANCHES[0]:
        return None
    return max(t for t in TRANCHES if probabilite >= t - 1e-12)


# --------------------------------------------------------------------------
# Comptage
# --------------------------------------------------------------------------

def _candidats(ligne) -> list[tuple[str, float, bool]]:
    """(famille, probabilite du modele, passe ?) pour chaque prono d'un match."""
    p1, pn, p2 = ligne.p_modele_1, ligne.p_modele_N, ligne.p_modele_2
    plus = ligne.p_modele_plus25
    bd, be = ligne.buts_dom, ligne.buts_ext
    total = bd + be
    return [
        ("1N2", p1, bd > be), ("1N2", pn, bd == be), ("1N2", p2, bd < be),
        ("double chance", p1 + pn, bd >= be), ("double chance", pn + p2, bd <= be),
        ("double chance", p1 + p2, bd != be),
        ("totaux 2.5", plus, total > 2.5), ("totaux 2.5", 1.0 - plus, total < 2.5),
    ]


def compter(predictions: pd.DataFrame) -> dict:
    """{famille: {tranche: [nombre, passes]}} a partir de predictions walk-forward."""
    table: dict[str, dict[str, list[int]]] = {f: {} for f in FAMILLES}
    if predictions.empty:
        return table
    for ligne in predictions.itertuples():
        if pd.isna(ligne.buts_dom) or pd.isna(ligne.buts_ext):
            continue
        for famille, probabilite, passe in _candidats(ligne):
            borne = tranche(float(probabilite))
            if borne is None:
                continue
            case = table[famille].setdefault(f"{borne:.1f}", [0, 0])
            case[0] += 1
            case[1] += int(bool(passe))
    return table


def mesurer(championnat: str, nb_saisons: int = 3, forcer: bool = False) -> dict | None:
    """Table de reussite d'un championnat, depuis le cache si elle a moins de 7 jours.

    Renvoie None si le championnat n'a pas d'historique (coupe, ligue vide).
    """
    championnat = championnat.upper()
    if championnat not in config.CHAMPIONNATS:
        return None
    chemin = _chemin(championnat)
    if not forcer and chemin.exists() and (
            time.time() - chemin.stat().st_mtime) < DUREE_VALIDITE_SECONDES:
        return json.loads(chemin.read_text(encoding="utf-8"))

    from data import resultats as resultats_module
    from modele import calibration

    matchs = resultats_module.charger(championnat, nb_saisons)
    if matchs.empty:
        return None
    debut = time.time()
    predictions = calibration.predictions_walk_forward(
        matchs, frequence_reentrainement=FREQUENCE_REENTRAINEMENT)
    table = {"championnat": championnat, "matchs": int(len(predictions)),
             "familles": compter(predictions)}
    _dossier().mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(table), encoding="utf-8")
    _journal.info("Reussite %s mesuree sur %d matchs en %.0f s",
                  championnat, len(predictions), time.time() - debut)
    return table


def en_cache(championnat: str) -> dict | None:
    """La table si elle est deja calculee et encore valide, sans rien calculer."""
    chemin = _chemin(championnat)
    if not chemin.exists():
        return None
    if time.time() - chemin.stat().st_mtime >= DUREE_VALIDITE_SECONDES:
        return None
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------

def _lire(tables: list[dict], famille: str, borne: float) -> tuple[int, int]:
    nombre = passes = 0
    for table in tables:
        case = table.get("familles", {}).get(famille, {}).get(f"{borne:.1f}")
        if case:
            nombre += case[0]
            passes += case[1]
    return nombre, passes


def taux(famille: str, probabilite: float, table_ligue: dict | None,
         toutes: list[dict]) -> dict | None:
    """Le taux mesure pour un prono de cette famille a cette probabilite.

    Celui du championnat s'il compte assez de pronos comparables, sinon celui
    de tous les championnats mesures : moins precis, mais pas muet.
    """
    borne = tranche(probabilite)
    if borne is None or famille not in FAMILLES:
        return None
    haut = min(borne + 0.1, 1.0)
    for portee, tables in (("ligue", [table_ligue] if table_ligue else []),
                           ("toutes", toutes)):
        nombre, passes = _lire(tables, famille, borne)
        if nombre >= MIN_PRONOS_MESURE:
            return {"taux": passes / nombre, "nombre": nombre, "passes": passes,
                    "portee": portee, "tranche": [borne, haut]}
    return None
