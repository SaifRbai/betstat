"""Tests du de-vig, du melange et des hyperparametres (T06, T08, T09)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
from modele import calibration as cal

COTES_EXEMPLE = [2.10, 3.40, 3.30]


# --------------------------------------------------------------------------
# T08 — de-vig
# --------------------------------------------------------------------------

@pytest.mark.parametrize("methode", ["multiplicatif", "puissance", "shin"])
def test_devig_somme_a_un(methode: str) -> None:
    probas = cal.deviguer(COTES_EXEMPLE, methode)
    assert probas.sum() == pytest.approx(1.0, abs=1e-12)
    assert (probas > 0).all()


def test_surcote_positive() -> None:
    assert cal.surcote(COTES_EXEMPLE) == pytest.approx(0.0733, abs=1e-4)
    assert cal.surcote([2.0, 2.0]) == pytest.approx(0.0, abs=1e-12)


def test_ordre_des_methodes_sur_le_favori() -> None:
    """Le multiplicatif surestime les outsiders, donc SOUS-estime le favori.

    Le critere d'acceptation de TACHES.txt annonce l'inverse (Shin en dessous
    du multiplicatif sur le favori) ; c'est incompatible avec sa propre
    description du biais multiplicatif. On teste la relation mathematique
    reelle : puissance et Shin rendent au favori la probabilite que le
    multiplicatif lui retire.
    """
    multiplicatif = cal.devig_multiplicatif(COTES_EXEMPLE)
    puissance = cal.devig_puissance(COTES_EXEMPLE)
    shin = cal.devig_shin(COTES_EXEMPLE)
    assert shin[0] > multiplicatif[0]
    assert puissance[0] > multiplicatif[0]
    # Shin reste plus conservateur que la methode puissance.
    assert shin[0] < puissance[0]
    # Et le classement des issues n'est jamais bouleverse.
    for probas in (multiplicatif, puissance, shin):
        assert np.argmax(probas) == 0


def test_devig_sans_marge_est_neutre() -> None:
    probas = cal.deviguer([2.0, 4.0, 4.0], "shin")
    assert probas == pytest.approx([0.5, 0.25, 0.25], abs=1e-9)


def test_devig_favori_extreme() -> None:
    probas = cal.deviguer([1.05, 15.0, 30.0], "shin")
    assert probas.sum() == pytest.approx(1.0, abs=1e-12)
    assert probas[0] > 0.9


def test_devig_cotes_invalides() -> None:
    with pytest.raises(ValueError, match="Cotes invalides"):
        cal.deviguer([0.9, 3.0, 3.0])
    with pytest.raises(ValueError, match="Cotes invalides"):
        cal.deviguer([2.0, float("nan")])


def test_consensus_mediane_inter_bookmakers() -> None:
    cotes = pd.DataFrame([
        {"date": "2025-01-01", "dom": "A", "ext": "B", "marche": "1N2",
         "bookmaker": bk, "selection": sel, "cote": cote}
        for bk, prix in {"bk1": (2.10, 3.40, 3.30), "bk2": (2.05, 3.50, 3.40),
                         "bk3": (2.20, 3.30, 3.20)}.items()
        for sel, cote in zip(("1", "N", "2"), prix)
    ])
    resultat = cal.consensus(cotes)
    assert len(resultat) == 3
    assert resultat["p_marche"].sum() == pytest.approx(1.0, abs=1e-9)
    assert set(resultat["nb_bookmakers"]) == {3}
    meilleure = resultat[resultat["selection"] == "1"].iloc[0]
    assert meilleure["meilleure_cote"] == pytest.approx(2.20)
    assert meilleure["bookmaker_meilleure"] == "bk3"


def test_consensus_vide() -> None:
    assert cal.consensus(pd.DataFrame()).empty


# --------------------------------------------------------------------------
# T09 — melange
# --------------------------------------------------------------------------

def test_melange_geometrique_bornes() -> None:
    modele = np.array([0.5, 0.3, 0.2])
    marche = np.array([0.4, 0.3, 0.3])
    assert cal.melange_geometrique(modele, marche, 1.0) == pytest.approx(modele)
    assert cal.melange_geometrique(modele, marche, 0.0) == pytest.approx(marche)
    milieu = cal.melange_geometrique(modele, marche, 0.5)
    assert milieu.sum() == pytest.approx(1.0)
    assert marche[0] < milieu[0] < modele[0]


def test_combiner_renvoie_les_trois_probabilites() -> None:
    resultat = cal.combiner([0.5, 0.3, 0.2], [0.4, 0.3, 0.3], a=0.3)
    detail = resultat.en_dict()
    assert set(detail) == {"modele", "marche", "finale"}
    assert resultat.ecart_max == pytest.approx(0.1, abs=1e-9)


def test_log_loss_et_brier() -> None:
    probas = np.array([[0.7, 0.2, 0.1], [0.2, 0.3, 0.5]])
    assert cal.log_loss(probas, ["1", "2"]) == pytest.approx(
        -(np.log(0.7) + np.log(0.5)) / 2)
    assert cal.brier(probas, ["1", "2"]) == pytest.approx(
        ((0.09 + 0.04 + 0.01) + (0.04 + 0.09 + 0.25)) / 2)


def test_resultat_1n2() -> None:
    matchs = pd.DataFrame({"buts_dom": [2, 1, 0], "buts_ext": [1, 1, 2]})
    assert cal.resultat_1n2(matchs).tolist() == ["1", "N", "2"]


# --------------------------------------------------------------------------
# Calibration isotonique
# --------------------------------------------------------------------------

def test_isotonique_est_croissante_et_corrige_la_sur_confiance() -> None:
    generateur = np.random.default_rng(3)
    annoncees = generateur.uniform(0.05, 0.95, 400)
    reelles = annoncees * 0.7 + 0.1  # modele sur-confiant
    tirages = (generateur.uniform(size=400) < reelles).astype(int)
    calibrateur = cal.entrainer_isotonique(annoncees, tirages)
    corrigees = calibrateur.appliquer([0.1, 0.3, 0.5, 0.7, 0.9])
    assert all(corrigees[i] <= corrigees[i + 1] + 1e-9 for i in range(len(corrigees) - 1))
    assert corrigees[-1] < 0.9  # la sur-confiance est bien rabotee


def test_isotonique_trop_peu_de_donnees() -> None:
    with pytest.raises(ValueError, match="30 observations"):
        cal.entrainer_isotonique([0.5] * 10, [1] * 10)


def test_calibration_multiclasse_renormalise() -> None:
    generateur = np.random.default_rng(5)
    probas = generateur.dirichlet([3, 2, 2], size=300)
    issues = generateur.choice(["1", "N", "2"], size=300)
    calibrateurs = cal.calibrer_multiclasse(probas, issues)
    corrigees = cal.appliquer_calibration(probas, calibrateurs)
    assert np.allclose(corrigees.sum(axis=1), 1.0)


# --------------------------------------------------------------------------
# Persistance du poids de melange (T-corrections)
# --------------------------------------------------------------------------

def test_le_poids_mesure_est_enregistre_puis_relu(monkeypatch, tmp_path) -> None:
    """Sans persistance, le poids etait calcule puis jete, et 0.30 s'appliquait."""
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    monkeypatch.setattr(config, "creer_dossiers", lambda: None)
    resultat = {"a": 0.0, "log_loss_modele": 1.0038, "log_loss_marche": 0.9633,
                "log_loss_melange": 0.9633, "melange_bat_marche": False,
                "nb_matchs": 1942}
    cal.enregistrer_poids_melange("SP1", resultat)
    relu = cal.charger_poids_melange("SP1")
    assert relu["a"] == 0.0
    assert relu["championnat"] == "SP1"
    assert cal.poids_melange("SP1") == 0.0


def test_un_championnat_jamais_calibre_retombe_sur_le_defaut(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    assert cal.poids_melange("ZZ") == config.POIDS_MELANGE_DEFAUT
    assert cal.poids_melange("ZZ", defaut=0.5) == 0.5


def test_un_fichier_de_poids_corrompu_ne_fait_pas_tomber_la_page(
        monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    (tmp_path / "poids_melange_SP1.json").write_text("{casse", encoding="utf-8")
    assert cal.charger_poids_melange("SP1") is None
    assert cal.poids_melange("SP1") == config.POIDS_MELANGE_DEFAUT


def test_un_melange_egal_au_marche_ne_compte_pas_comme_une_victoire() -> None:
    """a=0 rend le melange identique au marche : un ecart de 1e-16 n'est pas un gain."""
    predictions = pd.DataFrame({
        "p_modele_1": [0.5, 0.3, 0.4] * 20,
        "p_modele_N": [0.3, 0.4, 0.3] * 20,
        "p_modele_2": [0.2, 0.3, 0.3] * 20,
        "cote_1": [2.0, 3.3, 2.5] * 20,
        "cote_N": [3.4, 3.4, 3.4] * 20,
        "cote_2": [5.0, 3.3, 3.0] * 20,
        "issue": ["1", "N", "2"] * 20,
    })
    resultat = cal.calibrer_poids_melange(
        predictions, grille_a=[0.0], afficher=False)
    assert resultat["a"] == 0.0
    assert resultat["melange_bat_marche"] is False
