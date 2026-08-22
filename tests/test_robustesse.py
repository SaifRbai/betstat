"""Cas limites de T17 : rien ne doit produire une trace d'exception brute.

Chaque cas ci-dessous correspond a une situation qui arrive vraiment :
reseau coupe, cle absente, quota epuise, championnat sans xG, equipe promue,
match reporte, saison en cours encore vide au mois de juillet.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config
from data import cotes as cotes_module
from data import noms as noms_module
from data import resultats as resultats_module
from data import xg as xg_module
from modele import calibration
from modele import dixon_coles as dc


# --------------------------------------------------------------------------
# Cles API et configuration
# --------------------------------------------------------------------------

def test_cle_absente_message_actionnable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(config, "_env_charge", True)
    with pytest.raises(config.CleApiManquante) as erreur:
        config.cle_api("odds")
    message = str(erreur.value)
    assert ".env" in message                      # quel fichier editer
    assert "ODDS_API_KEY" in message              # quelle ligne ajouter
    assert "the-odds-api.com" in message          # ou obtenir la cle


def test_cle_facultative_renvoie_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(config, "_env_charge", True)
    assert config.cle_api("odds", obligatoire=False) is None


def test_valeur_gabarit_non_remplie_est_traitee_comme_absente(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODDS_API_KEY", "votre_cle_ici")
    monkeypatch.setattr(config, "_env_charge", True)
    assert config.cle_api("odds", obligatoire=False) is None


def test_service_inconnu() -> None:
    with pytest.raises(KeyError, match="Service API inconnu"):
        config.cle_api("bookmaker_imaginaire")


def test_dossiers_crees_au_premier_import() -> None:
    assert config.DOSSIER_DONNEES.exists()
    assert config.DOSSIER_CACHE.exists()
    assert str(config.DOSSIER_DONNEES) == os.environ["FOOT_EDGE_HOME"]


# --------------------------------------------------------------------------
# Donnees absentes ou incompletes
# --------------------------------------------------------------------------

ENTETE = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,"
          "HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR\n")


def test_saison_en_cours_vide_en_juillet(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """En juillet, le CSV de la saison a venir ne contient que son en-tete."""
    monkeypatch.setattr(resultats_module.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    saison = resultats_module.dernieres_saisons(1)[0]
    (tmp_path / f"F1_{saison}.csv").write_text(ENTETE, encoding="utf-8")
    propre = resultats_module.nettoyer(
        resultats_module.lire_csv(tmp_path / f"F1_{saison}.csv"), saison, "F1")
    assert propre.empty
    assert list(propre.columns) == list(resultats_module.COLONNES_NORMALISEES)


def test_match_reporte_est_ecarte_pas_compte_comme_zero_zero(tmp_path: Path) -> None:
    """Une ligne avec une date mais sans score est un match reporte, pas un 0-0."""
    contenu = ENTETE + (
        "F1,09/08/2019,19:45,Monaco,Lyon,,,,,,,,,,,,,,,,,,\n"
        "F1,10/08/2019,17:00,Nice,Reims,2,1,H,1,0,H,10,8,4,3,9,11,5,3,1,2,0,0\n")
    chemin = tmp_path / "F1_1920.csv"
    chemin.write_text(contenu, encoding="utf-8")
    propre = resultats_module.nettoyer(resultats_module.lire_csv(chemin), "1920", "F1")
    assert len(propre) == 1
    assert propre.iloc[0]["dom"] == "Nice"


def test_equipe_promue_avec_deux_matchs(tmp_path: Path) -> None:
    """Un promu a peu de matchs : le modele doit l'estimer sans planter."""
    generateur = np.random.default_rng(4)
    equipes = [f"Equipe {i:02d}" for i in range(12)]
    matchs = dc.simuler_championnat(
        equipes, {e: 0.0 for e in equipes}, {e: 0.1 for e in equipes}, 0.25,
        nb_tours=4, graine=4)
    promu = pd.DataFrame({
        "date": pd.to_datetime(["2020-09-01", "2020-09-08"]),
        "dom": ["Promu", "Equipe 00"], "ext": ["Equipe 01", "Promu"],
        "buts_dom": [0, 3], "buts_ext": [2, 0],
    })
    modele = dc.entrainer(pd.concat([matchs, promu], ignore_index=True), xi=0.0)
    assert "Promu" in modele.attaque
    probas = modele.predire("Promu", "Equipe 01")["1N2"]
    assert abs(sum(probas.values()) - 1.0) < 1e-9


def test_prediction_equipe_absente_du_modele() -> None:
    equipes = [f"E{i}" for i in range(10)]
    matchs = dc.simuler_championnat(equipes, {e: 0.0 for e in equipes},
                                    {e: 0.0 for e in equipes}, 0.2, nb_tours=3, graine=1)
    modele = dc.entrainer(matchs, xi=0.0)
    with pytest.raises(KeyError, match="Equipe absente du modele"):
        modele.predire("Club Fantome", "E1")


# --------------------------------------------------------------------------
# xG absents
# --------------------------------------------------------------------------

def test_championnat_sans_xg_renvoie_un_tableau_vide(caplog: pytest.LogCaptureFixture) -> None:
    """La Turquie n'a pas de xG publics : DataFrame vide et log explicite."""
    assert not xg_module.championnat_couvert("T1")
    with caplog.at_level("INFO"):
        resultat = xg_module.charger_xg("T1", 2)
    assert resultat.empty
    assert list(resultat.columns) == list(xg_module.COLONNES_XG)
    assert "Pas de xG publics" in caplog.text


def test_fusion_sans_xg_ne_plante_pas() -> None:
    matchs = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01"]), "dom": ["Lyon"], "ext": ["Nice"],
        "buts_dom": [1], "buts_ext": [0]})
    enrichi, rapport = xg_module.fusionner(matchs, xg_module._dataframe_vide())
    assert enrichi["xg_dom"].isna().all()
    assert rapport["taux"] == 0.0


def test_fusion_tolerance_d_un_jour() -> None:
    matchs = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-02", "2025-02-01"]),
        "dom": ["Paris SG", "Lyon"], "ext": ["Lyon", "Nice"],
        "buts_dom": [2, 1], "buts_ext": [1, 0]})
    xg = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01", "2025-02-05"]),
        "dom": ["Paris Saint-Germain", "Lyon"], "ext": ["Lyon", "Nice"],
        "xg_dom": [1.8, 1.2], "xg_ext": [0.9, 0.4], "source": ["understat"] * 2})
    enrichi, rapport = xg_module.fusionner(matchs, xg)
    # Le premier match est apparie malgre un jour d'ecart et un nom different ;
    # le second a quatre jours d'ecart et reste donc non apparie.
    assert enrichi.iloc[0]["xg_dom"] == pytest.approx(1.8)
    assert pd.isna(enrichi.iloc[1]["xg_dom"])
    assert rapport["apparies"] == 1
    assert len(rapport["non_apparies"]) == 1


def test_avertissement_sous_90_pourcent(caplog: pytest.LogCaptureFixture) -> None:
    rapport = {"total": 10, "apparies": 5, "taux": 0.5,
               "non_apparies": ["01/01/2025 A - B"]}
    with caplog.at_level("WARNING"):
        message = xg_module.afficher_rapport(rapport, "F1")
    assert "inferieur a 90" in message
    assert "verifier_noms.py" in message


# --------------------------------------------------------------------------
# Cotes indisponibles
# --------------------------------------------------------------------------

def test_recuperer_sans_cle_message_clair(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(config, "_env_charge", True)
    monkeypatch.setattr(cotes_module, "_chemin_cache", lambda c: tmp_path / f"{c}.json")
    monkeypatch.setattr(cotes_module, "lire_compteur",
                        lambda: {"mois": "2026-08", "appels": 0, "restant_api": None})
    with pytest.raises(config.CleApiManquante, match="the-odds-api.com"):
        cotes_module.recuperer_brut("F1", forcer=True)


def test_devig_refuse_des_cotes_manquantes() -> None:
    with pytest.raises(ValueError, match="Cotes invalides"):
        calibration.deviguer([2.0, float("nan"), 3.0])


def test_consensus_ignore_un_bookmaker_incomplet() -> None:
    """Un book qui ne cote qu'une selection n'a pas de marge calculable."""
    cotes = pd.DataFrame([
        {"date": "2026-01-01", "dom": "A", "ext": "B", "marche": "1N2",
         "bookmaker": "complet", "selection": s, "cote": c}
        for s, c in zip(("1", "N", "2"), (2.10, 3.40, 3.30))
    ] + [{"date": "2026-01-01", "dom": "A", "ext": "B", "marche": "1N2",
          "bookmaker": "partiel", "selection": "1", "cote": 2.50}])
    resultat = calibration.consensus(cotes)
    assert len(resultat) == 3
    assert resultat["p_marche"].sum() == pytest.approx(1.0)
    # La meilleure cote reste celle du book partiel : c'est une vraie information.
    assert resultat[resultat["selection"] == "1"].iloc[0]["meilleure_cote"] == 2.50


def test_noms_inconnus_ne_sont_jamais_devines() -> None:
    equipes = ["Club Totalement Inconnu", "Autre Club Inconnu"]
    correspondances, inconnus = noms_module.canoniser_liste(equipes)
    assert correspondances == {}
    assert inconnus == equipes
