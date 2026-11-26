"""Modele de Dixon-Coles, entrainable sur les buts, les xG ou un melange.

Formulation retenue :
    lambda_dom = exp(attaque_dom + defense_ext + gamma)
    lambda_ext = exp(attaque_ext + defense_dom)
Un parametre "defense" eleve signifie donc une equipe qui encaisse beaucoup.

Trois raffinements par rapport au Poisson naif :
  1. correction rho de Dixon-Coles sur les petits scores (0-0, 0-1, 1-0, 1-1),
     ou l'independance des deux Poisson est manifestement fausse ;
  2. ponderation temporelle exp(-xi * jours), les matchs anciens comptant moins ;
  3. cible d'entrainement au choix : buts reels, xG, ou melange w*xg+(1-w)*buts.
     Les xG n'etant pas entiers, on utilise une vraisemblance de Poisson
     continue : log(k!) est remplace par lgamma(k+1), ce qui reste une densite
     valide a une constante de normalisation pres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

Cible = Literal["buts", "xg", "melange", "tirs", "marche"]

TAILLE_MATRICE = 12          # scores 0 a 11 par equipe
XI_DEFAUT = 0.0018           # demi-vie d'environ 385 jours
POIDS_XG_DEFAUT = 0.7
BORNE_RHO = 0.35
# Penalite L2 sur attaque/defense (retrecissement vers la moyenne du championnat).
# Mesure walk-forward sur 4 saisons, log-loss 1N2 du modele, sans -> avec 3 :
# F1 0.9997->0.985, D1 1.034->1.004, E0 1.027->1.011, N1 1.014->0.997,
# SP1 0.981->0.976, I1 0.965->0.968. La valeur par championnat est recalibree
# par `cli.py calibrer` (cle "regularisation" des hyperparametres).
REGULARISATION_DEFAUT = 3.0

# Cible "marche" : buts implicites des cotes des matchs deja joues (voir
# modele/marche_implicite.py). La cible etant bien moins bruitee que le score,
# elle supporte un oubli plus rapide et un retrecissement plus faible. Valeurs
# retenues par walk-forward sur F1, E0, SP1, I1, D1, N1 (4 saisons), cible
# tiree des cotes de CLOTURE. Log-loss 1N2 moyenne des six championnats :
#   ouverture, w=0.8 xi=0.004 r=0.3 : 0.9824
#   cloture,   w=0.8 xi=0.004 r=0.3 : 0.9816
#   cloture,   w=0.9 xi=0.006 r=0.1 : 0.9807   <- retenu
# (marche de cloture de-vigge : ~0.968). Au-dela (xi=0.015 ou r=1) on degrade.
# Ajouter le handicap asiatique de cloture a l'inversion n'apporte rien (0.9812
# contre 0.9813), une recalibration a posteriori non plus : non retenus.
POIDS_MARCHE_DEFAUT = 0.9
XI_MARCHE_DEFAUT = 0.006
REGULARISATION_MARCHE_DEFAUT = 0.1


# Cible utilisee par l'application quand rien n'est precise.
CIBLE_DEFAUT: Cible = "marche"


def defauts_cible(cible: str) -> dict[str, float]:
    """xi, w et regularisation par defaut, selon la cible d'entrainement."""
    if cible == "marche":
        return {"xi": XI_MARCHE_DEFAUT, "w": POIDS_MARCHE_DEFAUT,
                "regularisation": REGULARISATION_MARCHE_DEFAUT}
    return {"xi": XI_DEFAUT, "w": POIDS_XG_DEFAUT, "regularisation": REGULARISATION_DEFAUT}


# --------------------------------------------------------------------------
# Structure du modele entraine
# --------------------------------------------------------------------------

@dataclass
class ModeleDixonColes:
    """Parametres estimes et methodes de prediction."""

    equipes: list[str]
    attaque: dict[str, float]
    defense: dict[str, float]
    attaque_ext: dict[str, float]
    defense_ext: dict[str, float]
    gamma: float
    rho: float
    xi: float
    cible: Cible
    poids_xg: float
    force_domicile_separee: bool
    nb_matchs: int
    log_vraisemblance: float
    convergence: bool
    ajustements: dict[str, tuple[float, float]] = field(default_factory=dict)

    # -- forces -------------------------------------------------------------

    def _att(self, equipe: str, domicile: bool) -> float:
        base = self.attaque[equipe] if domicile or not self.force_domicile_separee \
            else self.attaque_ext[equipe]
        correction = self.ajustements.get(equipe, (0.0, 0.0))[0]
        return base + correction

    def _def(self, equipe: str, domicile: bool) -> float:
        base = self.defense[equipe] if domicile or not self.force_domicile_separee \
            else self.defense_ext[equipe]
        correction = self.ajustements.get(equipe, (0.0, 0.0))[1]
        return base + correction

    def verifier_equipes(self, *equipes: str) -> None:
        inconnues = [e for e in equipes if e not in self.attaque]
        if inconnues:
            from data.noms import suggerer  # import tardif, evite un cycle

            details = []
            for equipe in inconnues:
                proches = ", ".join(nom for nom, _ in suggerer(equipe, self.equipes, 3))
                details.append(f"{equipe!r} (le plus proche : {proches})")
            raise KeyError(
                "Equipe absente du modele : " + " ; ".join(details) +
                ". Verifie l'orthographe ou charge une saison ou elle a joue."
            )

    def lambdas(self, dom: str, ext: str) -> tuple[float, float]:
        """Nombre de buts attendu pour chaque equipe."""
        self.verifier_equipes(dom, ext)
        lam_dom = math.exp(self._att(dom, True) + self._def(ext, False) + self.gamma)
        lam_ext = math.exp(self._att(ext, False) + self._def(dom, True))
        return lam_dom, lam_ext

    def ajuster(self, equipe: str, attaque_pct: float = 0.0, defense_pct: float = 0.0) -> None:
        """Correction manuelle en pourcentage (blessure, transfert, turnover).

        attaque_pct = +10 signifie "cette equipe marque 10 % de plus que ce que
        dit l'historique". defense_pct = +10 signifie "elle encaisse 10 % de plus".
        """
        self.verifier_equipes(equipe)
        for valeur in (attaque_pct, defense_pct):
            if valeur <= -100:
                raise ValueError("Un ajustement doit rester strictement superieur a -100 %.")
        self.ajustements[equipe] = (
            math.log(1 + attaque_pct / 100.0),
            math.log(1 + defense_pct / 100.0),
        )

    def reinitialiser_ajustements(self) -> None:
        self.ajustements.clear()

    def forces(self) -> pd.DataFrame:
        """Tableau des forces estimees, trie par note globale decroissante."""
        lignes = []
        for equipe in self.equipes:
            att = self._att(equipe, True)
            dfs = self._def(equipe, True)
            lignes.append({
                "equipe": equipe,
                "attaque": math.exp(att),
                "defense": math.exp(dfs),
                "note": math.exp(att - dfs),
            })
        tableau = pd.DataFrame(lignes).sort_values("note", ascending=False)
        return tableau.reset_index(drop=True)

    # -- prediction ---------------------------------------------------------

    def matrice_scores(self, dom: str, ext: str, taille: int = TAILLE_MATRICE) -> np.ndarray:
        """Matrice des probabilites de score exact, corrigee et normalisee."""
        lam_dom, lam_ext = self.lambdas(dom, ext)
        buts = np.arange(taille)
        p_dom = np.exp(-lam_dom + buts * np.log(lam_dom) - gammaln(buts + 1))
        p_ext = np.exp(-lam_ext + buts * np.log(lam_ext) - gammaln(buts + 1))
        matrice = np.outer(p_dom, p_ext)
        matrice[0, 0] *= 1 - lam_dom * lam_ext * self.rho
        matrice[0, 1] *= 1 + lam_dom * self.rho
        matrice[1, 0] *= 1 + lam_ext * self.rho
        matrice[1, 1] *= 1 - self.rho
        matrice = np.clip(matrice, 1e-15, None)
        return matrice / matrice.sum()

    def predire(self, dom: str, ext: str, taille: int = TAILLE_MATRICE) -> dict:
        """Tous les marches derives de la matrice de scores."""
        matrice = self.matrice_scores(dom, ext, taille)
        return construire_marches(matrice, dom, ext, *self.lambdas(dom, ext))


# --------------------------------------------------------------------------
# Derivation des marches
# --------------------------------------------------------------------------

def _proba_totaux(matrice: np.ndarray) -> dict[str, float]:
    taille = matrice.shape[0]
    totaux = np.add.outer(np.arange(taille), np.arange(taille))
    resultat: dict[str, float] = {}
    for seuil in (0.5, 1.5, 2.5, 3.5, 4.5):
        plus = float(matrice[totaux > seuil].sum())
        resultat[f"plus_{seuil}"] = plus
        resultat[f"moins_{seuil}"] = 1.0 - plus
    return resultat


def _proba_handicaps(matrice: np.ndarray) -> dict[str, dict[str, float]]:
    """Handicaps asiatiques de -2 a +2 par pas de 0.5, du point de vue du dom."""
    taille = matrice.shape[0]
    ecart = np.subtract.outer(np.arange(taille), np.arange(taille))
    handicaps: dict[str, dict[str, float]] = {}
    for demi in range(-4, 5):
        h = demi / 2.0
        gagne = float(matrice[ecart + h > 0].sum())
        perd = float(matrice[ecart + h < 0].sum())
        handicaps[f"{h:+.1f}"] = {
            "dom": gagne,
            "ext": perd,
            "remboursement": max(0.0, 1.0 - gagne - perd),
        }
    return handicaps


def construire_marches(
    matrice: np.ndarray, dom: str, ext: str, lam_dom: float, lam_ext: float
) -> dict:
    """Transforme une matrice de scores en probabilites de marches."""
    taille = matrice.shape[0]
    p_dom = float(np.tril(matrice, -1).sum())
    p_nul = float(np.trace(matrice))
    p_ext = float(np.triu(matrice, 1).sum())

    btts = float(matrice[1:, 1:].sum())
    scores = [
        (f"{i}-{j}", float(matrice[i, j]))
        for i in range(taille) for j in range(taille)
    ]
    scores.sort(key=lambda couple: couple[1], reverse=True)

    return {
        "dom": dom,
        "ext": ext,
        "lambda_dom": lam_dom,
        "lambda_ext": lam_ext,
        "1N2": {"1": p_dom, "N": p_nul, "2": p_ext},
        "double_chance": {"1N": p_dom + p_nul, "12": p_dom + p_ext, "N2": p_nul + p_ext},
        "totaux": _proba_totaux(matrice),
        "btts": {"oui": btts, "non": 1.0 - btts},
        "clean_sheet": {
            "dom": float(matrice[:, 0].sum()),
            "ext": float(matrice[0, :].sum()),
        },
        "handicap_asiatique": _proba_handicaps(matrice),
        "scores_probables": scores[:10],
        "buts_attendus": {"dom": lam_dom, "ext": lam_ext, "total": lam_dom + lam_ext},
    }


# --------------------------------------------------------------------------
# Entrainement
# --------------------------------------------------------------------------

COLONNES_TIRS = ("tirs_dom", "tirs_ext", "tirs_cadres_dom", "tirs_cadres_ext")


def coefficients_tirs(matchs: pd.DataFrame) -> tuple[float, float] | None:
    """Buts ~ a * tirs cadres + b * tirs non cadres, par moindres carres sans constante.

    Ajuste sur les matchs fournis, donc sur la seule periode d'entrainement :
    aucune information du futur n'entre dans les coefficients. None si les
    tirs manquent (sources ESPN, vieux CSV) ou sont trop rares pour estimer.
    """
    if not set(COLONNES_TIRS) <= set(matchs.columns):
        return None
    cadres = np.r_[matchs["tirs_cadres_dom"], matchs["tirs_cadres_ext"]].astype(float)
    tirs = np.r_[matchs["tirs_dom"], matchs["tirs_ext"]].astype(float)
    buts = np.r_[matchs["buts_dom"], matchs["buts_ext"]].astype(float)
    non_cadres = tirs - cadres
    valides = ~(np.isnan(cadres) | np.isnan(non_cadres) | np.isnan(buts)) & (non_cadres >= 0)
    if valides.sum() < 100:
        return None
    a, b = np.linalg.lstsq(np.c_[cadres[valides], non_cadres[valides]],
                           buts[valides], rcond=None)[0]
    return float(max(a, 0.0)), float(max(b, 0.0))


def xg_par_les_tirs(matchs: pd.DataFrame) -> tuple[np.ndarray, np.ndarray] | None:
    """« xG du pauvre » tire des tirs : disponible pour tous les championnats
    football-data, contrairement aux vrais xG (5 grands championnats).

    Mesure walk-forward sur 4 saisons, log-loss 1N2 du modele seul, buts ->
    0.7*tirs+0.3*buts : N1 0.9968->0.9861, D1 1.0082->1.0029, E1 1.0571->1.0519,
    E0 1.0103->1.0078 ; mais SP1 0.9761->0.9801, I1 0.9697->0.9767,
    P1 0.9420->0.9467. Le poids w se calibre donc par championnat.
    """
    coefficients = coefficients_tirs(matchs)
    if coefficients is None:
        return None
    a, b = coefficients
    def estimer(cote: str) -> np.ndarray:
        cadres = matchs[f"tirs_cadres_{cote}"].to_numpy(dtype=float)
        tirs = matchs[f"tirs_{cote}"].to_numpy(dtype=float)
        return a * cadres + b * np.clip(tirs - cadres, 0.0, None)
    return estimer("dom"), estimer("ext")


def cible_entrainement(matchs: pd.DataFrame, cible: Cible, poids_xg: float) -> tuple[np.ndarray, np.ndarray]:
    """Valeurs a expliquer par le modele, selon le mode choisi."""
    buts_dom = matchs["buts_dom"].to_numpy(dtype=float)
    buts_ext = matchs["buts_ext"].to_numpy(dtype=float)
    if cible == "buts":
        return buts_dom, buts_ext

    if cible == "marche":
        from modele.marche_implicite import buts_implicites  # import tardif : scipy.spatial

        colonnes = ("cote_1", "cote_N", "cote_2", "cote_plus25", "cote_moins25")
        if not set(colonnes) <= set(matchs.columns):
            # Source sans cotes (ESPN) : on retombe sur les buts, comme "tirs".
            return buts_dom, buts_ext
        cotes = [matchs[c].astype(float) for c in colonnes]
        # Cote de cloture quand elle existe (plus informee), sinon d'ouverture.
        cotes = [matchs[f"{c}_cloture"].astype(float).fillna(cote)
                 if f"{c}_cloture" in matchs.columns else cote
                 for c, cote in zip(colonnes, cotes)]
        # Cloture et ouverture ne se melangent pas a l'interieur d'un match : un
        # 1N2 de cloture avec un plus/moins d'ouverture donnerait une cible
        # incoherente. On ne garde la cloture que si les cinq cotes y sont.
        if all(f"{c}_cloture" in matchs.columns for c in colonnes):
            complet = np.all([matchs[f"{c}_cloture"].notna().to_numpy()
                              for c in colonnes], axis=0)
            cotes = [np.where(complet, cl, matchs[c].astype(float))
                     for c, cl in zip(colonnes, cotes)]
        lam, mu = buts_implicites(*cotes)
        # Un match sans cotes exploitables garde ses buts reels.
        lam = np.where(np.isnan(lam), buts_dom, lam)
        mu = np.where(np.isnan(mu), buts_ext, mu)
        w = float(np.clip(poids_xg, 0.0, 1.0))
        return w * lam + (1 - w) * buts_dom, w * mu + (1 - w) * buts_ext

    if cible == "tirs":
        estimes = xg_par_les_tirs(matchs)
        if estimes is None:
            # Pas de tirs dans cette source : on retombe sur les buts plutot
            # que d'echouer, le modele reste celui de la cible "buts".
            return buts_dom, buts_ext
        xg_dom = np.where(np.isnan(estimes[0]), buts_dom, estimes[0])
        xg_ext = np.where(np.isnan(estimes[1]), buts_ext, estimes[1])
        w = float(np.clip(poids_xg, 0.0, 1.0))
        return w * xg_dom + (1 - w) * buts_dom, w * xg_ext + (1 - w) * buts_ext

    if "xg_dom" not in matchs.columns:
        raise ValueError(
            "Mode 'xg' demande mais les colonnes xg_dom/xg_ext sont absentes.\n"
            "  Charge les donnees avec data.xg.charger_avec_xg(), ou repasse en cible='buts'."
        )
    xg_dom = matchs["xg_dom"].to_numpy(dtype=float)
    xg_ext = matchs["xg_ext"].to_numpy(dtype=float)
    # Un match sans xG retombe sur ses buts reels plutot que d'etre perdu.
    xg_dom = np.where(np.isnan(xg_dom), buts_dom, xg_dom)
    xg_ext = np.where(np.isnan(xg_ext), buts_ext, xg_ext)
    if cible == "xg":
        return xg_dom, xg_ext
    w = float(np.clip(poids_xg, 0.0, 1.0))
    return w * xg_dom + (1 - w) * buts_dom, w * xg_ext + (1 - w) * buts_ext


def poids_temporels(dates: pd.Series, xi: float, reference: pd.Timestamp | None = None) -> np.ndarray:
    """exp(-xi * nombre de jours ecoules) ; xi = 0 donne des poids uniformes."""
    dates = pd.to_datetime(dates)
    fin = reference if reference is not None else dates.max()
    jours = (pd.Timestamp(fin) - dates).dt.total_seconds().to_numpy() / 86400.0
    jours = np.clip(jours, 0.0, None)
    return np.exp(-xi * jours)


def _log_tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Correction de Dixon-Coles sur les quatre petits scores, en log."""
    tau = np.ones_like(lam)
    petits = (x <= 1) & (y <= 1)
    if not petits.any():
        return np.zeros_like(lam)
    tau = np.where((x == 0) & (y == 0), 1 - lam * mu * rho, tau)
    tau = np.where((x == 0) & (y == 1), 1 + lam * rho, tau)
    tau = np.where((x == 1) & (y == 0), 1 + mu * rho, tau)
    tau = np.where((x == 1) & (y == 1), 1 - rho, tau)
    return np.log(np.clip(tau, 1e-10, None))


def _log_poisson_continu(k: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """log densite de Poisson valable pour k non entier (lgamma au lieu de log k!)."""
    return k * np.log(lam) - lam - gammaln(k + 1.0)


def _indices_equipes(matchs: pd.DataFrame) -> tuple[list[str], np.ndarray, np.ndarray]:
    equipes = sorted(set(matchs["dom"].astype(str)) | set(matchs["ext"].astype(str)))
    rang = {equipe: i for i, equipe in enumerate(equipes)}
    i_dom = matchs["dom"].astype(str).map(rang).to_numpy(dtype=int)
    i_ext = matchs["ext"].astype(str).map(rang).to_numpy(dtype=int)
    return equipes, i_dom, i_ext


def _decouper(parametres: np.ndarray, n: int, separee: bool) -> tuple[np.ndarray, ...]:
    if separee:
        att_d, att_e = parametres[:n], parametres[n:2 * n]
        def_d, def_e = parametres[2 * n:3 * n], parametres[3 * n:4 * n]
        return att_d, att_e, def_d, def_e, parametres[4 * n], parametres[4 * n + 1]
    att, dfs = parametres[:n], parametres[n:2 * n]
    return att, att, dfs, dfs, parametres[2 * n], parametres[2 * n + 1]


def entrainer(
    matchs: pd.DataFrame,
    cible: Cible = "buts",
    poids_xg: float = POIDS_XG_DEFAUT,
    xi: float = XI_DEFAUT,
    force_domicile_separee: bool = False,
    date_reference: pd.Timestamp | None = None,
    regularisation: float | None = None,
    groupes: dict[str, str] | None = None,
) -> ModeleDixonColes:
    """Estime les parametres par maximum de vraisemblance ponderee (L-BFGS-B).

    `regularisation` : penalite L2 sur les forces d'equipe (voir REGULARISATION_DEFAUT).

    `groupes` (equipe -> championnat) : chaque groupe recoit un niveau
    d'attaque et de defense propre, NON penalise, et la penalite ne porte plus
    que sur l'ecart de chaque equipe au niveau de son groupe. Sans cela, sur
    plusieurs championnats a la fois, la penalite ramene tous les clubs vers
    une moyenne commune et efface la difference de niveau entre championnats
    (AEK presque a egalite avec le Real Madrid). Une equipe absente du
    dictionnaire forme le groupe « hors championnat ».
    """
    if regularisation is None:
        regularisation = defauts_cible(cible)["regularisation"]
    matchs = matchs.dropna(subset=["dom", "ext", "buts_dom", "buts_ext"]).reset_index(drop=True)
    if len(matchs) < 20:
        raise ValueError(
            f"Seulement {len(matchs)} matchs exploitables : c'est trop peu pour estimer "
            "un modele. Charge davantage de saisons (au moins une saison complete)."
        )

    equipes, i_dom, i_ext = _indices_equipes(matchs)
    n = len(equipes)
    y_dom, y_ext = cible_entrainement(matchs, cible, poids_xg)
    buts_dom = matchs["buts_dom"].to_numpy(dtype=float)
    buts_ext = matchs["buts_ext"].to_numpy(dtype=float)
    poids = poids_temporels(matchs["date"], xi, date_reference)

    taille_base = (4 * n if force_domicile_separee else 2 * n) + 2
    if groupes:
        noms_groupes = sorted({groupes.get(e, "@hors") for e in equipes})
        rang_groupe = {g: i for i, g in enumerate(noms_groupes)}
        i_groupe = np.array([rang_groupe[groupes.get(e, "@hors")] for e in equipes])
        nb_groupes = len(noms_groupes)
    else:
        i_groupe, nb_groupes = np.zeros(n, dtype=int), 0

    def forces(parametres: np.ndarray) -> tuple:
        att_d, att_e, def_d, def_e, gamma, rho = _decouper(
            parametres[:taille_base], n, force_domicile_separee)
        if nb_groupes:
            niveaux = parametres[taille_base:]
            off_att = niveaux[:nb_groupes][i_groupe]
            off_def = niveaux[nb_groupes:][i_groupe]
            att_d, att_e = att_d + off_att, att_e + off_att
            def_d, def_e = def_d + off_def, def_e + off_def
        return att_d, att_e, def_d, def_e, gamma, rho

    # Masques des quatre petits scores, constants pendant l'optimisation.
    s00 = (buts_dom == 0) & (buts_ext == 0)
    s01 = (buts_dom == 0) & (buts_ext == 1)
    s10 = (buts_dom == 1) & (buts_ext == 0)
    s11 = (buts_dom == 1) & (buts_ext == 1)

    def objectif(parametres: np.ndarray) -> tuple[float, np.ndarray]:
        """Vraisemblance negative penalisee ET son gradient exact.

        Sans gradient, L-BFGS-B l'approche par differences finies : 2n+2
        evaluations de l'objectif par iteration. Le gradient analytique rend
        l'entrainement des dizaines de fois plus rapide, ce qui permet un
        walk-forward reentraine a chaque journee.
        """
        att_d, att_e, def_d, def_e, gamma, rho = forces(parametres)
        lam_brut = np.exp(att_d[i_dom] + def_e[i_ext] + gamma)
        mu_brut = np.exp(att_e[i_ext] + def_d[i_dom])
        lam = np.clip(lam_brut, 1e-8, 25.0)
        mu = np.clip(mu_brut, 1e-8, 25.0)
        log_v = _log_poisson_continu(y_dom, lam) + _log_poisson_continu(y_ext, mu)
        log_v = log_v + _log_tau(buts_dom, buts_ext, lam, mu, rho)
        # Les niveaux de groupe, places apres gamma et rho, ne sont pas penalises.
        penalite = regularisation * float(np.sum(parametres[:taille_base - 2] ** 2))
        valeur = -float(np.sum(poids * log_v)) + penalite

        # -- gradient de la log-vraisemblance par rapport a log(lam), log(mu), rho
        tau = np.ones_like(lam)
        tau = np.where(s00, 1 - lam * mu * rho, tau)
        tau = np.where(s01, 1 + lam * rho, tau)
        tau = np.where(s10, 1 + mu * rho, tau)
        tau = np.where(s11, 1 - rho, tau)
        actif = tau > 1e-10                       # hors de la zone ecretee
        inv_tau = np.where(actif, 1.0 / np.where(actif, tau, 1.0), 0.0)
        dtau_lam = np.where(s00, -mu * rho, 0.0) + np.where(s01, rho, 0.0)
        dtau_mu = np.where(s00, -lam * rho, 0.0) + np.where(s10, rho, 0.0)
        dtau_rho = (np.where(s00, -lam * mu, 0.0) + np.where(s01, lam, 0.0)
                    + np.where(s10, mu, 0.0) - s11.astype(float))
        g_lam = poids * (y_dom - lam + lam * dtau_lam * inv_tau)
        g_mu = poids * (y_ext - mu + mu * dtau_mu * inv_tau)
        g_lam = np.where((lam_brut > 1e-8) & (lam_brut < 25.0), g_lam, 0.0)
        g_mu = np.where((mu_brut > 1e-8) & (mu_brut < 25.0), g_mu, 0.0)

        g_att_d = np.bincount(i_dom, g_lam, n)
        g_def_e = np.bincount(i_ext, g_lam, n)
        g_att_e = np.bincount(i_ext, g_mu, n)
        g_def_d = np.bincount(i_dom, g_mu, n)
        gradient = np.zeros_like(parametres)
        if force_domicile_separee:
            gradient[:n], gradient[n:2 * n] = g_att_d, g_att_e
            gradient[2 * n:3 * n], gradient[3 * n:4 * n] = g_def_d, g_def_e
        else:
            gradient[:n], gradient[n:2 * n] = g_att_d + g_att_e, g_def_d + g_def_e
        gradient[taille_base - 2] = g_lam.sum()
        gradient[taille_base - 1] = float(np.sum(poids * dtau_rho * inv_tau))
        if nb_groupes:
            gradient[taille_base:taille_base + nb_groupes] = np.bincount(
                i_groupe, g_att_d + g_att_e, nb_groupes)
            gradient[taille_base + nb_groupes:] = np.bincount(
                i_groupe, g_def_d + g_def_e, nb_groupes)
        gradient = -gradient
        gradient[:taille_base - 2] += 2.0 * regularisation * parametres[:taille_base - 2]
        return valeur, gradient

    depart = np.zeros(taille_base + 2 * nb_groupes)
    depart[taille_base - 2] = 0.25   # avantage du terrain initial
    depart[taille_base - 1] = -0.05  # rho initial
    bornes = ([(-3.0, 3.0)] * (taille_base - 2) + [(-1.0, 1.0), (-BORNE_RHO, BORNE_RHO)]
              + [(-3.0, 3.0)] * (2 * nb_groupes))

    solution = minimize(objectif, depart, method="L-BFGS-B", jac=True, bounds=bornes,
                        options={"maxiter": 800 if not nb_groupes else 3000, "ftol": 1e-10})

    att_d, att_e, def_d, def_e, gamma, rho = forces(solution.x)
    return _finaliser(
        equipes, att_d, att_e, def_d, def_e, float(gamma), float(rho),
        xi, cible, poids_xg, force_domicile_separee, len(matchs),
        -float(solution.fun), bool(solution.success),
    )


def _finaliser(
    equipes: list[str],
    att_d: np.ndarray, att_e: np.ndarray, def_d: np.ndarray, def_e: np.ndarray,
    gamma: float, rho: float, xi: float, cible: Cible, poids_xg: float,
    separee: bool, nb_matchs: int, log_v: float, convergence: bool,
) -> ModeleDixonColes:
    """Centre les attaques (le modele est invariant par translation) et emballe."""
    decalage = float(np.mean(att_d))
    att_d = att_d - decalage
    att_e = att_e - decalage
    def_d = def_d + decalage
    def_e = def_e + decalage
    return ModeleDixonColes(
        equipes=equipes,
        attaque=dict(zip(equipes, att_d.tolist())),
        defense=dict(zip(equipes, def_d.tolist())),
        attaque_ext=dict(zip(equipes, att_e.tolist())),
        defense_ext=dict(zip(equipes, def_e.tolist())),
        gamma=gamma, rho=rho, xi=xi, cible=cible, poids_xg=poids_xg,
        force_domicile_separee=separee, nb_matchs=nb_matchs,
        log_vraisemblance=log_v, convergence=convergence,
    )


def simuler_championnat(
    equipes: Iterable[str],
    attaques: dict[str, float],
    defenses: dict[str, float],
    gamma: float,
    nb_tours: int = 4,
    graine: int = 0,
    date_debut: str = "2020-08-01",
) -> pd.DataFrame:
    """Genere un championnat fictif a partir de forces connues (sert aux tests)."""
    generateur = np.random.default_rng(graine)
    equipes = list(equipes)
    lignes = []
    jour = pd.Timestamp(date_debut)
    for tour in range(nb_tours):
        for dom in equipes:
            for ext in equipes:
                if dom == ext:
                    continue
                lam = math.exp(attaques[dom] + defenses[ext] + gamma)
                mu = math.exp(attaques[ext] + defenses[dom])
                lignes.append({
                    "date": jour + pd.Timedelta(days=3 * tour),
                    "dom": dom, "ext": ext,
                    "buts_dom": int(generateur.poisson(lam)),
                    "buts_ext": int(generateur.poisson(mu)),
                })
    return pd.DataFrame(lignes)
