"""Tests de modele/annexes.py : corners et cartons jaunes."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modele import annexes


def _matchs(n: int = 200, graine: int = 7, colonnes: tuple[str, ...] = (
        "corners_dom", "corners_ext", "jaunes_dom", "jaunes_ext")) -> pd.DataFrame:
    """Championnat synthetique ou une equipe produit deux fois la moyenne."""
    hasard = np.random.default_rng(graine)
    equipes = ["A", "B", "C", "D", "E", "F"]
    force = {"A": 2.0, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0, "F": 0.5}
    lignes = []
    for i in range(n):
        dom, ext = hasard.choice(equipes, size=2, replace=False)
        ligne = {"date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i // 3),
                 "dom": dom, "ext": ext}
        for base, (col_d, col_e) in ((5.0, ("corners_dom", "corners_ext")),
                                     (2.0, ("jaunes_dom", "jaunes_ext"))):
            ligne[col_d] = hasard.poisson(base * force[dom])
            ligne[col_e] = hasard.poisson(base * 0.8 * force[ext])
        lignes.append(ligne)
    return pd.DataFrame(lignes)[["date", "dom", "ext", *colonnes]]


def test_les_forces_estimees_retrouvent_celles_qui_ont_genere_les_donnees() -> None:
    modele = annexes.entrainer(_matchs(600), "corners")
    assert modele.convergence
    # A produit deux fois la moyenne, F deux fois moins : l'ordre doit sortir.
    assert modele.production["A"] > modele.production["B"] > modele.production["F"]
    assert modele.production["A"] / modele.production["F"] == pytest.approx(4.0, rel=0.35)


def test_la_normalisation_rend_les_forces_comparables() -> None:
    modele = annexes.entrainer(_matchs(400), "jaunes")
    assert np.mean(list(modele.production.values())) == pytest.approx(1.0)
    assert np.mean(list(modele.concession.values())) == pytest.approx(1.0)


def test_les_marches_derives_sont_coherents() -> None:
    modele = annexes.entrainer(_matchs(400), "corners")
    p = modele.predire("A", "B")
    assert p["attendu_total"] == pytest.approx(p["attendu_dom"] + p["attendu_ext"])
    # A produit beaucoup : il doit dominer le duel des corners.
    assert p["comparaison"]["1"] > p["comparaison"]["2"]
    assert sum(p["comparaison"].values()) == pytest.approx(1.0, abs=1e-6)
    for seuil in annexes.TYPES["corners"]["seuils"]:
        assert p["totaux"][f"plus_{seuil}"] + p["totaux"][f"moins_{seuil}"] == pytest.approx(1.0)
    # Une ligne plus haute est toujours moins probable a depasser.
    plus = [p["totaux"][f"plus_{s}"] for s in annexes.TYPES["corners"]["seuils"]]
    assert plus == sorted(plus, reverse=True)


def test_la_dispersion_vaut_environ_un_sur_des_donnees_poisson() -> None:
    """Le chiffre doit etre credible, sinon l'avertissement qu'il declenche ne vaut rien."""
    modele = annexes.entrainer(_matchs(600), "corners")
    assert 0.8 < modele.dispersion < 1.25


def test_la_dispersion_grimpe_sur_des_donnees_sur_dispersees() -> None:
    hasard = np.random.default_rng(3)
    matchs = _matchs(400)
    # Melange de deux regimes : meme moyenne, variance bien plus grande.
    bruit = hasard.choice([0, 1], size=len(matchs))
    matchs["corners_dom"] = np.where(bruit, hasard.poisson(1, len(matchs)),
                                     hasard.poisson(9, len(matchs)))
    assert annexes.entrainer(matchs, "corners").dispersion > 1.5


def test_colonnes_absentes_message_actionnable() -> None:
    matchs = _matchs(100).drop(columns=["corners_dom", "corners_ext"])
    with pytest.raises(annexes.DonneesInsuffisantes, match="corners_dom"):
        annexes.entrainer(matchs, "corners")
    # Les cartons restent disponibles : un type ne fait pas tomber l'autre.
    assert annexes.entrainer(matchs, "jaunes").nb_matchs == 100


def test_trop_peu_de_matchs() -> None:
    with pytest.raises(annexes.DonneesInsuffisantes, match="30 minimum"):
        annexes.entrainer(_matchs(20), "corners")


def test_type_inconnu() -> None:
    with pytest.raises(ValueError, match="Type de compte inconnu"):
        annexes.entrainer(_matchs(100), "penaltys")


def test_equipe_inconnue_est_signalee() -> None:
    modele = annexes.entrainer(_matchs(200), "corners")
    with pytest.raises(KeyError, match="Promu"):
        modele.predire("Promu", "A")


# --------------------------------------------------------------------------
# Validation hors echantillon
# --------------------------------------------------------------------------

def _validation(**remplacements) -> annexes.Validation:
    base = dict(type_compte="corners", championnat="SP1", nb_matchs=936,
                biais=0.011, biais_ic=0.21, erreur_absolue=2.64, dispersion=1.47,
                log_vraisemblance=-2.63, log_vraisemblance_moyenne_ligue=-2.65,
                calibration=[{"ligne": 9.5, "annonce": 0.474, "constate": 0.467,
                              "ecart": 0.007}],
                ecart_calibration_max=0.016, calcule_le="2026-08-21T12:00:00+00:00")
    return annexes.Validation(**{**base, **remplacements})


def test_un_biais_dans_son_intervalle_n_est_pas_annonce_comme_reel() -> None:
    v = _validation(biais=0.011, biais_ic=0.21)
    assert v.biais_significatif is False
    assert "non distinguable de zero" in v.verdict
    assert "sur-estime" not in v.verdict


def test_un_biais_significatif_est_nomme_et_oriente() -> None:
    """Le cas mesure sur la Serie A : -0.24 carton par match, hors intervalle."""
    v = _validation(type_compte="jaunes", biais=-0.24, biais_ic=0.12)
    assert v.biais_significatif is True
    assert "sur-estime de 0.24" in v.verdict


def test_un_modele_qui_ne_bat_pas_la_moyenne_de_ligue_le_dit() -> None:
    """Le cas mesure sur la Ligue 1 et l'Eredivisie pour les corners."""
    v = _validation(log_vraisemblance=-2.66, log_vraisemblance_moyenne_ligue=-2.63)
    assert v.bat_la_moyenne_de_ligue is False
    assert "n'ameliorent PAS" in v.verdict
    assert "decoratif" in v.verdict


def test_aucune_equipe_ne_recoit_une_probabilite_nulle() -> None:
    """Sans a priori, une equipe jamais cartonnee avait lambda=0 : proba zero."""
    matchs = _matchs(300)
    # Une equipe qui n'a jamais pris le moindre carton sur la fenetre.
    vierge = (matchs["dom"] == "A")
    matchs.loc[vierge, "jaunes_dom"] = 0
    matchs.loc[matchs["ext"] == "A", "jaunes_ext"] = 0
    modele = annexes.entrainer(matchs, "jaunes")
    assert modele.production["A"] > 0
    lam_dom, _ = modele.lambdas("A", "B")
    assert lam_dom > 0
    # Et donc une probabilite non nulle d'au moins un carton.
    assert modele.predire("A", "B")["totaux"]["plus_1.5"] > 0


def test_validation_walk_forward_ne_voit_jamais_le_futur() -> None:
    matchs = _matchs(500)
    v = annexes.valider(matchs, "corners", "TEST", taille_initiale=150)
    assert v.nb_matchs > 100
    assert v.type_compte == "corners" and v.championnat == "TEST"
    assert len(v.calibration) == len(annexes.TYPES["corners"]["seuils"])
    for ligne in v.calibration:
        assert 0.0 <= ligne["annonce"] <= 1.0
        assert ligne["ecart"] == pytest.approx(ligne["annonce"] - ligne["constate"])
    # Donnees generees par un Poisson : la dispersion doit rester proche de 1.
    assert 0.7 < v.dispersion < 1.4


def test_valider_refuse_de_conclure_sur_trop_peu_de_matchs() -> None:
    with pytest.raises(annexes.DonneesInsuffisantes, match="trop peu pour valider"):
        annexes.valider(_matchs(200), "corners", taille_initiale=190)


def test_enregistrer_puis_relire_une_validation(monkeypatch, tmp_path) -> None:
    import config
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    monkeypatch.setattr(config, "creer_dossiers", lambda: None)
    origine = _validation()
    annexes.enregistrer_validation(origine)
    assert annexes.charger_validation("SP1", "corners") == origine
    assert annexes.charger_validation("SP1", "jaunes") is None


def test_une_validation_corrompue_ne_fait_pas_tomber_la_page(monkeypatch, tmp_path) -> None:
    import config
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    (tmp_path / "validation_corners_SP1.json").write_text("{casse", encoding="utf-8")
    assert annexes.charger_validation("SP1", "corners") is None
