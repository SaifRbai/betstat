"""Detection de valeur et calcul de mise.

Une probabilite n'est pas une decision. Ce module transforme l'une en l'autre,
avec quatre garde-fous cumulatifs et un plafond de mise non contournable.

Rappel de cadrage : la marge du bookmaker est de 5 a 8 %. Un ecart de 2 % entre
ton modele et le marche n'est pas une opportunite, c'est du bruit. D'ou le
seuil d'esperance a 4 % par defaut, et les trois conditions supplementaires.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

import config
from data import noms as noms_module
from modele import calibration

_journal = logging.getLogger("foot_edge.valeur")


@dataclass
class Opportunite:
    """Verdict complet sur un pari possible, y compris quand il est rejete."""

    dom: str
    ext: str
    marche: str
    selection: str
    cote: float
    bookmaker: str
    p_modele: float
    p_marche: float
    p_finale: float
    esperance: float
    fraction_kelly: float
    mise: float
    recommandee: bool
    nb_bookmakers: int
    cote_mediane: float
    ecart_modele_marche: float
    raisons_rejet: list[str] = field(default_factory=list)
    alerte: str | None = None
    date: object | None = None

    def resume(self) -> str:
        entete = (f"{self.dom} - {self.ext} | {self.marche} {self.selection} "
                  f"@ {self.cote:.2f} ({self.bookmaker})")
        detail = (f"  modele {self.p_modele:.1%} | marche {self.p_marche:.1%} | "
                  f"finale {self.p_finale:.1%} | esperance {self.esperance:+.1%}")
        if self.recommandee:
            return f"{entete}\n{detail}\n  mise conseillee : {self.mise:.2f}"
        motifs = " ; ".join(self.raisons_rejet) or "aucun motif"
        return f"{entete}\n{detail}\n  ecarte : {motifs}"


# --------------------------------------------------------------------------
# Mise
# --------------------------------------------------------------------------

def fraction_kelly_complete(proba: float, cote: float) -> float:
    """f = (p*(c-1) - (1-p)) / (c-1). Negative si le pari n'a pas de valeur."""
    if cote <= 1.0:
        return 0.0
    gain = cote - 1.0
    return (proba * gain - (1.0 - proba)) / gain


def mise_conseillee(proba: float, cote: float, bankroll: float,
                    fraction: float = config.FRACTION_KELLY) -> tuple[float, float]:
    """Renvoie (fraction de bankroll retenue, mise en euros).

    Kelly complet ruine statistiquement un joueur dont l'estimation de
    probabilite est un peu fausse. On divise donc par 4 au minimum, et on
    plafonne a 2 % de la bankroll. Ce plafond est lu dans config et n'est
    volontairement expose par aucun parametre : il ne doit pas etre desactivable.
    """
    if bankroll <= 0:
        return 0.0, 0.0
    fraction = min(max(fraction, 0.0), config.FRACTION_KELLY)
    brute = fraction_kelly_complete(proba, cote)
    if brute <= 0:
        return 0.0, 0.0
    retenue = min(brute * fraction, config.PLAFOND_MISE_BANKROLL)
    return retenue, round(retenue * bankroll, 2)


# --------------------------------------------------------------------------
# Evaluation d'un pari
# --------------------------------------------------------------------------

def evaluer(
    dom: str,
    ext: str,
    marche: str,
    selection: str,
    cote: float,
    bookmaker: str,
    p_modele: float,
    p_marche: float,
    p_finale: float,
    bankroll: float,
    nb_bookmakers: int,
    cote_mediane: float,
    seuil_esperance: float = config.SEUIL_ESPERANCE_DEFAUT,
    date: object | None = None,
) -> Opportunite:
    """Applique les quatre conditions cumulatives et calcule la mise.

    Renvoie toujours un objet complet : quand le pari est ecarte, la liste
    raisons_rejet dit pourquoi. On veut comprendre, pas obtenir un booleen.
    """
    esperance = p_finale * cote - 1.0
    ecart = abs(p_modele - p_marche)
    raisons: list[str] = []
    alerte: str | None = None

    if esperance <= seuil_esperance:
        raisons.append(f"esperance {esperance:+.1%} sous le seuil de {seuil_esperance:.1%}")
    if not math.isnan(cote_mediane) and cote <= cote_mediane:
        raisons.append(f"cote {cote:.2f} pas meilleure que la mediane des books "
                       f"({cote_mediane:.2f})")
    if nb_bookmakers < config.MIN_BOOKMAKERS_CONSENSUS:
        raisons.append(f"{nb_bookmakers} bookmaker(s) seulement, il en faut "
                       f"{config.MIN_BOOKMAKERS_CONSENSUS} pour un consensus")
    if ecart > config.SEUIL_DIVERGENCE_ALERTE:
        alerte = (f"divergence de {ecart:.0%} entre modele et marche : c'est presque "
                  "toujours une information qui manque au modele (blessure, composition, "
                  "match sans enjeu), pas une opportunite")
        raisons.append("divergence modele/marche trop forte")

    recommandee = not raisons
    fraction, mise = mise_conseillee(p_finale, cote, bankroll) if recommandee else (0.0, 0.0)

    return Opportunite(
        dom=dom, ext=ext, marche=marche, selection=selection, cote=float(cote),
        bookmaker=bookmaker, p_modele=float(p_modele), p_marche=float(p_marche),
        p_finale=float(p_finale), esperance=float(esperance),
        fraction_kelly=fraction, mise=mise, recommandee=recommandee,
        nb_bookmakers=int(nb_bookmakers), cote_mediane=float(cote_mediane),
        ecart_modele_marche=float(ecart), raisons_rejet=raisons, alerte=alerte, date=date,
    )


# --------------------------------------------------------------------------
# Balayage d'une journee
# --------------------------------------------------------------------------

_SELECTIONS_MODELE = {
    ("1N2", "1"): lambda p: p["1N2"]["1"],
    ("1N2", "N"): lambda p: p["1N2"]["N"],
    ("1N2", "2"): lambda p: p["1N2"]["2"],
    ("plus_moins_2.5", "plus"): lambda p: p["totaux"]["plus_2.5"],
    ("plus_moins_2.5", "moins"): lambda p: p["totaux"]["moins_2.5"],
}


def _mediane_cotes(cotes: pd.DataFrame, cle: tuple, selection: str) -> tuple[float, int]:
    lignes = cotes[(cotes["dom"] == cle[1]) & (cotes["ext"] == cle[2])
                   & (cotes["marche"] == cle[3]) & (cotes["selection"] == selection)]
    if lignes.empty:
        return float("nan"), 0
    return float(lignes["cote"].median()), int(lignes["bookmaker"].nunique())


def detecter(
    cotes: pd.DataFrame,
    modele,
    bankroll: float,
    a_melange: float = 0.3,
    seuil_esperance: float = config.SEUIL_ESPERANCE_DEFAUT,
    methode_devig: str = "shin",
) -> list[Opportunite]:
    """Parcourt les cotes d'une journee et evalue chaque selection.

    Renvoie la liste complete (recommandees ou non), triee par esperance
    decroissante : les rejets sont des informations, pas du bruit a cacher.
    """
    if cotes.empty:
        return []

    marche_devigge = calibration.consensus(cotes, methode_devig)
    opportunites: list[Opportunite] = []
    non_resolues: set[str] = set()

    for cle, groupe in marche_devigge.groupby(["date", "dom", "ext", "marche"], dropna=False):
        date_match, dom, ext, nom_marche = cle
        equipe_dom = noms_module.resoudre_vers(dom, modele.equipes)
        equipe_ext = noms_module.resoudre_vers(ext, modele.equipes)
        if equipe_dom is None or equipe_ext is None:
            # Equipe absente du modele ou nom ambigu : on ne devine pas, on note.
            non_resolues.update(n for n, e in ((dom, equipe_dom), (ext, equipe_ext))
                                if e is None)
            continue
        prediction = modele.predire(equipe_dom, equipe_ext)
        for ligne in groupe.itertuples():
            fonction = _SELECTIONS_MODELE.get((nom_marche, ligne.selection))
            if fonction is None:
                continue
            p_modele = float(fonction(prediction))
            p_marche = float(ligne.p_marche)
            p_finale = float(calibration.melange_geometrique(
                np.array([p_modele, 1 - p_modele]),
                np.array([p_marche, 1 - p_marche]), a_melange)[0])
            mediane, nb_books = _mediane_cotes(cotes, cle, ligne.selection)
            opportunites.append(evaluer(
                dom=dom, ext=ext, marche=nom_marche, selection=ligne.selection,
                cote=float(ligne.meilleure_cote), bookmaker=str(ligne.bookmaker_meilleure),
                p_modele=p_modele, p_marche=p_marche, p_finale=p_finale,
                bankroll=bankroll, nb_bookmakers=nb_books, cote_mediane=mediane,
                seuil_esperance=seuil_esperance, date=date_match,
            ))

    if non_resolues:
        _journal.info(
            "%d equipe(s) du flux de cotes sans correspondance dans le modele, "
            "leurs matchs sont ignores : %s. "
            "Complete data/noms_equipes.json (outils/verifier_noms.py) ou charge "
            "davantage de saisons si ce sont des promus.",
            len(non_resolues), ", ".join(sorted(non_resolues)))

    opportunites.sort(key=lambda o: o.esperance, reverse=True)
    return opportunites


def en_dataframe(opportunites: Sequence[Opportunite]) -> pd.DataFrame:
    """Vue tabulaire, pour l'interface et l'export CSV."""
    if not opportunites:
        return pd.DataFrame(columns=[
            "date", "dom", "ext", "marche", "selection", "cote", "bookmaker",
            "p_modele", "p_marche", "p_finale", "ecart", "esperance", "mise",
            "recommandee", "alerte", "motifs"])
    return pd.DataFrame([{
        "date": o.date, "dom": o.dom, "ext": o.ext, "marche": o.marche,
        "selection": o.selection, "cote": o.cote, "bookmaker": o.bookmaker,
        "p_modele": o.p_modele, "p_marche": o.p_marche, "p_finale": o.p_finale,
        "ecart": o.ecart_modele_marche, "esperance": o.esperance, "mise": o.mise,
        "recommandee": o.recommandee, "alerte": o.alerte or "",
        "motifs": " ; ".join(o.raisons_rejet),
    } for o in opportunites])
