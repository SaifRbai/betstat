"""Tests du modele commun aux divisions d'un pays.

Aucun telechargement : les historiques sont des bouchons. Ce qui est verifie
ici n'est pas la qualite des probabilites — elle depend de Dixon-Coles, teste
ailleurs — mais la regle qui decide QUAND on a le droit d'en produire.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
from modele import inter_divisions as inter


def _division(equipes: list[str], debut: str = "2024-01-01", n: int = 60) -> pd.DataFrame:
    """Un mini-championnat : chaque equipe rencontre les autres, scores fixes."""
    lignes = []
    generateur = np.random.default_rng(0)
    dates = pd.date_range(debut, periods=n, freq="3D")
    for i in range(n):
        dom, ext = generateur.choice(equipes, size=2, replace=False)
        lignes.append({"date": dates[i], "dom": dom, "ext": ext,
                       "buts_dom": int(generateur.integers(0, 4)),
                       "buts_ext": int(generateur.integers(0, 3))})
    return pd.DataFrame(lignes)


# --------------------------------------------------------------------------
# La regle de connexite
# --------------------------------------------------------------------------

def test_deux_divisions_reliees_forment_une_seule_composante():
    """Les equipes-pont sont ce qui met deux divisions a la meme echelle."""
    charges = {
        "X1": _division(["A", "B", "C", "PONT1", "PONT2", "PONT3"]),
        "X2": _division(["D", "E", "F", "PONT1", "PONT2", "PONT3"]),
    }
    retenues, ponts = inter.composante_reliee(charges, "X1")
    assert retenues == ["X1", "X2"]
    assert ponts["X1/X2"] == 3


def test_une_division_sans_equipe_pont_est_ecartee():
    """Le cas du Portugal : deux divisions sans equipe commune n'ont pas
    d'ecart de niveau mesurable. Les melanger produirait des probabilites
    sans signification, ce qui est pire que de ne rien produire."""
    charges = {
        "X1": _division(["A", "B", "C"]),
        "X2": _division(["D", "E", "F"]),
    }
    retenues, ponts = inter.composante_reliee(charges, "X1")
    assert retenues == ["X1"]
    assert ponts == {}


def test_le_seuil_d_equipes_pont_est_respecte():
    """Une seule equipe-pont suffit en theorie, pas en pratique : tout l'ecart
    entre les deux divisions reposerait sur ses quelques matchs."""
    communes = ["P1", "P2"]           # deux equipes : sous le seuil de trois
    charges = {
        "X1": _division(["A", "B", *communes]),
        "X2": _division(["D", "E", *communes]),
    }
    assert inter.composante_reliee(charges, "X1")[0] == ["X1"]
    charges["X2"] = _division(["D", "E", *communes, "P3"])
    charges["X1"] = _division(["A", "B", *communes, "P3"])
    assert inter.composante_reliee(charges, "X1")[0] == ["X1", "X2"]


def test_la_chaine_se_propage_de_proche_en_proche():
    """E0 ne partage aucune equipe avec la National League, mais la chaine
    E0-E1-E2-E3-EC les relie. Le parcours doit donc etre transitif."""
    charges = {
        "X1": _division(["A", "B", "AB1", "AB2", "AB3"]),
        "X2": _division(["AB1", "AB2", "AB3", "BC1", "BC2", "BC3"]),
        "X3": _division(["BC1", "BC2", "BC3", "Z"]),
    }
    retenues, _ = inter.composante_reliee(charges, "X1")
    assert retenues == ["X1", "X2", "X3"]


def test_l_ordre_hierarchique_est_conserve():
    """L'ordre de parcours ne doit pas fuiter dans l'affichage : on veut
    E0, E1, E2 et non l'ordre dans lequel le graphe a ete visite."""
    charges = {
        "X1": _division(["A", "P1", "P2", "P3"]),
        "X2": _division(["P1", "P2", "P3", "Q1", "Q2", "Q3"]),
        "X3": _division(["Q1", "Q2", "Q3"]),
    }
    assert inter.composante_reliee(charges, "X2")[0] == ["X1", "X2", "X3"]


# --------------------------------------------------------------------------
# Le refus
# --------------------------------------------------------------------------

def test_un_pays_a_une_seule_division_refuse_d_estimer(monkeypatch):
    """Ni la Grece ni les Pays-Bas n'ont de deuxieme division publiee : le
    modele commun y serait le modele de championnat, sans rien apporter."""
    monkeypatch.setattr(inter, "_pools", lambda divisions, saisons: {
        divisions[0]: _division(["A", "B", "C"])})
    with pytest.raises(ValueError, match="au moins deux divisions"):
        inter.entrainer("G1", saisons=4)


def test_un_championnat_sans_historique_remonte_clairement(monkeypatch):
    monkeypatch.setattr(inter, "_pools", lambda divisions, saisons: {})
    with pytest.raises(ValueError, match="Aucun historique"):
        inter.entrainer("E0", saisons=4)


# --------------------------------------------------------------------------
# Le rapport
# --------------------------------------------------------------------------

def test_le_rapport_dit_ce_qui_a_ete_ecarte_et_pourquoi(monkeypatch):
    monkeypatch.setattr(inter, "_pools", lambda divisions, saisons: {
        "E0": _division(["Arsenal", "Chelsea", "P1", "P2", "P3"]),
        "E1": _division(["Coventry", "Hull", "P1", "P2", "P3"]),
        "EC": _division(["Altrincham", "Barnet"]),        # aucun pont
    })
    modele, rapport = inter.entrainer("E0", saisons=4)
    assert rapport.divisions == ["E0", "E1"]
    assert "EC" in rapport.ecartees
    assert "echelles non comparables" in rapport.ecartees["EC"]
    assert rapport.ponts == {"E0/E1": 3}
    assert "Arsenal" in modele.equipes and "Coventry" in modele.equipes
    assert "Altrincham" not in modele.equipes


def test_le_modele_commun_sait_comparer_deux_divisions(monkeypatch):
    """Le but de tout l'exercice : Arsenal - Coventry devient estimable."""
    monkeypatch.setattr(inter, "_pools", lambda divisions, saisons: {
        "E0": _division(["Arsenal", "Chelsea", "P1", "P2", "P3"]),
        "E1": _division(["Coventry", "Hull", "P1", "P2", "P3"]),
    })
    modele, _ = inter.entrainer("E0", saisons=4)
    prediction = modele.predire("Arsenal", "Coventry")
    total = sum(prediction["1N2"][cle] for cle in ("1", "N", "2"))
    assert total == pytest.approx(1.0, abs=0.02)


# --------------------------------------------------------------------------
# La configuration
# --------------------------------------------------------------------------

def test_les_divisions_d_un_pays_sont_ordonnees_du_haut_vers_le_bas():
    assert config.divisions_du_pays("E0") == ("E0", "E1", "E2", "E3", "EC")
    assert config.divisions_du_pays("EC") == ("E0", "E1", "E2", "E3", "EC")
    assert config.divisions_du_pays("F2") == ("F1", "F2")
    assert config.divisions_du_pays("G1") == ("G1",)


def test_toute_division_declaree_a_un_pays_connu():
    for code, info in config.CHAMPIONNATS.items():
        assert info["pays"], f"{code} sans pays"
        assert code in config.DIVISIONS_PAR_PAYS[info["pays"]]
