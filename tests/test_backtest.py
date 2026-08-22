"""Tests du backtest (T10) : pas de fuite du futur, ROI avec intervalle, baselines."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modele import backtest, calibration
from modele import dixon_coles as dc


@pytest.fixture(scope="module")
def matchs_simules() -> pd.DataFrame:
    """Championnat fictif avec cotes historiques coherentes avec les vraies forces."""
    generateur = np.random.default_rng(11)
    equipes = [f"Equipe {i:02d}" for i in range(14)]
    attaques = {e: float(v) for e, v in zip(equipes, generateur.normal(0, 0.28, len(equipes)))}
    defenses = {e: float(v) for e, v in zip(equipes, generateur.normal(0.1, 0.22, len(equipes)))}
    matchs = dc.simuler_championnat(equipes, attaques, defenses, 0.25,
                                    nb_tours=6, graine=11)
    modele_vrai = dc.ModeleDixonColes(
        equipes=equipes, attaque=attaques, defense=defenses,
        attaque_ext=attaques, defense_ext=defenses, gamma=0.25, rho=0.0,
        xi=0.0, cible="buts", poids_xg=0.7, force_domicile_separee=False,
        nb_matchs=len(matchs), log_vraisemblance=0.0, convergence=True)
    lignes = []
    for ligne in matchs.itertuples():
        probas = modele_vrai.predire(ligne.dom, ligne.ext)["1N2"]
        # Cotes avec 6 % de marge, comme un vrai bookmaker.
        lignes.append({s: round(1 / (probas[s] * 1.06), 2) for s in ("1", "N", "2")})
    cotes = pd.DataFrame(lignes)
    matchs = matchs.copy()
    for selection in ("1", "N", "2"):
        matchs[f"cote_{selection}"] = cotes[selection].to_numpy()
        matchs[f"cote_{selection}_max"] = (cotes[selection] * 1.03).round(2).to_numpy()
    return matchs


def test_walk_forward_ne_predit_que_du_hors_echantillon(matchs_simules: pd.DataFrame) -> None:
    predictions = calibration.predictions_walk_forward(
        matchs_simules, xi=0.0, frequence_reentrainement=5, taille_initiale=200)
    assert not predictions.empty
    # Aucune journee n'est predite sans qu'un historique la precede strictement,
    # et la premiere journee du jeu de donnees n'est jamais predite.
    premiere_journee = matchs_simules["date"].min()
    assert predictions["date"].min() > premiere_journee
    for jour in predictions["date"].unique():
        assert (matchs_simules["date"] < jour).sum() >= 60


def test_probabilites_marche_devigguees_somment_a_un(matchs_simules: pd.DataFrame) -> None:
    predictions = calibration.predictions_walk_forward(
        matchs_simules, xi=0.0, frequence_reentrainement=8, taille_initiale=200)
    p_marche = backtest._probabilites_marche(predictions)
    assert np.allclose(np.nansum(p_marche, axis=1), 1.0)


def test_intervalle_bootstrap_contient_le_roi() -> None:
    generateur = np.random.default_rng(0)
    mises = np.full(300, 10.0)
    gains = np.where(generateur.uniform(size=300) < 0.5, 10.0, -10.0)
    roi, bas, haut = backtest.intervalle_bootstrap_roi(gains, mises)
    assert bas <= roi <= haut
    assert bas < 0 < haut  # 50/50 a cote 2 : indistinguable du hasard


def test_intervalle_bootstrap_sans_paris() -> None:
    roi, bas, haut = backtest.intervalle_bootstrap_roi(np.array([]), np.array([]))
    assert np.isnan(roi) and np.isnan(bas) and np.isnan(haut)


def test_drawdown_maximum() -> None:
    assert backtest.drawdown_maximum([110, 90, 120], 100) == pytest.approx(
        (110 - 90) / 110)
    assert backtest.drawdown_maximum([120, 130], 100) == pytest.approx(0.0)


def test_courbe_de_calibration() -> None:
    probas = np.array([0.05, 0.15, 0.15, 0.55, 0.95])
    realise = np.array([0.0, 0.0, 1.0, 1.0, 1.0])
    courbe = backtest.courbe_calibration(probas, realise, nb_buckets=10)
    assert courbe["effectif"].sum() == 5
    assert set(courbe.columns) == {"bucket", "annonce_moyen", "realise", "effectif"}


def test_rapport_complet(matchs_simules: pd.DataFrame) -> None:
    resultat = backtest.backtester(
        matchs_simules, championnat="TEST", xi=0.0,
        frequence_reentrainement=10, graphiques=False)
    rapport = resultat.rapport
    assert "IC 95 %" in rapport or "Aucun pari" in rapport
    assert "BASELINE" in rapport
    assert "favori du marche" in rapport
    assert resultat.baselines["marche_devigge"]["n"] > 0


def test_mention_explicite_quand_l_intervalle_contient_zero() -> None:
    paris = {"n": 400, "mise_totale": 4000.0, "pnl": 5.0, "roi": 0.001,
             "ic_bas": -0.05, "ic_haut": 0.05, "ic_contient_zero": True,
             "taux_reussite": 0.5, "cote_moyenne": 2.0, "bankroll_finale": 1005.0,
             "drawdown_max": 0.1}
    texte = "\n".join(backtest._bloc_paris(paris))
    assert "CONTIENT ZERO" in texte
    assert "n'est PAS distinguable" in texte.replace("\n", " ").replace("       ", " ")


def test_avertissement_petit_echantillon() -> None:
    paris = {"n": 5, "mise_totale": 50.0, "pnl": -50.0, "roi": -1.0,
             "ic_bas": -1.0, "ic_haut": -1.0, "ic_contient_zero": False,
             "taux_reussite": 0.0, "cote_moyenne": 4.0, "bankroll_finale": 950.0,
             "drawdown_max": 0.05}
    texte = "\n".join(backtest._bloc_paris(paris))
    assert "Seulement 5 pari" in texte


def test_baseline_favori_marche(matchs_simules: pd.DataFrame) -> None:
    predictions = calibration.predictions_walk_forward(
        matchs_simules, xi=0.0, frequence_reentrainement=10, taille_initiale=200)
    baseline = backtest.baseline_favori_marche(predictions)
    assert baseline["n"] == len(predictions)
    assert baseline["ic_bas"] <= baseline["roi"] <= baseline["ic_haut"]


def test_backtest_refuse_un_historique_trop_court() -> None:
    generateur = np.random.default_rng(2)
    equipes = [f"E{i}" for i in range(6)]
    petit = dc.simuler_championnat(
        equipes, {e: 0.0 for e in equipes}, {e: 0.0 for e in equipes}, 0.2,
        nb_tours=1, graine=2)
    with pytest.raises(ValueError, match="Backtest impossible"):
        backtest.backtester(petit, graphiques=False)
