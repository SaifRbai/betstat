"""Un modele commun a toutes les divisions d'un meme pays.

Pourquoi il existe : un match de coupe oppose reguliergement deux equipes de
divisions differentes. Arsenal - Coventry City, Schalke 04 en DFB-Pokal, Le Mans
en Coupe de France. Le modele d'un championnat ne connait que ses propres
equipes ; il ecarte donc la rencontre, et la page n'affiche rien.

Ce qui rend un modele commun possible : les promotions et les relegations. Une
equipe qui a joue en E0 puis en E1 au cours des saisons chargees ne porte qu'une
seule force, ajustee contre les adversaires des deux divisions a la fois. Ce
sont ces equipes-pont, et elles seules, qui mettent les divisions a la meme
echelle. Sans elles, deux divisions forment deux composantes disjointes : leur
difference de niveau n'est pas identifiable, et un modele commun produirait des
probabilites qui n'ont aucun sens. C'est le cas du Portugal, ou football-data ne
publie pas la deuxieme division ; on refuse alors d'estimer plutot que d'inventer.

Ce que ce modele ne fournit PAS : la calibration mesuree (`poids_melange`) et le
ROI backteste sont etablis championnat par championnat, sur des echantillons qui
ne contiennent aucun match inter-divisions. Ils ne s'appliquent pas ici. Toute
estimation qui sort de ce module doit etre presentee comme non validee.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

import config
from data import resultats as resultats_module
from modele import calibration
from modele import dixon_coles as dc

_journal = logging.getLogger("foot_edge.inter_divisions")

# Nombre minimal d'equipes ayant joue dans les deux divisions pour qu'on
# accepte de les mettre sur la meme echelle. Une seule equipe-pont suffit en
# theorie a identifier l'ecart de niveau ; en pratique elle le ferait reposer
# sur une poignee de matchs, et une saison atypique de ce club deplacerait toute
# une division. Trois est un compromis, pas une verite.
SEUIL_EQUIPES_PONT = 3


@dataclass
class RapportInterDivisions:
    """Ce qui a ete retenu, ce qui a ete laisse, et pourquoi."""

    pays: str
    divisions: list[str] = field(default_factory=list)
    ecartees: dict[str, str] = field(default_factory=dict)
    ponts: dict[str, int] = field(default_factory=dict)
    nb_matchs: int = 0
    nb_equipes: int = 0

    def resume(self) -> str:
        detail = ", ".join(f"{paire} : {n}" for paire, n in self.ponts.items())
        return (f"{self.pays} — {'+'.join(self.divisions)} — {self.nb_matchs} matchs, "
                f"{self.nb_equipes} equipes, equipes-pont ({detail})")


def _pools(divisions: tuple[str, ...], saisons: int) -> dict[str, pd.DataFrame]:
    """Historique de chaque division, celles qui n'ont rien mises de cote."""
    charges: dict[str, pd.DataFrame] = {}
    for code in divisions:
        matchs = resultats_module.charger(code, saisons)
        if matchs.empty:
            _journal.info("Division %s sans historique, ignoree.", code)
            continue
        charges[code] = matchs
    return charges


def _equipes(matchs: pd.DataFrame) -> set[str]:
    return set(matchs["dom"].dropna()) | set(matchs["ext"].dropna())


def composante_reliee(charges: dict[str, pd.DataFrame],
                      depart: str) -> tuple[list[str], dict[str, int]]:
    """Divisions reliees a `depart` par assez d'equipes-pont, et le compte des ponts.

    Parcours en largeur sur le graphe des divisions : une arete existe des que
    deux divisions partagent au moins `SEUIL_EQUIPES_PONT` equipes. On ne garde
    que la composante qui contient la division demandee — une division isolee
    n'apporte rien a l'estimation et fausserait l'echelle.
    """
    equipes = {code: _equipes(m) for code, m in charges.items()}
    retenues = [depart] if depart in equipes else []
    ponts: dict[str, int] = {}
    a_visiter = list(retenues)
    while a_visiter:
        courante = a_visiter.pop(0)
        for code, autres in equipes.items():
            if code in retenues:
                continue
            commun = equipes[courante] & autres
            if len(commun) >= SEUIL_EQUIPES_PONT:
                ponts[f"{courante}/{code}"] = len(commun)
                retenues.append(code)
                a_visiter.append(code)
    # L'ordre de config est l'ordre hierarchique : le conserver rend les
    # journaux et l'affichage lisibles (E0, E1, E2... et non l'ordre de visite).
    return [c for c in charges if c in retenues], ponts


def entrainer(championnat: str, saisons: int = 4,
              xi: float | None = None) -> tuple[dc.ModeleDixonColes, RapportInterDivisions]:
    """Modele commun aux divisions du pays de `championnat`.

    Toujours sur les buts : les xG n'existent que pour cinq championnats, jamais
    pour les divisions inferieures d'ou viennent justement les adversaires de
    coupe. Les hyperparametres sont ceux de la division demandee, faute d'une
    calibration propre a l'ensemble — c'est une approximation, et elle est dite.

    Leve ValueError si le pays n'a qu'une division exploitable : dans ce cas le
    modele commun serait le modele de championnat, sans rien apporter.
    """
    championnat = championnat.upper()
    pays = config.CHAMPIONNATS.get(championnat, {}).get("pays", championnat)
    divisions = config.divisions_du_pays(championnat)
    rapport = RapportInterDivisions(pays=pays)

    charges = _pools(divisions, saisons)
    if championnat not in charges:
        raise ValueError(f"Aucun historique pour {championnat}.")

    retenues, ponts = composante_reliee(charges, championnat)
    rapport.divisions, rapport.ponts = retenues, ponts
    for code in charges:
        if code not in retenues:
            rapport.ecartees[code] = (
                f"moins de {SEUIL_EQUIPES_PONT} equipes en commun avec "
                f"{'/'.join(retenues)} : echelles non comparables")

    if len(retenues) < 2:
        raise ValueError(
            f"{pays} n'a qu'une division reliee ({championnat}). Un modele "
            "inter-divisions demande au moins deux divisions partageant "
            f"{SEUIL_EQUIPES_PONT} equipes ou plus (promotions et relegations).")

    matchs = pd.concat([charges[c] for c in retenues], ignore_index=True)
    matchs = matchs.sort_values("date").reset_index(drop=True)
    rapport.nb_matchs = len(matchs)
    rapport.nb_equipes = len(_equipes(matchs))

    parametres = calibration.charger_hyperparametres(championnat) or {}
    modele = dc.entrainer(
        matchs, cible="buts",
        xi=xi if xi is not None else parametres.get("xi", dc.XI_DEFAUT))
    _journal.info("Modele inter-divisions %s", rapport.resume())
    return modele, rapport


def pays_eligibles(saisons: int = 4) -> dict[str, list[str]]:
    """Les pays ou un modele commun est possible, avec leurs divisions retenues.

    Utile pour savoir d'avance ce que l'outil sait faire, sans attendre qu'un
    match de coupe le decouvre.
    """
    eligibles: dict[str, list[str]] = {}
    for pays, divisions in config.DIVISIONS_PAR_PAYS.items():
        if len(divisions) < 2:
            continue
        charges = _pools(divisions, saisons)
        if divisions[0] not in charges:
            continue
        retenues, _ = composante_reliee(charges, divisions[0])
        if len(retenues) >= 2:
            eligibles[pays] = retenues
    return eligibles


def equipes_connues(modele: Any) -> list[str]:
    """Les equipes que ce modele sait estimer, toutes divisions confondues."""
    return list(getattr(modele, "equipes", []))
