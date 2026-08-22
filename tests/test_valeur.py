"""Tests de la detection de valeur et du calcul de mise (T11)."""

from __future__ import annotations

import pandas as pd
import pytest

import config
from paris import valeur


def test_kelly_complet_formule() -> None:
    assert valeur.fraction_kelly_complete(0.55, 2.10) == pytest.approx(0.1409090909, abs=1e-9)
    assert valeur.fraction_kelly_complete(0.40, 2.10) < 0


def test_kelly_est_divise_par_quatre_puis_plafonne() -> None:
    """p=0.55 a 2.10 : Kelly complet 14.09 %, Kelly/4 3.52 %, plafonne a 2 %."""
    fraction, mise = valeur.mise_conseillee(0.55, 2.10, 1000.0)
    assert valeur.fraction_kelly_complete(0.55, 2.10) / 4 == pytest.approx(0.0352272, abs=1e-6)
    assert fraction == pytest.approx(config.PLAFOND_MISE_BANKROLL)
    assert mise == pytest.approx(20.0)


def test_plafond_non_contournable_par_parametre() -> None:
    """Meme en reclamant du Kelly complet, on ne depasse jamais 2 % de bankroll."""
    fraction, mise = valeur.mise_conseillee(0.90, 3.00, 1000.0, fraction=1.0)
    assert fraction <= config.PLAFOND_MISE_BANKROLL + 1e-12
    assert mise <= 20.0


def test_petit_edge_reste_sous_le_plafond() -> None:
    fraction, mise = valeur.mise_conseillee(0.35, 3.10, 1000.0)
    assert 0 < fraction < config.PLAFOND_MISE_BANKROLL
    assert mise == pytest.approx(round(fraction * 1000, 2))


def test_pas_de_mise_sans_valeur() -> None:
    assert valeur.mise_conseillee(0.30, 2.00, 1000.0) == (0.0, 0.0)
    assert valeur.mise_conseillee(0.60, 2.00, 0.0) == (0.0, 0.0)


def _opportunite(**modifs):
    base = dict(dom="A", ext="B", marche="1N2", selection="1", cote=2.10, bookmaker="bk",
                p_modele=0.55, p_marche=0.53, p_finale=0.55, bankroll=1000.0,
                nb_bookmakers=5, cote_mediane=2.00)
    base.update(modifs)
    return valeur.evaluer(**base)


def test_opportunite_valide() -> None:
    o = _opportunite()
    assert o.recommandee
    assert o.esperance == pytest.approx(0.155, abs=1e-9)
    assert o.mise == pytest.approx(20.0)
    assert o.raisons_rejet == []


def test_rejet_esperance_insuffisante() -> None:
    o = _opportunite(p_finale=0.46)
    assert not o.recommandee
    assert any("esperance" in motif for motif in o.raisons_rejet)


def test_rejet_cote_sous_la_mediane() -> None:
    o = _opportunite(cote_mediane=2.30)
    assert not o.recommandee
    assert any("mediane" in motif for motif in o.raisons_rejet)


def test_rejet_pas_assez_de_bookmakers() -> None:
    o = _opportunite(nb_bookmakers=2)
    assert not o.recommandee
    assert any("bookmaker" in motif for motif in o.raisons_rejet)


def test_divergence_declenche_une_alerte_pas_une_recommandation() -> None:
    o = _opportunite(p_marche=0.30)
    assert not o.recommandee
    assert o.alerte is not None and "divergence" in o.alerte
    assert o.mise == 0.0


def test_les_quatre_conditions_sont_cumulatives() -> None:
    o = _opportunite(p_finale=0.40, cote_mediane=2.50, nb_bookmakers=1, p_marche=0.10)
    assert len(o.raisons_rejet) == 4


def test_resume_lisible_en_cas_de_rejet() -> None:
    texte = _opportunite(nb_bookmakers=1).resume()
    assert "ecarte" in texte and "bookmaker" in texte


def test_en_dataframe_vide() -> None:
    tableau = valeur.en_dataframe([])
    assert tableau.empty and "esperance" in tableau.columns


def test_en_dataframe_colonnes() -> None:
    tableau = valeur.en_dataframe([_opportunite(), _opportunite(nb_bookmakers=1)])
    assert len(tableau) == 2
    assert tableau["recommandee"].tolist() == [True, False]
