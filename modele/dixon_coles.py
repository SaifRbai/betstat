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

Cible = Literal["buts", "xg", "melange"]

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

def cible_entrainement(matchs: pd.DataFrame, cible: Cible, poids_xg: float) -> tuple[np.ndarray, np.ndarray]:
    """Valeurs a expliquer par le modele, selon le mode choisi."""
    buts_dom = matchs["buts_dom"].to_numpy(dtype=float)
    buts_ext = matchs["buts_ext"].to_numpy(dtype=float)
    if cible == "buts":
        return buts_dom, buts_ext

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
) -> ModeleDixonColes:
    """Estime les parametres par maximum de vraisemblance ponderee (L-BFGS-B).

    `regularisation` : penalite L2 sur les forces d'equipe (voir REGULARISATION_DEFAUT).
    """
    if regularisation is None:
        regularisation = REGULARISATION_DEFAUT
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

    def objectif(parametres: np.ndarray) -> float:
        att_d, att_e, def_d, def_e, gamma, rho = _decouper(parametres, n, force_domicile_separee)
        lam = np.exp(att_d[i_dom] + def_e[i_ext] + gamma)
        mu = np.exp(att_e[i_ext] + def_d[i_dom])
        lam = np.clip(lam, 1e-8, 25.0)
        mu = np.clip(mu, 1e-8, 25.0)
        log_v = _log_poisson_continu(y_dom, lam) + _log_poisson_continu(y_ext, mu)
        log_v = log_v + _log_tau(buts_dom, buts_ext, lam, mu, rho)
        penalite = regularisation * float(np.sum(parametres[:-2] ** 2))
        return -float(np.sum(poids * log_v)) + penalite

    taille = (4 * n if force_domicile_separee else 2 * n) + 2
    depart = np.zeros(taille)
    depart[-2] = 0.25   # avantage du terrain initial
    depart[-1] = -0.05  # rho initial
    bornes = [(-3.0, 3.0)] * (taille - 2) + [(-1.0, 1.0), (-BORNE_RHO, BORNE_RHO)]

    solution = minimize(objectif, depart, method="L-BFGS-B", bounds=bornes,
                        options={"maxiter": 800, "ftol": 1e-10})

    att_d, att_e, def_d, def_e, gamma, rho = _decouper(solution.x, n, force_domicile_separee)
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
