"""Tests de data/cotes.py (T07) : normalisation, quota, cache, historisation."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import config
from data import cotes


EVENEMENT = {
    "home_team": "Paris Saint Germain",
    "away_team": "Olympique de Marseille",
    "commence_time": "2026-09-01T19:00:00Z",
    "bookmakers": [
        {"title": "Book A", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Paris Saint Germain", "price": 1.50},
                {"name": "Draw", "price": 4.20},
                {"name": "Olympique de Marseille", "price": 6.00}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "point": 2.5, "price": 1.70},
                {"name": "Under", "point": 2.5, "price": 2.10},
                {"name": "Over", "point": 3.5, "price": 2.90}]}]},
        {"title": "Book B", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Paris Saint Germain", "price": 1.55},
                {"name": "Draw", "price": 4.00},
                {"name": "Olympique de Marseille", "price": 5.80}]}]},
    ],
}


def test_normalisation_et_canonisation_des_noms() -> None:
    tableau = cotes.normaliser([EVENEMENT], "F1")
    assert set(tableau["dom"]) == {"Paris Saint-Germain"}
    assert set(tableau["ext"]) == {"Marseille"}
    assert set(tableau["marche"]) == {cotes.MARCHE_1N2, cotes.MARCHE_TOTAUX}
    # Le total 3.5 n'est pas un marche suivi par le projet : il est ignore.
    assert len(tableau[tableau["marche"] == cotes.MARCHE_TOTAUX]) == 2
    assert set(tableau[tableau["marche"] == cotes.MARCHE_1N2]["selection"]) == {"1", "N", "2"}


def test_normalisation_vide() -> None:
    assert cotes.normaliser([], "F1").empty


def test_matchs_a_venir_compte_les_bookmakers() -> None:
    resume = cotes.matchs_a_venir(cotes.normaliser([EVENEMENT], "F1"))
    assert len(resume) == 1
    assert resume.iloc[0]["nb_bookmakers"] == 2


def test_detail_par_bookmaker() -> None:
    tableau = cotes.normaliser([EVENEMENT], "F1")
    resume = cotes.matchs_a_venir(tableau).iloc[0]
    detail = cotes.par_bookmaker(tableau, resume["date"], resume["dom"], resume["ext"])
    assert [ligne["bookmaker"] for ligne in detail] == ["Book B", "Book A"]  # marge croissante
    book_a = next(l for l in detail if l["bookmaker"] == "Book A")
    assert (book_a["1"], book_a["N"], book_a["2"]) == (1.50, 4.20, 6.00)
    assert (book_a["plus"], book_a["moins"]) == (1.70, 2.10)
    # Book B ne cote pas les totaux : l'absence est rendue telle quelle.
    book_b = next(l for l in detail if l["bookmaker"] == "Book B")
    assert book_b["plus"] is None and book_b["moins"] is None
    assert cotes.meilleures_cotes(detail) == {
        "1": 1.55, "N": 4.20, "2": 6.00, "plus": 1.70, "moins": 2.10}


def test_detail_par_bookmaker_sur_match_inconnu_ou_tableau_partiel() -> None:
    tableau = cotes.normaliser([EVENEMENT], "F1")
    assert cotes.par_bookmaker(tableau, "2026-01-01", "X", "Y") == []
    # Un tableau ampute d'une colonne ne doit pas faire echouer la page.
    assert cotes.par_bookmaker(tableau.drop(columns=["cote"]),
                               tableau.iloc[0]["date"], "Paris Saint-Germain",
                               "Marseille") == []
    assert cotes.meilleures_cotes([]) == {}


def test_marge_bookmaker() -> None:
    assert cotes.marge_bookmaker({"1": 2.0, "N": 4.0, "2": 4.0}) == pytest.approx(0.0)
    assert cotes.marge_bookmaker({"1": 1.9, "N": 3.8, "2": 3.8}) > 0.05
    assert cotes.marge_bookmaker({"1": 2.0, "N": 4.0}) is None


def test_compteur_de_quota(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cotes, "_chemin_compteur", lambda: tmp_path / "quota.json")
    assert cotes.lire_compteur()["appels"] == 0
    cotes._incrementer_compteur(423)
    etat = cotes.lire_compteur()
    assert etat["appels"] == 1 and etat["restant_api"] == 423


def test_avertissement_au_dela_du_seuil(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    chemin = tmp_path / "quota.json"
    monkeypatch.setattr(cotes, "_chemin_compteur", lambda: chemin)
    chemin.write_text(json.dumps({"mois": cotes._mois_courant(), "appels": 101,
                                  "credits": 404, "restant_api": 96}), encoding="utf-8")
    message = cotes.message_quota()
    assert "ATTENTION" in message
    assert "404 credit" in message


def test_le_quota_compte_des_credits_pas_des_appels(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    """The Odds API facture 1 credit par marche et par region, pas par appel HTTP."""
    chemin = tmp_path / "quota.json"
    monkeypatch.setattr(cotes, "_chemin_compteur", lambda: chemin)
    assert cotes.COUT_CREDITS_PAR_APPEL == 2  # h2h + totals, region eu
    # L'en-tete x-requests-used fait foi quand il est present.
    cotes._incrementer_compteur(restant_api=496, utilises_api=4)
    etat = cotes.lire_compteur()
    assert etat["appels"] == 1
    assert cotes.credits_consommes(etat) == 4
    assert "4 credit(s) consomme(s)" in cotes.message_quota(etat)


def test_estimation_locale_sans_en_tete(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    """Sans en-tete exploitable, on estime le cout au lieu de sous-compter."""
    monkeypatch.setattr(cotes, "_chemin_compteur", lambda: tmp_path / "quota.json")
    cotes._incrementer_compteur(restant_api=None, utilises_api=None)
    assert cotes.credits_consommes() == cotes.COUT_CREDITS_PAR_APPEL


def test_compteur_remis_a_zero_le_mois_suivant(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    chemin = tmp_path / "quota.json"
    monkeypatch.setattr(cotes, "_chemin_compteur", lambda: chemin)
    chemin.write_text(json.dumps({"mois": "1999-01", "appels": 500}), encoding="utf-8")
    assert cotes.lire_compteur()["appels"] == 0


def test_cache_de_quinze_minutes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Deux appels rapproches ne consomment qu'une requete : le second lit le cache."""
    monkeypatch.setattr(config, "DOSSIER_CACHE_COTES", tmp_path)
    monkeypatch.setattr(cotes.config, "DOSSIER_CACHE_COTES", tmp_path)
    monkeypatch.setattr(cotes, "_chemin_cache", lambda c: tmp_path / f"{c}.json")
    (tmp_path / "F1.json").write_text(json.dumps([EVENEMENT]), encoding="utf-8")

    appels = {"n": 0}

    def api_interdite(*_args: object, **_kwargs: object):
        appels["n"] += 1
        raise AssertionError("le cache aurait du suffire")

    monkeypatch.setattr(cotes, "_appeler_api", api_interdite)
    donnees, du_cache = cotes.recuperer_brut("F1")
    assert du_cache and appels["n"] == 0 and len(donnees) == 1


def test_quota_epuise_message_actionnable(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    monkeypatch.setattr(cotes, "_chemin_cache", lambda c: tmp_path / f"{c}.json")
    monkeypatch.setattr(cotes, "lire_compteur",
                        lambda: {"mois": "2026-08", "appels": 500, "restant_api": 0})
    with pytest.raises(cotes.QuotaEpuise, match="Quota mensuel local atteint"):
        cotes.recuperer_brut("F1", forcer=True)


def test_championnat_sans_cle_odds() -> None:
    with pytest.raises(ValueError, match="Championnat inconnu"):
        cotes.recuperer_brut("ZZZ")


def test_historisation_et_relecture(tmp_path: Path) -> None:
    base = tmp_path / "hist.db"
    tableau = cotes.normaliser([EVENEMENT], "F1")
    assert cotes.historiser(tableau, base) > 0
    # Le meme instantane ne doit pas etre insere deux fois.
    cotes.historiser(tableau, base)
    historique = cotes.historique_match("Paris Saint-Germain", "Marseille",
                                        "2026-09-01", chemin_db=base)
    assert len(historique) == 6  # 2 books x 3 selections
    assert set(historique["bookmaker"]) == {"Book A", "Book B"}


def test_dernieres_cotes_avant(tmp_path: Path) -> None:
    base = tmp_path / "hist.db"
    tableau = cotes.normaliser([EVENEMENT], "F1")
    tableau["horodatage"] = "2026-09-01T10:00:00"
    cotes.historiser(tableau, base)
    tardif = tableau.copy()
    tardif["horodatage"] = "2026-09-01T18:45:00"
    tardif["cote"] = tardif["cote"] + 0.10
    cotes.historiser(tardif, base)

    avant = cotes.dernieres_cotes_avant("Paris Saint-Germain", "Marseille",
                                        "2026-09-01", "2026-09-01T12:00:00", chemin_db=base)
    assert set(avant["horodatage"]) == {"2026-09-01T10:00:00"}
    apres = cotes.dernieres_cotes_avant("Paris Saint-Germain", "Marseille",
                                        "2026-09-01", "2026-09-01T23:00:00", chemin_db=base)
    assert set(apres["horodatage"]) == {"2026-09-01T18:45:00"}


def test_historiser_vide(tmp_path: Path) -> None:
    assert cotes.historiser(pd.DataFrame(), tmp_path / "vide.db") == 0
