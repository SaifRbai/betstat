"""Tests de paris/performance.py : la regle est-elle jugee honnetement ?"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config
from paris import performance


def _perf(**remplacements) -> performance.Performance:
    base = dict(championnat="SP1", a_melange=0.3, nb_selections=2220, nb_paris=255,
                roi=-0.223, roi_bas=-0.408, roi_haut=-0.032,
                esperance_annoncee=0.081, roi_favori_marche=0.046,
                roi_tout_parier=-0.052, matchs=740, saisons=4,
                calcule_le="2026-08-20T21:00:00+00:00")
    return performance.Performance(**{**base, **remplacements})


def test_un_intervalle_entierement_negatif_est_significatif() -> None:
    p = _perf()
    assert p.significatif is True
    assert "PERDU 22.3%" in p.verdict
    assert "annoncait +8.1%" in p.verdict


def test_un_intervalle_qui_contient_zero_ne_conclut_pas() -> None:
    """Le cas de la Ligue 1 : perte apparente, mais rien de demontre."""
    p = _perf(roi=-0.171, roi_bas=-0.382, roi_haut=0.056)
    assert p.significatif is False
    assert "ni gagnante ni perdante" in p.verdict
    # Surtout : ne pas annoncer une perte comme etablie.
    assert "PERDU" not in p.verdict


def test_un_intervalle_entierement_positif_est_reconnu() -> None:
    p = _perf(roi=0.09, roi_bas=0.02, roi_haut=0.16)
    assert p.significatif is True
    assert "a gagne" in p.verdict


def test_la_table_des_selections_empile_les_trois_issues() -> None:
    predictions = pd.DataFrame({
        "p_modele_1": [0.50, 0.20], "p_modele_N": [0.30, 0.30],
        "p_modele_2": [0.20, 0.50],
        "cote_1": [2.0, 5.0], "cote_N": [3.4, 3.4], "cote_2": [5.0, 2.0],
        "cote_1_max": [2.1, 5.2], "cote_N_max": [3.5, 3.5], "cote_2_max": [5.2, 2.1],
        "issue": ["1", "2"],
    })
    table = performance._table_selections(predictions)
    assert len(table) == 6                       # 2 matchs x 3 issues
    assert set(table["selection"]) == {"1", "N", "2"}
    # Une seule issue realisee par match.
    assert table.groupby("match")["realise"].sum().tolist() == [1, 1]
    # Le melange reste borne par le modele et le marche.
    for ligne in table.itertuples():
        assert min(ligne.p_modele, ligne.p_marche) - 1e-9 <= ligne.p_finale
        assert ligne.p_finale <= max(ligne.p_modele, ligne.p_marche) + 1e-9


def test_sans_cotes_historiques_le_message_est_actionnable() -> None:
    predictions = pd.DataFrame({
        "p_modele_1": [0.5], "p_modele_N": [0.3], "p_modele_2": [0.2],
        "cote_1": [np.nan], "cote_N": [np.nan], "cote_2": [np.nan],
        "cote_1_max": [np.nan], "cote_N_max": [np.nan], "cote_2_max": [np.nan],
        "issue": ["1"],
    })
    assert performance._table_selections(predictions).empty


def test_enregistrer_puis_relire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    monkeypatch.setattr(config, "creer_dossiers", lambda: None)
    origine = _perf()
    performance.enregistrer(origine)
    relu = performance.charger("SP1")
    assert relu == origine
    assert relu.verdict == origine.verdict


def test_une_mesure_anterieure_au_poids_reste_lisible(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Les fichiers ecrits avant l'ajout du poids ne doivent pas devenir illisibles."""
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    ancien = {k: v for k, v in _perf().__dict__.items() if k != "a_melange"}
    (tmp_path / "performance_regle_SP1.json").write_text(
        json.dumps(ancien), encoding="utf-8")
    relu = performance.charger("SP1")
    assert relu is not None
    # On lui attribue la valeur qui etait alors codee en dur, pas une invention.
    assert relu.a_melange == config.POIDS_MELANGE_DEFAUT


def test_charger_absent_renvoie_none(monkeypatch: pytest.MonkeyPatch,
                                     tmp_path: Path) -> None:
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    assert performance.charger("ZZ") is None


def test_un_fichier_corrompu_ne_fait_pas_tomber_la_page(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "DOSSIER_CALIBRATION", tmp_path)
    (tmp_path / "performance_regle_SP1.json").write_text("{pas du json", encoding="utf-8")
    assert performance.charger("SP1") is None
