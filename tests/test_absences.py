"""Tests de data/absences.py : fenetre du plan gratuit, dedoublonnage, quota."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import config
from data import absences


REPONSE = [
    {"team": {"name": "Rayo Vallecano"}, "league": {"name": "La Liga"},
     "fixture": {"id": 1}, "player": {"name": "M. Kumbulla", "type": "Missing Fixture",
                                      "reason": "Muscle Injury"}},
    # Doublon exact : l'API repete chaque joueur, on ne doit le voir qu'une fois.
    {"team": {"name": "Rayo Vallecano"}, "league": {"name": "La Liga"},
     "fixture": {"id": 1}, "player": {"name": "M. Kumbulla", "type": "Missing Fixture",
                                      "reason": "Muscle Injury"}},
    {"team": {"name": "Alaves"}, "league": {"name": "La Liga"},
     "fixture": {"id": 1}, "player": {"name": "F. Garces", "type": "Missing Fixture",
                                      "reason": "Suspended"}},
    {"team": {"name": "Alaves"}, "league": {"name": "La Liga"},
     "fixture": {"id": 1}, "player": {"name": None, "reason": "x"}},   # ignore
]


def test_dedoublonnage_et_suspensions() -> None:
    lignes = absences.normaliser(REPONSE)
    assert len(lignes) == 2                       # doublon retire, joueur vide ignore
    kumbulla = next(l for l in lignes if l["joueur"] == "M. Kumbulla")
    assert kumbulla["suspension"] is False
    garces = next(l for l in lignes if l["joueur"] == "F. Garces")
    assert garces["suspension"] is True


def test_reconnaissance_des_suspensions() -> None:
    assert absences.est_suspension("Suspended")
    assert absences.est_suspension("Red Card")
    assert not absences.est_suspension("Knee Injury")
    assert not absences.est_suspension("")


def test_les_noms_de_l_api_sont_resolus_vers_les_notres() -> None:
    """« Rayo Vallecano » cote API doit retrouver « Vallecano » cote modele."""
    lignes = absences.normaliser(REPONSE)
    par_equipe = absences.du_match(lignes, "Vallecano", "Alaves",
                                   ["Vallecano", "Alaves", "Betis"])
    assert [a["joueur"] for a in par_equipe["Vallecano"]] == ["M. Kumbulla"]
    assert [a["joueur"] for a in par_equipe["Alaves"]] == ["F. Garces"]


def test_une_equipe_hors_du_match_est_ignoree() -> None:
    lignes = absences.normaliser(REPONSE)
    par_equipe = absences.du_match(lignes, "Betis", "Sociedad", ["Betis", "Sociedad"])
    assert par_equipe == {"Betis": [], "Sociedad": []}


def test_la_fenetre_est_aujourd_hui_et_demain() -> None:
    aujourd_hui = date(2026, 8, 20)
    assert absences.jours_couverts(aujourd_hui) == ["2026-08-20", "2026-08-21"]
    assert absences.dans_la_fenetre("2026-08-21", aujourd_hui)
    assert not absences.dans_la_fenetre("2026-08-22", aujourd_hui)


def test_hors_fenetre_ne_consomme_aucun_appel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un match lointain ne doit pas gaspiller une requete pour rien."""
    def interdit(*_a, **_k):
        raise AssertionError("aucun appel ne doit partir hors fenetre")
    monkeypatch.setattr(absences, "_appeler", interdit)
    lointain = str(date.today() + timedelta(days=5))
    lignes, statut, explication = absences.etat_du_jour(lointain)
    assert lignes == []
    assert statut == absences.DISPONIBILITE_HORS_FENETRE
    # Le message doit distinguer « pas d'information » de « pas d'absent ».
    assert "ne veut pas dire que personne ne manque" in explication


def test_une_api_muette_ne_fait_pas_tomber_la_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def casse(*_a, **_k):
        raise OSError("reseau coupe")
    monkeypatch.setattr(absences, "recuperer", casse)
    lignes, statut, explication = absences.etat_du_jour(str(date.today()))
    assert lignes == []
    assert statut == absences.DISPONIBILITE_INDISPONIBLE
    assert "reste valable" in explication


def test_compteur_quotidien(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(absences, "_chemin_compteur", lambda: tmp_path / "quota.json")
    assert absences.lire_compteur()["appels"] == 0
    absences._incrementer_compteur(97)
    etat = absences.lire_compteur()
    assert etat["appels"] == 1 and etat["restant_api"] == 97
    assert "1 appel(s) aujourd'hui sur 100" in absences.message_quota(etat)


def test_compteur_remis_a_zero_le_lendemain(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    chemin = tmp_path / "quota.json"
    monkeypatch.setattr(absences, "_chemin_compteur", lambda: chemin)
    chemin.write_text(json.dumps({"jour": "2020-01-01", "appels": 100}), encoding="utf-8")
    assert absences.lire_compteur()["appels"] == 0
