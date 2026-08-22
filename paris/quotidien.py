"""Une selection par jour, toutes ligues confondues, sur le seul critere mesure.

Ce module existe pour repondre a une demande legitime — « donne-moi un pari par
jour » — sans inventer un avantage que les mesures ne montrent pas.

Ce qui a ete mesure, sur 4019 matchs hors echantillon, 6 championnats,
4 saisons, en pariant a la meilleure cote disponible :

    favori du marche, toutes forces           ROI  -1.0 %   IC95 [-4.1 %, +2.0 %]
    favori du marche a plus de 70 %           ROI  +2.0 %   IC95 [-2.5 %, +6.8 %]
    favori a >70 % ET cote >3 % au-dessus     ROI  +7.3 %   IC95 [-2.6 %, +16.2 %]
    favori DU MODELE                          ROI  -3.4 %   IC95 [-6.4 %, -0.1 %]

Deux conclusions, et elles ne sont pas du meme ordre :

  1. La seule regle dont l'intervalle exclut zero est **negative** : suivre le
     favori du modele fait perdre. Le modele Dixon-Coles n'est pas utilise ici,
     et c'est deliberе.
  2. Aucune regle positive n'est demontree. Celle retenue ci-dessous est la
     moins mal placee, pas une regle gagnante. Son intervalle contient zero :
     sur cet echantillon, on ne peut pas distinguer son rendement de l'absence
     de rendement.

La regle retenue : le favori du marche, au-dessus d'un seuil de probabilite,
pris chez le bookmaker qui le paie le mieux. L'avantage recherche n'est pas
predictif — c'est le prix. On ne prevoit rien de mieux que le marche ; on
essaie seulement de payer moins cher que sa moyenne.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

import config
from modele import calibration

_journal = logging.getLogger("foot_edge.quotidien")

SEUIL_PROBABILITE = 0.70     # en dessous, le ROI mesure retombe vers zero
PRIME_MINIMALE = 0.03        # la meilleure cote doit depasser la mediane de 3 %

# Mesures ci-dessus, transportees avec le code pour qu'aucun affichage ne
# puisse presenter cette regle sans son incertitude.
ROI_MESURE = 0.0204
ROI_IC = (-0.0249, 0.0683)
ROI_NB_PARIS = 545
ROI_NB_MATCHS = 4019


@dataclass
class Selection:
    """Un pari propose, avec tout ce qui permet de le refuser."""

    date: object
    championnat: str
    dom: str
    ext: str
    selection: str
    equipe: str
    p_marche: float
    cote: float
    bookmaker: str
    cote_mediane: float
    prime: float               # de combien la meilleure cote depasse la mediane
    nb_bookmakers: int
    esperance: float           # p_marche * cote - 1, sur la proba du marche

    @property
    def libelle(self) -> str:
        return f"{self.dom} - {self.ext} : {self.selection} ({self.equipe})"


def _mediane_par_selection(cotes: pd.DataFrame) -> pd.DataFrame:
    return (cotes.groupby(["date", "dom", "ext", "marche", "selection"], dropna=False)["cote"]
            .median().reset_index().rename(columns={"cote": "cote_mediane"}))


def candidats(cotes: pd.DataFrame, championnat: str,
              seuil: float = SEUIL_PROBABILITE,
              prime_minimale: float = PRIME_MINIMALE) -> list[Selection]:
    """Selections d'un championnat qui satisfont la regle mesuree."""
    # Un tableau de cotes partiel ne doit pas faire tomber la page : meme regle
    # que `cotes.par_bookmaker`, on ne propose rien plutot que d'echouer.
    if cotes.empty or not {"marche", "selection", "cote"}.issubset(cotes.columns):
        return []
    marche = cotes[cotes["marche"] == "1N2"]
    if marche.empty:
        return []
    try:
        consensus = calibration.consensus(marche)
    except (ValueError, KeyError) as erreur:
        _journal.info("Consensus indisponible pour %s : %s", championnat, erreur)
        return []
    if consensus.empty:
        return []

    consensus = consensus.merge(_mediane_par_selection(marche),
                                on=["date", "dom", "ext", "marche", "selection"], how="left")

    retenus: list[Selection] = []
    for _, groupe in consensus.groupby(["date", "dom", "ext"], dropna=False):
        favori = groupe.loc[groupe["p_marche"].idxmax()]
        if favori["p_marche"] < seuil:
            continue
        if favori["nb_bookmakers"] < config.MIN_BOOKMAKERS_CONSENSUS:
            continue
        mediane = float(favori.get("cote_mediane") or 0.0)
        cote = float(favori["meilleure_cote"])
        prime = (cote / mediane - 1.0) if mediane > 0 else 0.0
        if prime < prime_minimale:
            continue
        equipe = {"1": favori["dom"], "2": favori["ext"]}.get(favori["selection"], "le nul")
        retenus.append(Selection(
            date=favori["date"], championnat=championnat.upper(),
            dom=favori["dom"], ext=favori["ext"],
            selection=str(favori["selection"]), equipe=str(equipe),
            p_marche=float(favori["p_marche"]), cote=cote,
            bookmaker=str(favori["bookmaker_meilleure"]),
            cote_mediane=mediane, prime=float(prime),
            nb_bookmakers=int(favori["nb_bookmakers"]),
            esperance=float(favori["p_marche"]) * cote - 1.0,
        ))
    return retenus


def du_jour(par_championnat: dict[str, pd.DataFrame],
            seuil: float = SEUIL_PROBABILITE,
            prime_minimale: float = PRIME_MINIMALE) -> list[Selection]:
    """Toutes les selections eligibles, toutes ligues, de la plus chere payee a la moins.

    Le tri se fait sur la prime — l'ecart entre la meilleure cote et la mediane —
    et non sur l'esperance : l'esperance est calculee avec la probabilite du
    marche lui-meme, donc elle ne distingue que le prix. Autant trier sur le
    prix directement, c'est la meme information, dite honnetement.
    """
    tout: list[Selection] = []
    for championnat, cotes in par_championnat.items():
        tout.extend(candidats(cotes, championnat, seuil, prime_minimale))
    tout.sort(key=lambda s: s.prime, reverse=True)
    return tout


def mise(bankroll: float, fraction: float = 0.01) -> float:
    """Mise plate, volontairement.

    Kelly demande une probabilite a laquelle on croit plus qu'au marche. Ici on
    prend celle du marche : Kelly n'aurait aucun sens, il amplifierait un
    avantage qu'on ne revendique pas. Une fraction fixe et modeste, plafonnee
    comme partout ailleurs dans le projet.
    """
    plafond = bankroll * config.PLAFOND_MISE_BANKROLL
    return round(min(bankroll * fraction, plafond), 2)


def avertissement() -> str:
    bas, haut = ROI_IC
    return (
        f"Regle mesuree sur {ROI_NB_MATCHS} matchs hors echantillon : "
        f"ROI {ROI_MESURE:+.1%} par pari, intervalle de confiance a 95 % "
        f"[{bas:+.1%}, {haut:+.1%}] sur {ROI_NB_PARIS} paris.\n"
        "  L'intervalle CONTIENT ZERO : ce rendement n'est pas distinguable de "
        "zero sur cet echantillon. C'est la moins mauvaise regle testee, pas une "
        "regle gagnante.\n"
        "  Elle n'utilise pas le modele Dixon-Coles : suivre le favori du modele "
        "a rendu -3.4 % (IC [-6.4 %, -0.1 %]), seule regle dont l'intervalle "
        "exclut zero, et il exclut zero du mauvais cote."
    )


# --------------------------------------------------------------------------
# Combine vers un objectif de gain
# --------------------------------------------------------------------------
# « 10 EUR qui rapportent 90 » veut dire une cote de 9. En pari simple, c'est la
# seule bande de cotes dont le backtest prouve la perte : sur 4019 matchs,
# 6 championnats, les selections cotees 7 a 12 ont rendu -31.7 % avec un
# intervalle a 95 % de [-47.4 %, -13.9 %] qui n'atteint jamais zero. Toutes les
# autres bandes sont indistinguables de zero ; celle-la est perdante, c'est
# etabli.
#
# La seule facon d'atteindre x9 sans passer par cette bande est d'empiler des
# favoris. Ce n'est pas plus sur pour autant : les probabilites se multiplient,
# les marges des bookmakers aussi. Ce module calcule les deux et les montre
# ensemble, parce que c'est le rapport entre elles qui informe.

ROI_BANDE_LONGUE = -0.317          # cotes 7 a 12, mesure
ROI_BANDE_LONGUE_IC = (-0.474, -0.139)
ROI_BANDE_LONGUE_N = 740


@dataclass
class CombineCible:
    """Un empilement de favoris atteignant un objectif de gain."""

    jambes: list[Selection]
    cote_totale: float
    probabilite: float          # que TOUTES passent, probabilites du marche
    mise: float
    cible: float
    objectif_atteint: bool = True

    @property
    def gain(self) -> float:
        return self.mise * self.cote_totale

    @property
    def cote_equitable(self) -> float:
        return 1.0 / self.probabilite if self.probabilite > 0 else float("inf")

    @property
    def esperance(self) -> float:
        return self.probabilite * self.cote_totale - 1.0

    @property
    def une_fois_sur(self) -> float:
        return 1.0 / self.probabilite if self.probabilite > 0 else float("inf")

    @property
    def marge_payee(self) -> float:
        """De combien la cote offerte est inferieure a la cote equitable."""
        return 1.0 - self.cote_totale / self.cote_equitable if self.probabilite > 0 else 1.0

    def sur_une_annee(self, jours: int = 365) -> dict:
        """Ce que donne le meme pari repete, en esperance."""
        return {
            "jours": jours,
            "engage": jours * self.mise,
            "retour_attendu": jours * self.mise * self.probabilite * self.cote_totale,
            "jours_gagnants": jours * self.probabilite,
        }


def combine_vers(candidats: list[Selection], cible: float = 9.0,
                 mise: float = 10.0, maximum_jambes: int = 15,
                 au_mieux: bool = False) -> CombineCible | None:
    """Empile les favoris les plus surs jusqu'a atteindre `cible` fois la mise.

    On prend les plus probables d'abord : c'est ce qui minimise le nombre de
    jambes, donc le nombre d'occasions de tout perdre. Les probabilites sont
    celles du marche, pas celles du modele — le backtest a mesure que le poids
    du modele doit valoir zero, l'utiliser ici serait incoherent.

    L'independance entre matchs est supposee. Elle est defendable ici (des
    rencontres differentes, souvent dans des championnats differents), a la
    difference d'un combine de deux marches du meme match.

    `au_mieux` renvoie le meilleur empilement possible au lieu de None quand la
    cible est hors d'atteinte. Une journee ordinaire n'offre que deux ou trois
    favoris eligibles : refuser de repondre laisserait croire a une panne, alors
    que la vraie information est « voila tout ce qu'il y a, et ca ne suffit pas ».
    Le resultat porte alors `objectif_atteint = False`, et aucun affichage n'a le
    droit de presenter un tel combine comme ayant atteint la cible demandee.
    """
    ordonnes = sorted(candidats, key=lambda s: s.p_marche, reverse=True)
    jambes: list[Selection] = []
    cote = 1.0
    probabilite = 1.0
    for selection in ordonnes:
        if cote >= cible or len(jambes) >= maximum_jambes:
            break
        jambes.append(selection)
        cote *= selection.cote
        probabilite *= selection.p_marche
    atteint = bool(jambes) and cote >= cible
    if not atteint and not (au_mieux and jambes):
        return None
    return CombineCible(jambes=jambes, cote_totale=cote, probabilite=probabilite,
                        mise=mise, cible=cible, objectif_atteint=atteint)


def avertissement_combine(combine: CombineCible) -> str:
    """Ce qu'il faut savoir avant de jouer, en une phrase par fait."""
    bas, haut = ROI_BANDE_LONGUE_IC
    annee = combine.sur_une_annee()
    return (
        f"Ce combine passe environ 1 fois sur {combine.une_fois_sur:.0f}.\n"
        f"  Sa cote equitable serait {combine.cote_equitable:.2f} ; on t'en offre "
        f"{combine.cote_totale:.2f}, soit {combine.marge_payee:.1%} de marge payee "
        f"aux bookmakers — chaque jambe ajoute la sienne, et elles se multiplient.\n"
        f"  Esperance {combine.esperance:+.1%}. Repete chaque jour pendant un an : "
        f"{annee['engage']:.0f} EUR engages, {annee['retour_attendu']:.0f} EUR "
        f"attendus en retour, gagnant environ {annee['jours_gagnants']:.0f} jours "
        f"sur {annee['jours']}.\n"
        f"  L'alternative en pari simple — une seule selection cotee {combine.cible:.0f} "
        f"— est pire : {ROI_BANDE_LONGUE:+.1%} mesures sur {ROI_BANDE_LONGUE_N} paris, "
        f"intervalle [{bas:+.1%}, {haut:+.1%}], le seul de toutes les bandes de cotes "
        "dont la perte soit statistiquement etablie."
    )
