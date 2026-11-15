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


def test_regularisation_retrecit_les_forces() -> None:
    """La penalite L2 rapproche les forces de la moyenne sans casser l'ordre."""
    matchs, *_ = _championnat_simule(nb_tours=2)
    libre = dc.entrainer(matchs, xi=0.0, regularisation=0.0)
    retreci = dc.entrainer(matchs, xi=0.0, regularisation=10.0)
    ecart_libre = np.std([libre.attaque[e] for e in libre.equipes])
    ecart_retreci = np.std([retreci.attaque[e] for e in retreci.equipes])
    assert ecart_retreci < ecart_libre
    corr = np.corrcoef([libre.attaque[e] for e in libre.equipes],
                       [retreci.attaque[e] for e in retreci.equipes])[0, 1]
    assert corr > 0.95
    assert abs(sum(retreci.predire("Equipe 00", "Equipe 01")["1N2"].values()) - 1.0) < 1e-9


def test_regularisation_par_defaut() -> None:
    """Sans argument, entrainer() applique REGULARISATION_DEFAUT."""
    matchs, *_ = _championnat_simule(nb_tours=2)
    defaut = dc.entrainer(matchs, xi=0.0)
    explicite = dc.entrainer(matchs, xi=0.0, regularisation=dc.REGULARISATION_DEFAUT)
    assert defaut.attaque == pytest.approx(explicite.attaque)


def test_niveaux_de_groupe_preservent_l_ecart_entre_championnats() -> None:
    """Deux championnats d'equipes egales entre elles, l'un nettement plus fort,
    relies par quelques matchs : avec un niveau par groupe, la penalite L2 ne
    doit plus ramener les deux championnats a la meme moyenne."""
    import pandas as pd
    from modele import dixon_coles as dc
    fortes = {f"F{i}": 0.5 for i in range(6)}
    faibles = {f"W{i}": -0.5 for i in range(6)}
    attaques = {**fortes, **faibles}
    defenses = {e: -a for e, a in attaques.items()}
    m1 = dc.simuler_championnat(list(fortes), attaques, defenses, 0.25, nb_tours=3, graine=1)
    m2 = dc.simuler_championnat(list(faibles), attaques, defenses, 0.25, nb_tours=3, graine=2)
    ponts = dc.simuler_championnat(["F0", "W0", "F1", "W1"], attaques, defenses, 0.25,
                                   nb_tours=2, graine=3)
    ponts = ponts[ponts["dom"].str[0] != ponts["ext"].str[0]]
    matchs = pd.concat([m1, m2, ponts], ignore_index=True)
    groupes = {e: e[0] for e in attaques}

    def ecart(modele):
        note = {e: modele.attaque[e] - modele.defense[e] for e in attaques}
        return (sum(note[e] for e in fortes) - sum(note[e] for e in faibles)) / 6

    sans = ecart(dc.entrainer(matchs, xi=0.0))
    avec = ecart(dc.entrainer(matchs, xi=0.0, groupes=groupes))
    assert avec > sans + 0.3        # vrai ecart : 2.0 ; la penalite l'ecrasait


def _avec_tirs(matchs: pd.DataFrame, graine: int = 3) -> pd.DataFrame:
    """Tirs simules coherents avec les buts : environ 3 tirs cadres par but."""
    generateur = np.random.default_rng(graine)
    matchs = matchs.copy()
    for cote in ("dom", "ext"):
        cadres = matchs[f"buts_{cote}"] + generateur.poisson(2.5, len(matchs))
        matchs[f"tirs_cadres_{cote}"] = cadres
        matchs[f"tirs_{cote}"] = cadres + generateur.poisson(6.0, len(matchs))
    return matchs


def test_coefficients_tirs_plausibles() -> None:
    matchs = _avec_tirs(_championnat_simule()[0])
    a, b = dc.coefficients_tirs(matchs)
    assert 0.1 < a < 0.6          # un tir cadre vaut bien plus qu'un tir non cadre
    assert 0.0 <= b < a


def test_cible_tirs_sans_colonnes_retombe_sur_les_buts() -> None:
    matchs, *_ = _championnat_simule()
    par_buts = dc.entrainer(matchs, cible="buts", xi=0.0)
    par_tirs = dc.entrainer(matchs, cible="tirs", poids_xg=0.7, xi=0.0)
    assert par_tirs.attaque == pytest.approx(par_buts.attaque)


def test_cible_tirs_poids_nul_egal_buts_et_poids_plein_different() -> None:
    matchs = _avec_tirs(_championnat_simule()[0])
    par_buts = dc.entrainer(matchs, cible="buts", xi=0.0)
    nul = dc.entrainer(matchs, cible="tirs", poids_xg=0.0, xi=0.0)
    plein = dc.entrainer(matchs, cible="tirs", poids_xg=1.0, xi=0.0)
    assert nul.attaque == pytest.approx(par_buts.attaque, abs=1e-5)
    assert plein.attaque != pytest.approx(par_buts.attaque, abs=1e-3)
    assert plein.convergence



# -- cible "marche" : buts implicites des cotes ---------------------------------

def _cotes_poisson(lam: float, mu: float, marge: float = 0.05) -> tuple[float, ...]:
    """Cotes 1N2 et plus/moins 2.5 d'un marche Poisson exact, avec surcote."""
    from scipy.stats import poisson

    k = np.arange(12)
    m = np.outer(poisson.pmf(k, lam), poisson.pmf(k, mu))
    p1, pn, p2 = np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()
    po = m[np.add.outer(k, k) > 2.5].sum()
    return tuple(1.0 / (p * (1 + marge)) for p in (p1, pn, p2, po, 1 - po))


def test_buts_implicites_retrouve_les_intensites() -> None:
    from modele.marche_implicite import buts_implicites

    for lam, mu in ((1.55, 1.20), (2.40, 0.85), (0.90, 1.70)):
        c = _cotes_poisson(lam, mu)
        l, u = buts_implicites(*([x] for x in c))
        assert l[0] == pytest.approx(lam, abs=0.02)
        assert u[0] == pytest.approx(mu, abs=0.02)


def test_buts_implicites_nan_si_cote_absente_ou_invalide() -> None:
    from modele.marche_implicite import buts_implicites

    c = _cotes_poisson(1.5, 1.2)
    l, u = buts_implicites([c[0], np.nan, 1.0], [c[1]] * 3, [c[2]] * 3,
                           [c[3]] * 3, [c[4]] * 3)
    assert np.isfinite(l[0]) and np.isfinite(u[0])
    assert np.isnan(l[1:]).all() and np.isnan(u[1:]).all()


def test_cible_marche_sans_cotes_retombe_sur_les_buts() -> None:
    matchs, *_ = _championnat_simule()
    par_buts = dc.entrainer(matchs, cible="buts", xi=0.0, regularisation=0.3)
    par_marche = dc.entrainer(matchs, cible="marche", xi=0.0)
    assert par_marche.attaque == pytest.approx(par_buts.attaque)


def test_cible_marche_apprend_des_cotes() -> None:
    """Des cotes tirees des vraies forces doivent mieux les retrouver que les scores."""
    matchs, attaques, defenses, gamma = _championnat_simule(nb_tours=2)
    cotes = [_cotes_poisson(math.exp(attaques[r.dom] + defenses[r.ext] + gamma),
                            math.exp(attaques[r.ext] + defenses[r.dom]))
             for r in matchs.itertuples()]
    for i, col in enumerate(("cote_1", "cote_N", "cote_2", "cote_plus25", "cote_moins25")):
        matchs[col] = [c[i] for c in cotes]
    def erreur(modele) -> float:
        vrai = np.array([attaques[e] for e in modele.equipes])
        return float(np.mean((np.array([modele.attaque[e] for e in modele.equipes])
                              - (vrai - vrai.mean())) ** 2))
    par_buts = dc.entrainer(matchs, cible="buts", xi=0.0)
    par_marche = dc.entrainer(matchs, cible="marche", poids_xg=1.0, xi=0.0)
    assert par_marche.convergence
    assert erreur(par_marche) < 0.5 * erreur(par_buts)


def test_defauts_par_cible() -> None:
    assert dc.defauts_cible("marche")["regularisation"] == dc.REGULARISATION_MARCHE_DEFAUT
    assert dc.defauts_cible("buts")["regularisation"] == dc.REGULARISATION_DEFAUT
    assert dc.CIBLE_DEFAUT == "marche"
