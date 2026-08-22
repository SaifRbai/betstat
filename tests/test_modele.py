"""Tests du modele Dixon-Coles (T05), dont la recuperation de parametres."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from modele import dixon_coles as dc


def _championnat_simule(graine: int = 7, nb_tours: int = 6) -> tuple[pd.DataFrame, dict, dict, float]:
    generateur = np.random.default_rng(graine)
    equipes = [f"Equipe {i:02d}" for i in range(18)]
    attaques = {e: float(v) for e, v in zip(equipes, generateur.normal(0.0, 0.30, len(equipes)))}
    attaques = {e: v - float(np.mean(list(attaques.values()))) for e, v in attaques.items()}
    defenses = {e: float(v) for e, v in zip(equipes, generateur.normal(0.10, 0.25, len(equipes)))}
    gamma = 0.26
    matchs = dc.simuler_championnat(equipes, attaques, defenses, gamma,
                                    nb_tours=nb_tours, graine=graine)
    return matchs, attaques, defenses, gamma


def test_recuperation_des_parametres() -> None:
    """Sur donnees simulees, le modele doit retrouver les forces qui les ont generees."""
    matchs, attaques, defenses, gamma = _championnat_simule()
    modele = dc.entrainer(matchs, cible="buts", xi=0.0)

    estimees = np.array([modele.attaque[e] for e in modele.equipes])
    reelles = np.array([attaques[e] for e in modele.equipes])
    correlation = float(np.corrcoef(estimees, reelles)[0, 1])
    assert correlation > 0.85, f"correlation attaque trop faible : {correlation:.3f}"

    est_def = np.array([modele.defense[e] for e in modele.equipes])
    reel_def = np.array([defenses[e] for e in modele.equipes])
    assert float(np.corrcoef(est_def, reel_def)[0, 1]) > 0.85

    assert abs(modele.gamma - gamma) < 0.05, f"gamma estime {modele.gamma:.3f} vs reel {gamma}"
    assert modele.convergence


def test_probabilites_1n2_somment_a_un() -> None:
    matchs, *_ = _championnat_simule()
    modele = dc.entrainer(matchs, xi=0.0)
    prediction = modele.predire("Equipe 00", "Equipe 01")
    assert abs(sum(prediction["1N2"].values()) - 1.0) < 1e-9


def test_coherence_des_marches_derives() -> None:
    matchs, *_ = _championnat_simule()
    modele = dc.entrainer(matchs, xi=0.0)
    p = modele.predire("Equipe 03", "Equipe 04")
    for seuil in (0.5, 1.5, 2.5, 3.5, 4.5):
        somme = p["totaux"][f"plus_{seuil}"] + p["totaux"][f"moins_{seuil}"]
        assert abs(somme - 1.0) < 1e-9
    assert abs(p["btts"]["oui"] + p["btts"]["non"] - 1.0) < 1e-9
    assert abs(p["double_chance"]["1N"] - (p["1N2"]["1"] + p["1N2"]["N"])) < 1e-12
    for handicap in p["handicap_asiatique"].values():
        total = handicap["dom"] + handicap["ext"] + handicap["remboursement"]
        assert abs(total - 1.0) < 1e-9


def test_matrice_normalisee() -> None:
    matchs, *_ = _championnat_simule()
    modele = dc.entrainer(matchs, xi=0.0)
    matrice = modele.matrice_scores("Equipe 00", "Equipe 05")
    assert abs(matrice.sum() - 1.0) < 1e-12
    assert (matrice > 0).all()


def test_mode_xg_vraisemblance_continue() -> None:
    """Des cibles non entieres ne doivent pas faire echouer l'entrainement."""
    matchs, *_ = _championnat_simule()
    generateur = np.random.default_rng(1)
    matchs = matchs.copy()
    matchs["xg_dom"] = matchs["buts_dom"] + generateur.normal(0, 0.3, len(matchs))
    matchs["xg_ext"] = matchs["buts_ext"] + generateur.normal(0, 0.3, len(matchs))
    matchs[["xg_dom", "xg_ext"]] = matchs[["xg_dom", "xg_ext"]].clip(lower=0.0)

    modele_xg = dc.entrainer(matchs, cible="xg", xi=0.0)
    modele_mix = dc.entrainer(matchs, cible="melange", poids_xg=0.7, xi=0.0)
    assert modele_xg.convergence and modele_mix.convergence
    assert abs(sum(modele_xg.predire("Equipe 00", "Equipe 01")["1N2"].values()) - 1.0) < 1e-9


def test_mode_xg_sans_colonnes_message_clair() -> None:
    matchs, *_ = _championnat_simule()
    with pytest.raises(ValueError, match="xg_dom"):
        dc.entrainer(matchs, cible="xg")


def test_option_force_domicile_separee_desactivee_par_defaut() -> None:
    matchs, *_ = _championnat_simule()
    simple = dc.entrainer(matchs, xi=0.0)
    assert simple.force_domicile_separee is False
    separe = dc.entrainer(matchs, xi=0.0, force_domicile_separee=True)
    assert separe.force_domicile_separee is True
    # Le modele a 4 parametres par equipe doit coller au moins aussi bien.
    assert separe.log_vraisemblance >= simple.log_vraisemblance - 1e-6


def test_ponderation_temporelle() -> None:
    dates = pd.Series(pd.to_datetime(["2024-01-01", "2024-06-29", "2024-12-26"]))
    poids = dc.poids_temporels(dates, xi=0.002)
    assert poids[-1] == pytest.approx(1.0)
    assert poids[0] < poids[1] < poids[2]
    uniformes = dc.poids_temporels(dates, xi=0.0)
    assert np.allclose(uniformes, 1.0)


def test_ajustement_manuel_augmente_les_buts_attendus() -> None:
    matchs, *_ = _championnat_simule()
    modele = dc.entrainer(matchs, xi=0.0)
    avant, _ = modele.lambdas("Equipe 00", "Equipe 01")
    modele.ajuster("Equipe 00", attaque_pct=20.0)
    apres, _ = modele.lambdas("Equipe 00", "Equipe 01")
    assert apres == pytest.approx(avant * 1.20, rel=1e-9)
    modele.reinitialiser_ajustements()
    assert modele.lambdas("Equipe 00", "Equipe 01")[0] == pytest.approx(avant)


def test_equipe_inconnue_message_avec_suggestions() -> None:
    matchs, *_ = _championnat_simule()
    modele = dc.entrainer(matchs, xi=0.0)
    with pytest.raises(KeyError, match="le plus proche"):
        modele.predire("Equipe 99", "Equipe 01")


def test_trop_peu_de_matchs() -> None:
    matchs, *_ = _championnat_simule()
    with pytest.raises(ValueError, match="trop peu"):
        dc.entrainer(matchs.head(10))


def test_rho_borne() -> None:
    matchs, *_ = _championnat_simule()
    modele = dc.entrainer(matchs, xi=0.0)
    assert -dc.BORNE_RHO <= modele.rho <= dc.BORNE_RHO
