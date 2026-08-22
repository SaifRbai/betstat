"""Tests de paris/confiance.py : classement des marches et honnetete des reserves."""

from __future__ import annotations

import pytest

import config
from paris import confiance


PREDICTION = {
    "1N2": {"1": 0.55, "N": 0.25, "2": 0.20},
    "double_chance": {"1N": 0.80, "12": 0.75, "N2": 0.45},
    "totaux": {
        "plus_0.5": 0.95, "moins_0.5": 0.05,
        "plus_1.5": 0.80, "moins_1.5": 0.20,
        "plus_2.5": 0.55, "moins_2.5": 0.45,
        "plus_3.5": 0.30, "moins_3.5": 0.70,
        "plus_4.5": 0.15, "moins_4.5": 0.85,
    },
    "btts": {"oui": 0.52, "non": 0.48},
    "clean_sheet": {"dom": 0.35, "ext": 0.22},
}


def test_les_marches_sont_classes_du_plus_probable_au_moins_probable() -> None:
    pronos = confiance.lister(PREDICTION, "Lyon", "Nice")
    probabilites = [p.p_modele for p in pronos]
    assert probabilites == sorted(probabilites, reverse=True)
    assert pronos[0].libelle == "Plus de 0.5 buts"
    # 3 (1N2) + 3 (double chance) + 2 (btts) + 2 (clean sheet) + 10 (totaux)
    assert len(pronos) == 20


def test_le_cout_d_un_echec_est_exact() -> None:
    p = confiance.prono("totaux 0.5", "plus", "Plus de 0.5 buts", 0.95)
    assert p.cote_equitable == pytest.approx(1 / 0.95)
    assert p.echoue_1_fois_sur == pytest.approx(20.0)
    # Une perte de 1 contre dix-neuf gains de (1/0.95 - 1) : c'est le chiffre
    # que « pari sans risque » fait oublier.
    assert p.gains_annules_par_une_perte == pytest.approx(19.0)
    assert p.niveau == confiance.NIVEAU_TRES_PROBABLE


def test_le_desaccord_du_marche_degrade_le_niveau() -> None:
    ecart = config.SEUIL_DIVERGENCE_ALERTE + 0.05
    p = confiance.prono("1N2", "1", "Victoire Lyon", 0.90, p_marche=0.90 - ecart)
    assert p.niveau == confiance.NIVEAU_RESERVE
    assert any("le marche dit" in r for r in p.reserves)
    # Un accord large ne cree aucune reserve.
    accord = confiance.prono("1N2", "1", "Victoire Lyon", 0.90, p_marche=0.88)
    assert accord.niveau == confiance.NIVEAU_TRES_PROBABLE
    assert accord.reserves == []


def test_une_reserve_commune_touche_tous_les_marches() -> None:
    pronos = confiance.lister(PREDICTION, "Lyon", "Nice",
                              reserves_communes=["Nice n'a que 2 match(s)"])
    assert all(p.niveau == confiance.NIVEAU_RESERVE for p in pronos)
    assert confiance.les_plus_surs(pronos) == []


def test_les_plus_surs_ecarte_les_reserves_et_les_probabilites_faibles() -> None:
    marche = {("1N2", "1"): 0.20}          # contredit franchement le modele a 55 %
    pronos = confiance.lister(PREDICTION, "Lyon", "Nice", marche)
    surs = confiance.les_plus_surs(pronos)
    assert all(p.reserves == [] for p in surs)
    assert all(p.p_modele >= confiance.SEUIL_PROBABLE for p in surs)
    assert len(surs) <= 3
    assert "Victoire Lyon" not in [p.libelle for p in surs]


def test_prono_1n2_retient_l_issue_la_plus_probable() -> None:
    p = confiance.prono_1n2(PREDICTION, "Lyon", "Nice")
    assert p["selection"] == "1"                 # 55 % contre 25 % et 20 %
    assert p["libelle"] == "Victoire Lyon"
    assert p["p_modele"] == pytest.approx(0.55)
    assert p["cote_equitable"] == pytest.approx(1 / 0.55)
    # Sans cote ni marche, rien n'est invente.
    assert p["p_marche"] is None
    assert p["meilleure_cote"] is None
    assert p["esperance"] is None
    assert p["p_finale"] == pytest.approx(0.55)


def test_la_couverture_inclut_toujours_le_nul() -> None:
    lyon = confiance.prono_1n2(PREDICTION, "Lyon", "Nice")["couverture"]
    assert lyon["selection"] == "1N"             # jamais "12", qui exclut le nul
    assert lyon["libelle"] == "Lyon ou nul"
    assert lyon["p_modele"] == pytest.approx(0.80)
    assert lyon["gagne_en_probabilite"] == pytest.approx(0.25)
    # Ce que la couverture coute : la cote equitable s'ecrase.
    assert lyon["cote_equitable"] == pytest.approx(1.25)
    assert lyon["perd_en_cote"] == pytest.approx(1 / 0.55 - 1.25)

    victoire_ext = {**PREDICTION, "1N2": {"1": 0.20, "N": 0.25, "2": 0.55}}
    assert confiance.prono_1n2(victoire_ext, "Lyon", "Nice")["couverture"]["selection"] == "N2"

    # Sur un nul favori, les deux couvertures conviennent : on prend la meilleure.
    nul = {**PREDICTION, "1N2": {"1": 0.30, "N": 0.40, "2": 0.30}}
    assert confiance.prono_1n2(nul, "Lyon", "Nice")["couverture"]["selection"] == "1N"


def test_l_esperance_est_calculee_sur_la_probabilite_melangee() -> None:
    """Le point le plus important du module : ne pas parier sur son propre ecart."""
    marche = {("1N2", "1"): 0.40}
    p = confiance.prono_1n2(PREDICTION, "Lyon", "Nice", marche, {"1": 2.20})
    # Le melange rapproche le modele (55 %) du marche (40 %).
    assert 0.40 < p["p_finale"] < 0.55
    assert p["esperance"] == pytest.approx(p["p_finale"] * 2.20 - 1)
    # Calculee sur le modele seul, elle vaudrait bien davantage : c'est
    # exactement l'illusion que le melange evite.
    assert p["esperance"] < 0.55 * 2.20 - 1
    assert p["accord_marche"] == pytest.approx(0.15)


def test_prono_1n2_sans_1n2_ne_renvoie_rien() -> None:
    assert confiance.prono_1n2({"totaux": {"plus_2.5": 0.5}}, "Lyon", "Nice") is None


def test_prono_1n2_porte_les_reserves_du_match() -> None:
    p = confiance.prono_1n2(PREDICTION, "Lyon", "Nice",
                            reserves_communes=["Nice n'a que 2 match(s)"])
    assert p["niveau"] == confiance.NIVEAU_RESERVE
    assert p["reserves"] == ["Nice n'a que 2 match(s)"]


def test_serialisation_pour_la_page() -> None:
    pronos = confiance.lister(PREDICTION, "Lyon", "Nice")
    lignes = confiance.en_dictionnaires(pronos)
    assert set(lignes[0]) == {"marche", "selection", "libelle", "p_modele", "p_marche",
                              "cote_equitable", "echoue_1_fois_sur", "gains_annules",
                              "niveau", "reserves"}


def test_probabilite_extreme_ne_divise_pas_par_zero() -> None:
    p = confiance.prono("totaux 0.5", "plus", "Plus de 0.5 buts", 1.0)
    assert p.cote_equitable == pytest.approx(1.0)
    assert p.echoue_1_fois_sur > 1e6


def test_prono_comptes_choisit_la_ligne_qui_encadre_le_total() -> None:
    prediction = {
        "type": "corners", "libelle": "corners",
        "attendu_dom": 5.8, "attendu_ext": 4.4, "attendu_total": 10.2,
        "totaux": {"plus_8.5": 0.69, "moins_8.5": 0.31,
                   "plus_10.5": 0.44, "moins_10.5": 0.56,
                   "plus_12.5": 0.22, "moins_12.5": 0.78},
        "comparaison": {"1": 0.61, "N": 0.12, "2": 0.27},
        "dispersion": 1.49, "matchs": 1145,
    }
    p = confiance.prono_comptes(prediction, "Vallecano", "Alaves")
    assert p["ligne"] == 10.5                     # la plus proche de 10.2
    assert p["selection"] == "moins"              # 56 % contre 44 %
    assert p["p_modele"] == pytest.approx(0.56)
    assert p["duel"]["libelle"] == "Vallecano en obtient le plus"
    assert len(p["tous_les_totaux"]) == 3


def test_un_prono_annexe_porte_toujours_la_reserve_du_marche_absent() -> None:
    """Le point de fond : ces marches ne sont jamais confrontes aux bookmakers."""
    prediction = {
        "type": "jaunes", "libelle": "cartons jaunes",
        "attendu_dom": 2.7, "attendu_ext": 2.8, "attendu_total": 5.5,
        "totaux": {"plus_5.5": 0.48, "moins_5.5": 0.52},
        "comparaison": {"1": 0.39, "N": 0.17, "2": 0.44},
        "dispersion": 0.97, "matchs": 1145,
    }
    p = confiance.prono_comptes(prediction, "Lyon", "Nice")
    assert confiance.RESERVE_SANS_MARCHE in p["reserves"]
    assert p["niveau"] == confiance.NIVEAU_RESERVE
    # Dispersion saine : pas de seconde reserve.
    assert len(p["reserves"]) == 1

    sur_disperse = {**prediction, "dispersion": 1.49}
    reserves = confiance.prono_comptes(sur_disperse, "Lyon", "Nice")["reserves"]
    assert len(reserves) == 2
    assert any("dispersion mesuree 1.49" in r for r in reserves)


def test_prono_comptes_sans_totaux_ne_renvoie_rien() -> None:
    assert confiance.prono_comptes({"attendu_total": 5.0}, "Lyon", "Nice") is None
