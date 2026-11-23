"""Classement des marches d'un match par probabilite, sans promettre de certitude.

Ce module repond a une demande legitime — « montre-moi les pronos les moins
risques » — sans mentir sur ce qu'il montre. Aucun pari n'est sans risque, et
un pari a 95 % est dangereux precisement parce qu'il n'en a pas l'air :

  - il echoue une fois sur vingt, et cette fois-la annule dix-neuf gains ;
  - sa cote equitable est 1.05, donc trois points d'erreur d'estimation
    suffisent a rendre le pari perdant, alors qu'a 50 % il faudrait se
    tromper enormement pour changer le signe de l'esperance.

D'ou les trois colonnes rendues pour chaque prono : la probabilite, ce qu'elle
vaut en cote, et ce qu'une seule perte coute. Les reserves (peu de donnees,
desaccord avec le marche) sont attachees a la ligne, pas releguees en note de
bas de page : c'est ce qui distingue une forte probabilite d'une forte
probabilite *fiable*.

Ce module ne calcule aucune mise et ne recommande rien. La decision de parier
passe par paris/valeur.py, qui exige en plus une cote et une esperance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import config
from modele import calibration

# Au-dessus de ce seuil le modele est tres affirmatif ; en dessous du second,
# il ne dit pas grand-chose. Les deux valeurs sont des conventions d'affichage,
# pas des seuils de decision : aucun pari n'est declenche par ce module.
SEUIL_TRES_PROBABLE = 0.85
SEUIL_PROBABLE = 0.70

NIVEAU_TRES_PROBABLE = "tres probable"
NIVEAU_PROBABLE = "probable"
NIVEAU_INCERTAIN = "incertain"
NIVEAU_RESERVE = "sous reserve"


@dataclass
class Prono:
    """Un marche, sa probabilite, et le prix de son apparente securite."""

    marche: str
    selection: str
    libelle: str
    p_modele: float
    p_marche: float | None
    cote_equitable: float
    echoue_1_fois_sur: float
    gains_annules_par_une_perte: float
    niveau: str
    reserves: list[str] = field(default_factory=list)

    @property
    def cote_le_dit(self) -> str:
        """Phrase courte sur ce que coute un echec, pour l'affichage."""
        return (f"echoue 1 fois sur {self.echoue_1_fois_sur:.0f} — "
                f"cette fois-la annule {self.gains_annules_par_une_perte:.0f} gain(s)")


# --------------------------------------------------------------------------
# Catalogue des marches derives du modele
# --------------------------------------------------------------------------

# (marche, selection, libelle, chemin dans la prediction, cle du marche cote)
# La derniere colonne vaut None quand aucun bookmaker ne cote ce marche via
# The Odds API : la ligne est alors invalidable par le marche, ce qui est une
# information en soi et non un detail.
#
# Un marche absent de la prediction est simplement omis. C'est un module
# d'affichage : il s'adapte a ce que le modele expose plutot que d'imposer un
# contrat qui ferait echouer la page entiere pour une cle manquante.
def _catalogue(prediction: dict, dom: str, ext: str) -> list[tuple]:
    p1n2 = prediction.get("1N2", {})
    dc = prediction.get("double_chance", {})
    totaux = prediction.get("totaux", {})
    btts = prediction.get("btts", {})
    cs = prediction.get("clean_sheet", {})

    candidats: list[tuple] = [
        ("1N2", "1", f"Victoire {dom}", p1n2.get("1"), ("1N2", "1")),
        ("1N2", "N", "Match nul", p1n2.get("N"), ("1N2", "N")),
        ("1N2", "2", f"Victoire {ext}", p1n2.get("2"), ("1N2", "2")),
        ("double chance", "1N", f"{dom} ou nul", dc.get("1N"), None),
        ("double chance", "12", "Pas de match nul", dc.get("12"), None),
        ("double chance", "N2", f"{ext} ou nul", dc.get("N2"), None),
        ("btts", "oui", "Les deux equipes marquent", btts.get("oui"), None),
        ("btts", "non", "Une equipe au moins ne marque pas", btts.get("non"), None),
        ("clean sheet", "dom", f"{dom} n'encaisse pas", cs.get("dom"), None),
        ("clean sheet", "ext", f"{ext} n'encaisse pas", cs.get("ext"), None),
    ]
    for seuil in (0.5, 1.5, 2.5, 3.5, 4.5):
        cotee = seuil == 2.5
        candidats.append((f"totaux {seuil}", "plus", f"Plus de {seuil} buts",
                          totaux.get(f"plus_{seuil}"),
                          ("plus_moins_2.5", "plus") if cotee else None))
        candidats.append((f"totaux {seuil}", "moins", f"Moins de {seuil} buts",
                          totaux.get(f"moins_{seuil}"),
                          ("plus_moins_2.5", "moins") if cotee else None))
    return [ligne for ligne in candidats if ligne[3] is not None]


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------

def _niveau(probabilite: float, reserves: list[str]) -> str:
    if reserves:
        return NIVEAU_RESERVE
    if probabilite >= SEUIL_TRES_PROBABLE:
        return NIVEAU_TRES_PROBABLE
    if probabilite >= SEUIL_PROBABLE:
        return NIVEAU_PROBABLE
    return NIVEAU_INCERTAIN


def prono(marche: str, selection: str, libelle: str, p_modele: float,
          p_marche: float | None = None,
          reserves_communes: list[str] | None = None) -> Prono:
    """Un prono complet a partir d'une probabilite de modele.

    `reserves_communes` porte ce qui vaut pour tout le match (equipe sans
    historique, par exemple) ; le desaccord avec le marche est propre a la
    ligne et calcule ici.
    """
    p = min(max(float(p_modele), 1e-9), 1.0 - 1e-9)
    reserves = list(reserves_communes or [])
    if p_marche is not None and abs(p - p_marche) > config.SEUIL_DIVERGENCE_ALERTE:
        reserves.append(
            f"le marche dit {p_marche:.0%}, soit {abs(p - p_marche):.0%} d'ecart : "
            "c'est presque toujours une information qui manque au modele")
    return Prono(
        marche=marche, selection=selection, libelle=libelle,
        p_modele=p, p_marche=p_marche,
        cote_equitable=1.0 / p,
        echoue_1_fois_sur=1.0 / (1.0 - p),
        gains_annules_par_une_perte=p / (1.0 - p),
        niveau=_niveau(p, reserves), reserves=reserves,
    )


def lister(prediction: dict, dom: str, ext: str,
           p_marche: dict[tuple[str, str], float] | None = None,
           reserves_communes: list[str] | None = None) -> list[Prono]:
    """Tous les marches derives du modele, du plus probable au moins probable.

    `p_marche` donne les probabilites de marche deviggees pour les selections
    que The Odds API cote, indexees par (marche, selection).
    """
    p_marche = p_marche or {}
    pronos = [
        prono(marche, selection, libelle, probabilite,
              p_marche.get(cle) if cle else None, reserves_communes)
        for marche, selection, libelle, probabilite, cle in _catalogue(prediction, dom, ext)
    ]
    pronos.sort(key=lambda p: p.p_modele, reverse=True)
    return pronos


# --------------------------------------------------------------------------
# Prono 1N2 : le seul pari que tout le monde veut voir
# --------------------------------------------------------------------------

# Pour chaque issue seche, la double chance qui la couvre en incluant le nul.
# Le nul est couvert des deux cotes : on prend alors la plus probable.
_COUVERTURES: dict[str, tuple[str, ...]] = {"1": ("1N",), "N": ("1N", "N2"), "2": ("N2",)}

_LIBELLES_1N2 = {"1": "Victoire {dom}", "N": "Match nul", "2": "Victoire {ext}"}
_LIBELLES_DC = {"1N": "{dom} ou nul", "N2": "{ext} ou nul", "12": "Pas de match nul"}


def prono_1n2(prediction: dict, dom: str, ext: str,
              p_marche: dict[tuple[str, str], float] | None = None,
              meilleures_cotes: dict[str, float] | None = None,
              reserves_communes: list[str] | None = None,
              a_melange: float = 0.3) -> dict | None:
    """L'issue la plus probable du match, avec sa version couverte par le nul.

    Trois probabilites cohabitent et il faut les distinguer, sinon on croit a
    une valeur qui n'existe pas :
      - `p_modele`  : ce que dit le modele seul ;
      - `p_marche`  : ce que disent les bookmakers, marge retiree ;
      - `p_finale`  : le melange des deux, et c'est la seule qui doit servir a
        calculer une esperance. Sur un match ou le modele dit 44 % et le marche
        35 %, calculer l'esperance sur les 44 % afficherait un avantage de 26 %
        qui est presque entierement l'erreur du modele.

    C'est le meme melange geometrique que paris/valeur.py : les deux vues du
    meme match ne peuvent donc pas se contredire.
    """
    issues = prediction.get("1N2")
    if not issues:
        return None
    p_marche = p_marche or {}
    cotes = meilleures_cotes or {}

    selection = max(("1", "N", "2"), key=lambda s: issues.get(s, 0.0))
    p_modele = float(issues[selection])
    p_du_marche = p_marche.get(("1N2", selection))
    p_finale = p_modele
    if p_du_marche is not None:
        p_finale = float(calibration.melange_geometrique(
            np.array([p_modele, 1 - p_modele]),
            np.array([p_du_marche, 1 - p_du_marche]), a_melange)[0])

    ligne = prono("1N2", selection, _LIBELLES_1N2[selection].format(dom=dom, ext=ext),
                  p_modele, p_du_marche, reserves_communes)

    cote = cotes.get(selection)
    esperance = p_finale * cote - 1.0 if cote else None

    # La couverture ne se justifie que par ce qu'elle coute : elle monte la
    # probabilite et ecrase la cote. Les deux chiffres vont ensemble.
    dc = prediction.get("double_chance", {})
    couverture = None
    candidats = [c for c in _COUVERTURES[selection] if c in dc]
    if candidats:
        cle = max(candidats, key=lambda c: dc[c])
        p_dc = float(dc[cle])
        couverture = {
            "selection": cle,
            "libelle": _LIBELLES_DC[cle].format(dom=dom, ext=ext),
            "p_modele": p_dc,
            "cote_equitable": 1.0 / p_dc if p_dc > 0 else None,
            "gagne_en_probabilite": p_dc - p_modele,
            "perd_en_cote": ligne.cote_equitable - (1.0 / p_dc if p_dc > 0 else 0.0),
        }

    return {
        "selection": selection,
        "libelle": ligne.libelle,
        "p_modele": p_modele,
        "p_marche": p_du_marche,
        "p_finale": p_finale,
        "cote_equitable": ligne.cote_equitable,
        "meilleure_cote": cote,
        "esperance": esperance,
        "echoue_1_fois_sur": ligne.echoue_1_fois_sur,
        "niveau": ligne.niveau,
        "reserves": ligne.reserves,
        "accord_marche": None if p_du_marche is None else abs(p_modele - p_du_marche),
        "couverture": couverture,
    }


# --------------------------------------------------------------------------
# Le choix le plus sur d'un match
# --------------------------------------------------------------------------

# Sous cette cote, le pari « sur » ne rapporte presque rien et une seule perte
# efface une longue serie de gains : a 1.15, il faut gagner sept fois pour
# rembourser un echec. Ce n'est plus une facon de jouer le match.
COTE_MINIMALE_SURE = 1.20

# En dessous, rien n'est « sur » sur ce match : on le dit plutot que d'elire
# le moins mauvais candidat.
SEUIL_CHOIX_SUR = 0.60

_DOUBLES_CHANCES = {"1N": ("1", "N"), "N2": ("N", "2"), "12": ("1", "2")}


def _melange(p_modele: float, p_marche: float | None, a: float) -> float:
    if p_marche is None:
        return p_modele
    return float(calibration.melange_geometrique(
        np.array([p_modele, 1 - p_modele]), np.array([p_marche, 1 - p_marche]), a)[0])


def choix_risque(prediction: dict, dom: str, ext: str,
                 p_marche: dict[tuple[str, str], float] | None = None,
                 meilleures_cotes: dict[str, float] | None = None,
                 a_melange: float = 0.3) -> dict | None:
    """Le pari le plus probable selon le modele, quand le choix « sur » n'existe pas.

    Affiche a part, dans une autre couleur : ce n'est PAS un choix sur. Il ne
    passe aucun des filtres de `choix_le_plus_sur` (accord avec le marche,
    seuil de 60 %), il dit seulement ce que le modele jouerait. Sans cote
    connue, le prix affiche est la cote equitable du modele (1/p) : a comparer
    avec celle du bookmaker, pas a jouer telle quelle.

    Jamais repris dans la liste des pronos surs (`pronos_surs` lit `choix`).
    """
    p_marche = p_marche or {}
    cotes = meilleures_cotes or {}
    issues = prediction.get("1N2") or {}
    totaux = prediction.get("totaux") or {}
    doubles = prediction.get("double_chance") or {}

    candidats: list[dict] = []

    def ajouter(marche, selection, libelle, p_mod, p_mar, cote):
        if not p_mod or p_mod <= 0:
            return
        p_mod = float(p_mod)
        reelle = bool(cote)
        p_fin = _melange(p_mod, p_mar, a_melange)
        prix = float(cote) if reelle else 1.0 / p_fin
        if prix < COTE_MINIMALE_SURE:
            return
        candidats.append({
            "marche": marche, "selection": selection, "libelle": libelle,
            "p_modele": p_mod, "p_marche": p_mar, "p_finale": p_fin,
            "cote": round(prix, 2), "cote_reelle": reelle,
            "esperance": p_fin * prix - 1.0 if reelle else None,
            "echoue_1_fois_sur": 1.0 / max(1.0 - p_fin, 1e-9),
            "desaccord_marche": (p_mar is not None
                                 and abs(p_mod - p_mar) > config.SEUIL_DIVERGENCE_ALERTE),
        })

    for sel in ("1", "N", "2"):
        ajouter("1N2", sel, _LIBELLES_1N2[sel].format(dom=dom, ext=ext),
                issues.get(sel), p_marche.get(("1N2", sel)), cotes.get(sel))
    for sel, libelle in (("plus", "Plus de 2.5 buts"), ("moins", "Moins de 2.5 buts")):
        ajouter("totaux 2.5", sel, libelle, totaux.get(f"{sel}_2.5"),
                p_marche.get(("plus_moins_2.5", sel)), cotes.get(sel))
    for sel, (a, b) in _DOUBLES_CHANCES.items():
        ca, cb = cotes.get(a), cotes.get(b)
        pa, pb = p_marche.get(("1N2", a)), p_marche.get(("1N2", b))
        ajouter("double chance", sel, _LIBELLES_DC[sel].format(dom=dom, ext=ext),
                doubles.get(sel), None if pa is None or pb is None else pa + pb,
                1.0 / (1.0 / ca + 1.0 / cb) if ca and cb else None)

    if not candidats:
        return None
    candidats.sort(key=lambda c: c["p_finale"], reverse=True)
    return candidats[0]


def choix_le_plus_sur(prediction: dict, dom: str, ext: str,
                      p_marche: dict[tuple[str, str], float] | None = None,
                      meilleures_cotes: dict[str, float] | None = None,
                      reserves_communes: list[str] | None = None,
                      a_melange: float = 0.3) -> dict | None:
    """La facon de jouer ce match qui a le plus de chances de passer.

    Seulement parmi ce qu'on peut REELLEMENT jouer a un prix connu :
      - 1N2 et plus/moins 2.5 buts, a la meilleure cote des bookmakers ;
      - double chance, au prix obtenu en repartissant la mise sur les deux
        issues chez les bookmakers (1/(1/c1 + 1/c2)) : c'est un prix reel,
        pas une estimation, et un bookmaker qui cote la double chance offre
        en general un peu moins.

    La probabilite retenue est le melange modele/marche (`p_finale`), comme
    partout ou une decision est en jeu. Sont ecartes : un choix sous
    `COTE_MINIMALE_SURE`, et un choix ou modele et marche divergent de plus de
    `config.SEUIL_DIVERGENCE_ALERTE` — un « sur » que le marche conteste ne
    l'est pas.

    « Sur » ne veut pas dire rentable : l'esperance est rendue telle quelle, et
    elle est le plus souvent negative, parce que la marge du bookmaker pese
    d'abord sur les favoris.
    """
    p_marche = p_marche or {}
    cotes = meilleures_cotes or {}
    issues = prediction.get("1N2") or {}
    totaux = prediction.get("totaux") or {}
    doubles = prediction.get("double_chance") or {}
    reserves = list(reserves_communes or [])

    candidats: list[dict] = []

    def ajouter(marche, selection, libelle, p_mod, p_mar, cote, comment):
        if p_mod is None or not cote:
            return
        p_mod = float(p_mod)
        if p_mar is not None and abs(p_mod - p_mar) > config.SEUIL_DIVERGENCE_ALERTE:
            return
        if cote < COTE_MINIMALE_SURE:
            return
        p_fin = _melange(p_mod, p_mar, a_melange)
        candidats.append({
            "marche": marche, "selection": selection, "libelle": libelle,
            "p_modele": p_mod, "p_marche": p_mar, "p_finale": p_fin,
            "cote": round(float(cote), 2), "comment": comment,
            "esperance": p_fin * cote - 1.0,
            "echoue_1_fois_sur": 1.0 / max(1.0 - p_fin, 1e-9),
        })

    for sel in ("1", "N", "2"):
        ajouter("1N2", sel, _LIBELLES_1N2[sel].format(dom=dom, ext=ext),
                issues.get(sel), p_marche.get(("1N2", sel)), cotes.get(sel),
                "chez le bookmaker qui offre la meilleure cote")
    for sel, libelle in (("plus", "Plus de 2.5 buts"), ("moins", "Moins de 2.5 buts")):
        ajouter("totaux 2.5", sel, libelle, totaux.get(f"{sel}_2.5"),
                p_marche.get(("plus_moins_2.5", sel)), cotes.get(sel),
                "chez le bookmaker qui offre la meilleure cote")
    for sel, (a, b) in _DOUBLES_CHANCES.items():
        ca, cb = cotes.get(a), cotes.get(b)
        if not (ca and cb):
            continue
        pa, pb = p_marche.get(("1N2", a)), p_marche.get(("1N2", b))
        cote = 1.0 / (1.0 / ca + 1.0 / cb)
        part_a = (1.0 / ca) * cote                    # part de la mise sur `a`
        ajouter("double chance", sel, _LIBELLES_DC[sel].format(dom=dom, ext=ext),
                doubles.get(sel), None if pa is None or pb is None else pa + pb, cote,
                f"double chance du bookmaker, ou mise repartie {part_a:.0%} sur "
                f"« {a} » (cote {ca:.2f}) et {1 - part_a:.0%} sur « {b} » (cote {cb:.2f})")

    if not candidats:
        return {"choix": None, "alternatives": [], "sur": False, "reserves": reserves,
                "choix_risque": choix_risque(prediction, dom, ext, p_marche, cotes,
                                             a_melange),
                "explication": "aucun marche cote au-dessus de "
                               f"{COTE_MINIMALE_SURE:.2f} sur lequel modele et "
                               "marche s'accordent"}
    candidats.sort(key=lambda c: c["p_finale"], reverse=True)
    choix = candidats[0]
    if choix["p_finale"] < SEUIL_CHOIX_SUR:
        # Elire un nul a 17 % « choix le plus sur » serait absurde : quand
        # rien n'atteint le seuil, il n'y a pas de facon sure de jouer ce match.
        return {"choix": None, "alternatives": [], "sur": False, "reserves": reserves,
                "choix_risque": choix_risque(prediction, dom, ext, p_marche, cotes,
                                             a_melange),
                "explication": f"rien n'atteint {SEUIL_CHOIX_SUR:.0%} parmi les paris "
                               "sur lesquels modele et marche s'accordent"}
    return {
        "choix": choix,
        "alternatives": candidats[1:3],
        "sur": choix["p_finale"] >= SEUIL_CHOIX_SUR and not reserves,
        "seuil": SEUIL_CHOIX_SUR,
        "cote_minimale": COTE_MINIMALE_SURE,
        "reserves": reserves,
        "explication": None,
    }


# --------------------------------------------------------------------------
# Corners et cartons jaunes
# --------------------------------------------------------------------------

# Au-dela de ce rapport, la loi de Poisson decrit mal la donnee et les gros
# totaux sont sous-estimes. La valeur est mesuree a l'entrainement.
SEUIL_DISPERSION_ALERTE = 1.20

RESERVE_SANS_MARCHE = (
    "aucun bookmaker ne cote ce marche via l'API : rien ne peut contredire "
    "cette estimation, contrairement au 1N2")


def prono_comptes(prediction: dict, dom: str, ext: str,
                  reserves_communes: list[str] | None = None) -> dict | None:
    """Prono sur un compte annexe (corners, cartons), avec ses reserves propres.

    La ligne retenue est celle qui encadre le total attendu — c'est ainsi qu'un
    bookmaker la choisirait — et on annonce le cote le plus probable.

    Ces pronos portent **toujours** au moins une reserve, et c'est voulu : le
    modele principal est confronte au marche a chaque match, celui-ci ne l'est
    jamais. Presenter les deux avec le meme aplomb serait le seul vrai mensonge
    de cette page.
    """
    totaux = prediction.get("totaux")
    if not totaux:
        return None

    attendu = float(prediction["attendu_total"])
    lignes = sorted({float(cle.split("_")[1]) for cle in totaux})
    ligne = min(lignes, key=lambda seuil: abs(seuil - attendu))
    p_plus = float(totaux[f"plus_{ligne}"])
    selection = "plus" if p_plus >= 0.5 else "moins"
    p_modele = p_plus if selection == "plus" else 1.0 - p_plus

    libelle_compte = prediction.get("libelle", prediction.get("type", "compte"))
    reserves = list(reserves_communes or [])
    reserves.append(RESERVE_SANS_MARCHE)
    dispersion = prediction.get("dispersion")
    if dispersion and dispersion > SEUIL_DISPERSION_ALERTE:
        reserves.append(
            f"dispersion mesuree {dispersion:.2f} (Poisson suppose 1.00) : les "
            "totaux extremes sont plus frequents que ce modele ne l'annonce")

    ligne_prono = prono(libelle_compte, selection,
                        f"{'Plus' if selection == 'plus' else 'Moins'} de "
                        f"{ligne} {libelle_compte}",
                        p_modele, None, reserves)

    comparaison = prediction.get("comparaison", {})
    duel = None
    if comparaison:
        gagnant = max(comparaison, key=lambda s: comparaison[s])
        equipe = {"1": dom, "2": ext}.get(gagnant)
        duel = {
            "selection": gagnant,
            "libelle": (f"{equipe} en obtient le plus" if equipe
                        else f"Autant de {libelle_compte} des deux cotes"),
            "p_modele": float(comparaison[gagnant]),
        }

    return {
        "type": prediction.get("type"),
        "championnat": prediction.get("championnat"),
        "libelle_compte": libelle_compte,
        "attendu_dom": prediction.get("attendu_dom"),
        "attendu_ext": prediction.get("attendu_ext"),
        "attendu_total": attendu,
        "ligne": ligne,
        "selection": selection,
        "libelle": ligne_prono.libelle,
        "p_modele": p_modele,
        "cote_equitable": ligne_prono.cote_equitable,
        "echoue_1_fois_sur": ligne_prono.echoue_1_fois_sur,
        "niveau": ligne_prono.niveau,
        "reserves": ligne_prono.reserves,
        "dispersion": dispersion,
        "matchs": prediction.get("matchs"),
        "duel": duel,
        "tous_les_totaux": [
            {"ligne": seuil,
             "plus": float(totaux[f"plus_{seuil}"]),
             "moins": float(totaux[f"moins_{seuil}"])}
            for seuil in lignes],
    }


# --------------------------------------------------------------------------
# Les plus probables, tous marches confondus (resultat, buts, corners, cartons)
# --------------------------------------------------------------------------

# Seuil d'affichage : sous 70 %, une selection echoue plus d'une fois sur trois.
SEUIL_PRUDENT = 0.70

# Probabilite prudente : une estimation que rien ne vient contredire est
# ramenee vers 50 %. Le modele de Dixon-Coles et les modeles de comptes sont
# en moyenne trop affirmatifs aux extremes (log-loss mesuree au-dessus de celle
# du marche, voir README) ; le retrait compense cet exces de confiance au lieu
# de le prendre au pied de la lettre. Plus la verification manque, plus le
# retrait est fort.
RETRAIT_SANS_MARCHE = 0.90         # marche principal non cote (btts, totaux != 2.5...)
RETRAIT_ANNEXE_VALIDEE = 0.90      # corners/cartons verifies contre les comptes reels
RETRAIT_ANNEXE_NON_VALIDEE = 0.80  # corners/cartons jamais verifies
RETRAIT_DISPERSION = 0.85          # facteur supplementaire si Poisson decrit mal la donnee
ECART_CALIBRATION_TOLERE = 0.05

# « Le plus sur de tous » : une seule selection par match, a 85 % prudents au
# moins. Echoue encore environ 1 fois sur 7 : tres probable, jamais certain.
SEUIL_TRES_SUR = 0.85
# Sous cette cote equitable, une perte efface plus de dix gains.
COTE_MINIMALE_TRES_SUR = 1.10

_FAMILLES = {"1N2": "resultat", "double chance": "resultat", "btts": "buts",
             "clean sheet": "buts"}


def _famille(marche: str) -> str:
    if marche.startswith("totaux"):
        return "buts"
    return _FAMILLES.get(marche, marche)


def _retrait(p: float, facteur: float) -> float:
    return 0.5 + (p - 0.5) * facteur


def selection_prudente(pronos: list[Prono], pronos_annexes: list[dict] | None = None,
                       p_marche: dict[tuple[str, str], float] | None = None,
                       reserves_communes: list[str] | None = None,
                       a_melange: float = 0.3, par_famille: int = 1) -> dict:
    """Les selections les plus probables du match, tous marches confondus.

    Ce n'est pas une liste de paris « sans risque » : il n'en existe pas. C'est,
    pour chaque famille de marches (resultat, buts, corners, cartons), la
    selection dont la probabilite *prudente* est la plus haute, a condition :
      - qu'aucune reserve ne pese sur le match ni sur la ligne ;
      - que le marche, quand il cote la selection, ne la conteste pas ;
      - que la cote equitable reste jouable (>= COTE_MINIMALE_SURE) : « plus
        de 0.5 buts » a 95 % n'est pas une facon de jouer un match ;
      - que la probabilite prudente atteigne SEUIL_PRUDENT.

    La probabilite prudente est le melange modele/marche quand le marche cote
    la selection, sinon la probabilite du modele ramenee vers 50 % (voir
    RETRAIT_*). C'est elle qui sert au classement et a l'affichage.
    """
    p_marche = p_marche or {}
    if reserves_communes:
        return {"selections": [], "meilleure": None, "le_plus_sur": None,
                "explication": "reserve sur le match : " + " ; ".join(reserves_communes)}

    candidats: list[dict] = []

    def ajouter(famille, marche, selection, libelle, p_mod, p_mar, p_pru, verifie, note):
        if p_pru < SEUIL_PRUDENT:
            return
        candidats.append({
            "famille": famille, "marche": marche, "selection": selection,
            "libelle": libelle, "p_modele": float(p_mod), "p_marche": p_mar,
            "p_prudente": float(p_pru), "cote_equitable": round(1.0 / p_pru, 2),
            "echoue_1_fois_sur": 1.0 / max(1.0 - p_pru, 1e-9),
            "verifie_par_le_marche": verifie, "note": note,
        })

    for p in pronos:
        if p.reserves:
            continue
        p_mar = p.p_marche
        if p_mar is None and p.marche == "double chance" and p.selection in _DOUBLES_CHANCES:
            a, b = _DOUBLES_CHANCES[p.selection]
            pa, pb = p_marche.get(("1N2", a)), p_marche.get(("1N2", b))
            if pa is not None and pb is not None:
                p_mar = pa + pb
        if p_mar is not None:
            if abs(p.p_modele - p_mar) > config.SEUIL_DIVERGENCE_ALERTE:
                continue
            p_pru = _melange(p.p_modele, p_mar, a_melange)
            note = "modele et marche d'accord"
        else:
            p_pru = _retrait(p.p_modele, RETRAIT_SANS_MARCHE)
            note = "non cote par l'API : estimation du modele, ramenee vers 50 %"
        ajouter(_famille(p.marche), p.marche, p.selection, p.libelle,
                p.p_modele, p_mar, p_pru, p_mar is not None, note)

    for annexe in pronos_annexes or []:
        propres = [r for r in annexe.get("reserves", [])
                   if r != RESERVE_SANS_MARCHE and not r.startswith("dispersion")]
        if propres:
            continue
        validation = annexe.get("validation") or {}
        fiable = (validation
                  and not validation.get("biais_significatif")
                  and (validation.get("ecart_calibration_max") or 1.0) <= ECART_CALIBRATION_TOLERE)
        facteur = RETRAIT_ANNEXE_VALIDEE if fiable else RETRAIT_ANNEXE_NON_VALIDEE
        dispersion = annexe.get("dispersion")
        if dispersion and dispersion > SEUIL_DISPERSION_ALERTE:
            facteur *= RETRAIT_DISPERSION
        compte = annexe.get("libelle_compte", "compte")
        note = ("aucun bookmaker ne cote ce marche ; modele "
                + ("verifie contre les comptes reels" if fiable else "jamais verifie")
                + ", estimation ramenee vers 50 %")
        for ligne in annexe.get("tous_les_totaux", []):
            for cote_ligne in ("plus", "moins"):
                p_mod = float(ligne[cote_ligne])
                ajouter(compte, compte, cote_ligne,
                        f"{'Plus' if cote_ligne == 'plus' else 'Moins'} de "
                        f"{ligne['ligne']} {compte}",
                        p_mod, None, _retrait(p_mod, facteur), False, note)

    candidats.sort(key=lambda c: c["p_prudente"], reverse=True)
    # Le plus sur de tous : une seule selection, la plus probable, meme a
    # petite cote — mais pas sous COTE_MINIMALE_TRES_SUR, ou le pari ne
    # rapporte plus rien (« plus de 0.5 buts » a 97 %).
    # « Plus/moins de 0.5 but » est exclu : les bookmakers le cotent autour de
    # 1.05, et il serait le « plus sur » de presque tous les matchs sans rien
    # dire du match lui-meme.
    tres_surs = [c for c in candidats if c["p_prudente"] >= SEUIL_TRES_SUR
                 and c["cote_equitable"] >= COTE_MINIMALE_TRES_SUR
                 and c["marche"] != "totaux 0.5"]
    le_plus_sur = tres_surs[0] if tres_surs else None

    retenus: list[dict] = []
    comptes: dict[str, int] = {}
    for c in candidats:
        if c["cote_equitable"] < COTE_MINIMALE_SURE:
            continue
        if comptes.get(c["famille"], 0) >= par_famille:
            continue
        comptes[c["famille"]] = comptes.get(c["famille"], 0) + 1
        retenus.append(c)
    if not retenus:
        return {"selections": [], "meilleure": None, "le_plus_sur": le_plus_sur,
                "seuil_tres_sur": SEUIL_TRES_SUR,
                "explication": f"aucune selection jouable n'atteint {SEUIL_PRUDENT:.0%} "
                               "en probabilite prudente"}
    return {"selections": retenus, "meilleure": retenus[0]["p_prudente"],
            "le_plus_sur": le_plus_sur, "seuil_tres_sur": SEUIL_TRES_SUR,
            "seuil": SEUIL_PRUDENT, "explication": None}


def les_plus_surs(pronos: list[Prono], combien: int = 3) -> list[Prono]:
    """Les pronos les plus probables *et* sans reserve.

    Peut renvoyer une liste vide, et c'est le cas interessant : un match ou
    rien ne ressort sans reserve est un match sur lequel l'outil n'a rien a
    dire. Le silence vaut mieux qu'un classement de mauvais candidats.
    """
    return [p for p in pronos if not p.reserves
            and p.p_modele >= SEUIL_PROBABLE][:combien]


def en_dictionnaires(pronos: list[Prono]) -> list[dict]:
    """Serialisation pour l'interface web."""
    return [{
        "marche": p.marche, "selection": p.selection, "libelle": p.libelle,
        "p_modele": p.p_modele, "p_marche": p.p_marche,
        "cote_equitable": p.cote_equitable,
        "echoue_1_fois_sur": p.echoue_1_fois_sur,
        "gains_annules": p.gains_annules_par_une_perte,
        "niveau": p.niveau, "reserves": p.reserves,
    } for p in pronos]
