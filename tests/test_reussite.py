"""Tests de paris/reussite.py : le comptage des pronos passes par tranche."""

from __future__ import annotations

import pandas as pd

from paris import reussite


def _prediction(p1, pn, p2, plus, bd, be) -> dict:
    return {"p_modele_1": p1, "p_modele_N": pn, "p_modele_2": p2,
            "p_modele_plus25": plus, "buts_dom": bd, "buts_ext": be}


def test_tranches() -> None:
    assert reussite.tranche(0.49) is None
    assert reussite.tranche(0.5) == 0.5
    assert reussite.tranche(0.79) == 0.7
    assert reussite.tranche(0.95) == 0.9
    assert reussite.tranche(1.0) == 0.9


def test_chaque_prono_est_compte_dans_sa_tranche() -> None:
    predictions = pd.DataFrame([
        _prediction(0.62, 0.22, 0.16, 0.55, 2, 0),    # 1 passe, 1N passe, +2.5 rate
        _prediction(0.65, 0.20, 0.15, 0.30, 0, 1),    # 1 rate, -2.5 passe
    ])
    table = reussite.compter(predictions)
    assert table["1N2"]["0.6"] == [2, 1]
    # 1N a 84 % (passe) et 85 % (rate), 12 a 80 % (passe)
    assert table["double chance"]["0.8"] == [3, 2]
    assert table["totaux 2.5"]["0.5"] == [1, 0]       # +2.5 a 55 %, 2 buts
    assert table["totaux 2.5"]["0.7"] == [1, 1]       # -2.5 a 70 %, 1 but


def test_la_ligue_d_abord_puis_toutes_les_ligues() -> None:
    petite = {"familles": {"1N2": {"0.7": [10, 8]}}}
    grande = {"familles": {"1N2": {"0.7": [100, 74]}}}
    m = reussite.taux("1N2", 0.75, petite, [petite, grande])
    assert m["portee"] == "toutes" and m["nombre"] == 110 and m["passes"] == 82
    m = reussite.taux("1N2", 0.75, grande, [petite, grande])
    assert m["portee"] == "ligue" and m["taux"] == 0.74
    assert reussite.taux("1N2", 0.45, grande, [grande]) is None
    assert reussite.taux("btts", 0.75, grande, [grande]) is None
