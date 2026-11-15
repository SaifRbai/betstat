"""Buts attendus implicites du marche, tires des cotes d'un match deja joue.

Les cotes 1N2 et plus/moins 2.5 d'un match resument tout ce que le marche
savait avant le coup d'envoi (compositions, forme, blessures). On les inverse
en deux intensites de Poisson (lambda_dom, lambda_ext) : le couple qui
reproduit au mieux P(1), P(2) et P(plus de 2.5) de-vigges.

Ces buts implicites servent de cible d'entrainement au modele (cible
"marche"), exactement comme les xG : une mesure moins bruitee que le score.
Aucune fuite du futur : seules les cotes des matchs DEJA joues entrent dans
l'entrainement ; le match a predire n'apporte jamais ses propres cotes.

Mesure walk-forward sur 4 saisons, log-loss 1N2, modele sur buts -> cible
marche (w = 0.8, xi = 0.004, r = 0.3) : F1 0.9872->0.9790, SP1 0.9764->0.9679,
I1 0.9694->0.9610, D1 0.9987->0.9838, N1 0.9988->0.9869, E0 1.0095->1.0102.
Le marche reste devant (F1 0.9661, SP1 0.9537, I1 0.9462, D1 0.9711).

Methode : grille de couples (lambda, mu) de 0.15 a 4.00 par pas de 0.01,
probabilites calculees une fois, puis plus proche voisin (arbre k-d) dans
l'espace (P1, P2, Pplus2.5). La precision de 0.01 but est tres en dessous du
bruit de la cible.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Sequence

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import poisson

PAS = 0.01
MINIMUM = 0.15
MAXIMUM = 4.0
_TAILLE = 12


@lru_cache(maxsize=1)
def _grille() -> tuple[np.ndarray, np.ndarray, np.ndarray, cKDTree]:
    """Valeurs de lambda, indices des couples et arbre de recherche."""
    valeurs = np.round(np.arange(MINIMUM, MAXIMUM + PAS / 2, PAS), 2)
    buts = np.arange(_TAILLE)
    pmf = poisson.pmf(buts[None, :], valeurs[:, None])            # (g, 12)
    i_lam, i_mu = np.meshgrid(np.arange(len(valeurs)), np.arange(len(valeurs)),
                              indexing="ij")
    i_lam, i_mu = i_lam.ravel(), i_mu.ravel()
    ecart = buts[:, None] - buts[None, :]
    total = buts[:, None] + buts[None, :]
    # P1 = sum_ij pmf_lam[i] pmf_mu[j] [i>j], calcule sans materialiser (N,12,12).
    gagne = (ecart > 0).astype(float)
    perd = (ecart < 0).astype(float)
    plus = (total > 2.5).astype(float)
    a, b = pmf[i_lam], pmf[i_mu]
    p1 = np.einsum("ni,ij,nj->n", a, gagne, b)
    p2 = np.einsum("ni,ij,nj->n", a, perd, b)
    po = np.einsum("ni,ij,nj->n", a, plus, b)
    return valeurs, i_lam, i_mu, cKDTree(np.c_[p1, p2, po])


def _normaliser(*cotes: np.ndarray) -> np.ndarray:
    inverses = np.column_stack([1.0 / c for c in cotes])
    return inverses / inverses.sum(axis=1, keepdims=True)


def buts_implicites(
    cote_1: Sequence[float], cote_n: Sequence[float], cote_2: Sequence[float],
    cote_plus25: Sequence[float], cote_moins25: Sequence[float],
) -> tuple[np.ndarray, np.ndarray]:
    """(lambda_dom, lambda_ext) implicites, NaN quand une cote manque ou est invalide.

    De-vig multiplicatif : sur ces marches a 2 ou 3 issues, l'ecart avec Shin
    est de l'ordre du centieme de but, sans effet mesurable sur le modele.
    """
    colonnes = [np.asarray(c, dtype=float)
                for c in (cote_1, cote_n, cote_2, cote_plus25, cote_moins25)]
    with np.errstate(divide="ignore", invalid="ignore"):
        valides = np.all([np.isfinite(c) & (c > 1.0) for c in colonnes], axis=0)
        trois = _normaliser(*colonnes[:3])
        deux = _normaliser(*colonnes[3:])
    lam = np.full(len(colonnes[0]), np.nan)
    mu = np.full(len(colonnes[0]), np.nan)
    if valides.any():
        valeurs, i_lam, i_mu, arbre = _grille()
        cibles = np.c_[trois[valides, 0], trois[valides, 2], deux[valides, 0]]
        _, plus_proche = arbre.query(cibles)
        lam[valides] = valeurs[i_lam[plus_proche]]
        mu[valides] = valeurs[i_mu[plus_proche]]
    return lam, mu
