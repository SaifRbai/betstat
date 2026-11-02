"""Un modele commun aux premieres divisions europeennes, pour les coupes d'Europe.

Le probleme est celui de modele/inter_divisions.py, a l'echelle du continent :
Arsenal - Sporting n'est estimable que si la Premier League et la Primeira
Liga sont sur la meme echelle. Entre deux divisions d'un pays, ce sont les
promus et les relegues qui font le lien. Entre deux pays, il n'y a que les
matchs de coupe d'Europe — ici ceux de Ligue des champions (data/europe.py).

On entraine donc un seul Dixon-Coles sur les premieres divisions et les matchs
de Ligue des champions des memes saisons. Un championnat n'est retenu que s'il
est relie a la composante principale par au moins `SEUIL_MATCHS_PONT` matchs
europeens : sans eux, son niveau relatif n'est pas identifiable et ses clubs
recevraient des forces sans signification. Un club d'un championnat non charge
(Slavia Prague, Etoile rouge...) garde ses seuls matchs europeens : il est
estimable, mais sur une poignee de matchs, et la page le dit.

Ce que ce modele ne fournit PAS : aucune calibration ni aucun ROI n'ont ete
mesures sur des matchs de coupe d'Europe. Toute estimation qui en sort est
non validee, et doit etre presentee comme telle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

import config
from data import europe as europe_module
from data import resultats as resultats_module
from modele import dixon_coles as dc

_journal = logging.getLogger("foot_edge.modele_europe")

# Nombre minimal de matchs europeens entre un championnat et ceux deja retenus.
# Un seul match suffirait en theorie ; en pratique, il ferait reposer le niveau
# de tout un championnat sur un score. Dix, c'est moins d'une saison de phase de
# ligue pour un pays a deux clubs engages.
SEUIL_MATCHS_PONT = 10

# Le championnat de depart du parcours : le plus represente en Ligue des
# champions, donc celui par lequel tous les autres ont le plus de chances
# d'etre relies.
DEPART = "E0"


@dataclass
class RapportEurope:
    """Ce qui a ete retenu, ce qui a ete laisse, et pourquoi."""

    championnats: list[str] = field(default_factory=list)
    ecartes: dict[str, str] = field(default_factory=dict)
    ponts: dict[str, int] = field(default_factory=dict)
    matchs_europeens: int = 0
    nb_matchs: int = 0
    nb_equipes: int = 0

    def resume(self) -> str:
        return (f"Europe — {'+'.join(self.championnats)} — {self.nb_matchs} matchs "
                f"dont {self.matchs_europeens} europeens, {self.nb_equipes} equipes")


def premieres_divisions() -> list[str]:
    """La premiere division de chaque pays connu, dans l'ordre de config."""
    return [divisions[0] for divisions in config.DIVISIONS_PAR_PAYS.values()]


def _ligue_de(charges: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Equipe -> son championnat le plus recent parmi ceux charges."""
    ligue: dict[str, tuple[pd.Timestamp, str]] = {}
    for code, matchs in charges.items():
        for colonne in ("dom", "ext"):
            dernier = matchs.groupby(colonne)["date"].max()
            for equipe, quand in dernier.items():
                if equipe not in ligue or quand > ligue[equipe][0]:
                    ligue[equipe] = (quand, code)
    return {equipe: code for equipe, (_, code) in ligue.items()}


def composante_reliee(charges: dict[str, pd.DataFrame], europeens: pd.DataFrame,
                      depart: str = DEPART) -> tuple[list[str], dict[str, int]]:
    """Championnats relies a `depart` par assez de matchs europeens.

    Croissance gloutonne : un championnat rejoint la composante des que ses
    clubs ont dispute au moins `SEUIL_MATCHS_PONT` matchs europeens contre des
    clubs des championnats DEJA retenus, tous confondus. Exiger ce seuil paire
    par paire ecarterait la Belgique ou l'Ecosse, dont les clubs rencontrent
    chaque saison cinq championnats differents, jamais dix fois le meme — alors
    que c'est l'ensemble de ces matchs qui situe leur niveau.

    `ponts` donne, pour chaque championnat ajoute, le nombre de matchs qui l'ont
    relie au moment de son ajout.
    """
    ligue = _ligue_de(charges)
    paires: list[tuple[str, str]] = [
        (ligue[d], ligue[e]) for d, e in zip(europeens["dom"], europeens["ext"])
        if d in ligue and e in ligue and ligue[d] != ligue[e]]

    retenus = [depart] if depart in charges else []
    ponts: dict[str, int] = {}
    ajoute = bool(retenus)
    while ajoute:
        ajoute = False
        dans = set(retenus)
        comptes: dict[str, int] = {}
        for a, b in paires:
            if a in dans and b not in dans:
                comptes[b] = comptes.get(b, 0) + 1
            elif b in dans and a not in dans:
                comptes[a] = comptes.get(a, 0) + 1
        for code in charges:                          # ordre de config : stable
            if code not in dans and comptes.get(code, 0) >= SEUIL_MATCHS_PONT:
                ponts[code] = comptes[code]
                retenus.append(code)
                ajoute = True
    return [c for c in charges if c in retenus], ponts


def entrainer(saisons: int = 4, xi: float | None = None
              ) -> tuple[pd.DataFrame, dc.ModeleDixonColes, RapportEurope]:
    """Matchs d'entrainement, modele europeen et rapport de ce qui a ete retenu.

    Leve ValueError si aucun match de Ligue des champions n'est disponible :
    sans eux, les championnats ne sont pas comparables et on refuse d'estimer.
    """
    rapport = RapportEurope()
    charges: dict[str, pd.DataFrame] = {}
    for code in premieres_divisions():
        matchs = resultats_module.charger(code, saisons)
        if not matchs.empty:
            charges[code] = matchs
    if DEPART not in charges:
        raise ValueError(f"Aucun historique pour {DEPART} : modele europeen impossible.")

    connues = set().union(*(set(m["dom"]) | set(m["ext"]) for m in charges.values()))
    europeens = europe_module.charger(saisons, connues)
    if europeens.empty:
        raise ValueError(
            "Aucun match de Ligue des champions disponible (football-data.org : "
            "cle FOOTBALL_DATA_ORG_KEY absente ou reseau coupe, et pas de cache). "
            "Sans eux les championnats ne sont pas comparables : pas d'estimation.")

    retenus, ponts = composante_reliee(charges, europeens)
    rapport.championnats, rapport.ponts = retenus, ponts
    for code in charges:
        if code not in retenus:
            rapport.ecartes[code] = (
                f"moins de {SEUIL_MATCHS_PONT} matchs europeens avec les championnats "
                "retenus : niveau relatif non identifiable")

    # Les matchs europeens d'un club d'un championnat ecarte sont ecartes avec
    # lui : ils le relieraient a l'echelle commune par la seule coupe, alors
    # que ses dizaines de matchs de championnat, eux, ne sont pas charges.
    ligue = _ligue_de(charges)
    garder = europeens.apply(
        lambda m: all(ligue.get(e, "?") in retenus or e not in ligue
                      for e in (m["dom"], m["ext"])), axis=1)
    europeens = europeens.loc[garder]
    rapport.matchs_europeens = len(europeens)

    matchs = pd.concat([*(charges[c] for c in retenus), europeens], ignore_index=True)
    matchs = matchs.sort_values("date").reset_index(drop=True)
    rapport.nb_matchs = len(matchs)
    rapport.nb_equipes = len(set(matchs["dom"]) | set(matchs["ext"]))

    modele = dc.entrainer(matchs, cible="buts", xi=xi if xi is not None else dc.XI_DEFAUT)
    _journal.info("Modele europeen %s", rapport.resume())
    return matchs, modele, rapport
