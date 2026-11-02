"""Tests de data/espn.py et des ligues sans cotes. Aucun acces reseau."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import config
from data import espn, resultats


def _evenement(jour: str, dom: str, ext: str, score: tuple | None,
               etat: str = "post", nom: str = "STATUS_FULL_TIME") -> dict:
    return {
        "date": jour,
        "status": {"type": {"name": nom, "state": etat, "completed": etat == "post"}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"displayName": dom},
             "score": None if score is None else str(score[0])},
            {"homeAway": "away", "team": {"displayName": ext},
             "score": None if score is None else str(score[1])},
        ]}],
    }


@pytest.fixture()
def faux_espn(monkeypatch: pytest.MonkeyPatch):
    """Remplace l'appel ESPN par des evenements fournis par le test."""
    par_annee: dict[int, list[dict]] = {}
    appels: list[tuple[str, int]] = []

    def appeler(identifiant: str, annee: int) -> list[dict]:
        appels.append((identifiant, annee))
        return par_annee.get(annee, [])

    monkeypatch.setattr(espn, "_appeler", appeler)
    for ligue in ("COL", "KSA"):
        for annee in range(2020, 2030):
            espn._chemin_annee(ligue, annee).unlink(missing_ok=True)
    return par_annee, appels


def test_les_ligues_espn_sont_declarees() -> None:
    assert espn.slug("COL") == "col.1"
    assert espn.slug("ksa") == "ksa.1"
    assert espn.slug("F1") is None
    assert config.sans_cotes("COL") and not config.sans_cotes("KSA")
    assert "COL" in config.COMPETITIONS_AFFICHEES
    assert "COL" not in config.COMPETITIONS_AVEC_COTES


def test_bornes_d_une_saison() -> None:
    assert espn._bornes_saison("COL", "2026") == (date(2026, 1, 1), date(2027, 1, 1))
    assert espn._bornes_saison("KSA", "2526") == (date(2025, 7, 1), date(2026, 7, 1))


def test_seuls_les_matchs_termines_entrent_dans_l_historique(faux_espn) -> None:
    par_annee, _ = faux_espn
    par_annee[2025] = [
        _evenement("2025-03-01T23:00Z", "Millonarios", "Santa Fe", (2, 1)),
        _evenement("2025-03-02T01:00Z", "Junior", "Nacional", (0, 0)),
        _evenement("2025-03-03T01:00Z", "Cali", "America", None, "pre", "STATUS_SCHEDULED"),
    ]
    contenu = espn.csv_saison("COL", "2025")
    tableau = pd.read_csv(pd.io.common.BytesIO(contenu))
    assert list(tableau["HomeTeam"]) == ["Millonarios", "Junior"]
    assert list(tableau["FTR"]) == ["H", "D"]


def test_une_saison_a_cheval_couvre_deux_annees(faux_espn) -> None:
    par_annee, appels = faux_espn
    par_annee[2025] = [_evenement("2025-05-01T18:00Z", "Al Hilal", "Al Nassr", (1, 0)),
                       _evenement("2025-09-01T18:00Z", "Al Ahli", "Al Shabab", (3, 1))]
    par_annee[2026] = [_evenement("2026-02-01T18:00Z", "Al Nassr", "Al Ahli", (2, 2))]
    contenu = espn.csv_saison("KSA", "2526")
    tableau = pd.read_csv(pd.io.common.BytesIO(contenu))
    # Le match de mai 2025 appartient a la saison precedente.
    assert list(tableau["HomeTeam"]) == ["Al Ahli", "Al Nassr"]
    assert {a for _, a in appels} == {2025, 2026}


def test_une_saison_vide_n_est_pas_une_panne(faux_espn) -> None:
    assert espn.csv_saison("COL", "2024") == b""
    assert resultats.telecharger("COL", "2024", forcer=True) is None


def test_le_cache_sert_quand_espn_est_injoignable(faux_espn, monkeypatch) -> None:
    par_annee, _ = faux_espn
    annee = date.today().year
    par_annee[annee] = [_evenement(f"{annee}-01-10T20:00Z", "A", "B", (1, 0))]
    assert len(espn.evenements("COL", annee)) == 1

    def coupe(*_a, **_k):
        raise ConnectionError("wifi coupe")

    monkeypatch.setattr(espn, "_appeler", coupe)
    assert len(espn.evenements("COL", annee, ttl=0)) == 1


def test_le_calendrier_garde_les_matchs_du_jour_et_ecarte_les_reports(faux_espn) -> None:
    par_annee, _ = faux_espn
    par_annee[2026] = [
        _evenement("2026-11-01T20:00Z", "Vieux", "Match", (1, 1)),
        _evenement("2026-11-02T20:00Z", "Millonarios", "Santa Fe", (1, 0), "in",
                   "STATUS_SECOND_HALF"),
        _evenement("2026-11-03T01:00Z", "Junior", "Nacional", None, "pre",
                   "STATUS_SCHEDULED"),
        _evenement("2026-11-04T01:00Z", "Cali", "America", None, "pre",
                   "STATUS_POSTPONED"),
    ]
    tableau = espn.calendrier("COL", datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert list(tableau["dom"]) == ["Millonarios", "Junior"]
    assert list(tableau["nb_bookmakers"]) == [0, 0]
    assert tableau.iloc[0]["buts_dom"] == 1 and tableau.iloc[0]["statut"] == "in"
    assert pd.isna(tableau.iloc[1]["buts_dom"])


def test_le_calendrier_ne_leve_jamais(monkeypatch) -> None:
    def coupe(*_a, **_k):
        raise ConnectionError("wifi coupe")

    monkeypatch.setattr(espn, "_appeler", coupe)
    monkeypatch.setattr(espn, "_chemin_annee",
                        lambda l, a: config.DOSSIER_CACHE_RESULTATS / f"absent_{a}.json")
    assert espn.calendrier("COL").empty


def test_les_ligues_sans_cotes_n_ont_ni_valeur_ni_credit(monkeypatch) -> None:
    import web

    monkeypatch.setattr(web.CACHE, "modele", lambda c: (None, object()))
    monkeypatch.setattr(web.cotes_module, "recuperer",
                        lambda *a, **k: pytest.fail("aucun credit pour une ligue sans cotes"))
    monkeypatch.setattr(web.espn_module, "evenements", lambda *a, **k: [])
    assert web.opportunites("COL", 1000.0, forcer=True) == ([], None)
