"""Resultats de Ligue des champions et modele europeen, sans reseau."""

from __future__ import annotations

from datetime import date

import pandas as pd

import config
from data import europe
from modele import europe as modele_europe


def _match(dom, ext, plein, regulier=None, statut="FINISHED", duree="REGULAR"):
    score = {"duration": duree, "fullTime": {"home": plein[0], "away": plein[1]},
             "halfTime": {"home": 0, "away": 0}}
    if regulier:
        score["regularTime"] = {"home": regulier[0], "away": regulier[1]}
    return {"utcDate": "2026-10-21T19:00:00Z", "status": statut,
            "homeTeam": {"name": dom, "shortName": dom},
            "awayTeam": {"name": ext, "shortName": ext}, "score": score}


def test_le_score_retenu_est_celui_du_temps_reglementaire():
    donnees = {"matches": [
        _match("Arsenal FC", "FC Bayern München", (3, 2), regulier=(1, 1),
               duree="EXTRA_TIME"),
        _match("Arsenal FC", "Chelsea FC", (2, 0)),
    ]}
    sortie = europe.normaliser(donnees, 2026, ["Arsenal", "Bayern Munich", "Chelsea"])
    assert list(zip(sortie["buts_dom"], sortie["buts_ext"])) == [(1.0, 1.0), (2.0, 0.0)]
    assert list(sortie["resultat"]) == ["D", "H"]
    assert set(sortie["championnat"]) == {"UCL"}
    assert list(sortie["saison"]) == ["2627", "2627"]


def test_les_noms_sont_ramenes_a_ceux_des_modeles_sans_deviner():
    donnees = {"matches": [_match("FC Bayern München", "SK Slavia Praha", (2, 0))]}
    sortie = europe.normaliser(donnees, 2026, ["Bayern Munich", "Arsenal"])
    # Bayern par la table de correspondance ; Slavia, inconnu, garde son nom.
    assert (sortie.loc[0, "dom"], sortie.loc[0, "ext"]) == ("Bayern Munich", "SK Slavia Praha")


def test_un_match_non_termine_est_ignore():
    donnees = {"matches": [_match("Arsenal FC", "Chelsea FC", (None, None),
                                  statut="SCHEDULED")]}
    assert europe.normaliser(donnees, 2026, ["Arsenal", "Chelsea"]).empty


def test_la_saison_europeenne_commence_en_juillet():
    assert europe.annee_de_debut(date(2026, 11, 2)) == 2026
    assert europe.annee_de_debut(date(2027, 3, 1)) == 2026
    assert europe.saisons(3, date(2026, 11, 2)) == [2024, 2025, 2026]


def _championnat(code, equipes):
    lignes = [{"date": pd.Timestamp("2026-09-01"), "dom": a, "ext": b}
              for a in equipes for b in equipes if a != b]
    return pd.DataFrame(lignes).assign(championnat=code)


def test_un_championnat_est_relie_par_l_ensemble_de_ses_matchs_europeens():
    """Dix matchs au total suffisent, meme repartis sur deux adversaires."""
    charges = {"E0": _championnat("E0", ["Arsenal", "Chelsea"]),
               "SP1": _championnat("SP1", ["Real Madrid", "Sevilla"]),
               "B1": _championnat("B1", ["Club Brugge", "Antwerp"]),
               "RUS": _championnat("RUS", ["Zenit", "CSKA"])}
    europeens = pd.DataFrame(
        [("Arsenal", "Real Madrid")] * 10                  # E0 - SP1
        + [("Club Brugge", "Arsenal")] * 5                 # B1 - E0
        + [("Antwerp", "Sevilla")] * 5                     # B1 - SP1
        + [("Zenit", "Chelsea")] * 3,                      # RUS : trop peu
        columns=["dom", "ext"])
    retenus, ponts = modele_europe.composante_reliee(charges, europeens, "E0")
    assert retenus == ["E0", "SP1", "B1"]
    assert ponts == {"SP1": 10, "B1": 10}


def test_chaque_coupe_a_une_cle_de_cotes_et_un_modele_d_emprunt():
    for code, info in config.COUPES.items():
        assert info["odds"], code
        assert info["type"] in ("europe", "nationale"), code
        if info["type"] == "nationale":
            assert info["rattachement"] in config.CHAMPIONNATS, code
        assert config.est_coupe(code)
        assert config.nom_championnat(code) == info["nom"]


# -- Ligue Europa / Conference via ESPN -------------------------------------

def _evenement_espn(jour, dom, ext, bd, be, statut="STATUS_FULL_TIME"):
    return {"date": f"{jour}T20:00Z",
            "status": {"type": {"name": statut, "completed": True, "state": "post"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": str(bd), "team": {"displayName": dom}},
                {"homeAway": "away", "score": str(be), "team": {"displayName": ext}}]}]}


def test_coupes_espn_noms_resolus_et_prolongations_ecartees(monkeypatch) -> None:
    from datetime import date
    from data import espn as espn_module
    from data import europe as europe_data

    def faux(code, annee, ttl=None, identifiant=None):
        if code != "UEL" or annee != 2025:
            return []
        return [_evenement_espn("2025-10-02", "Olympiacos", "Benfica", 2, 1),
                _evenement_espn("2025-10-23", "Ferencvaros", "Benfica", 1, 1),
                _evenement_espn("2026-03-12", "Olympiacos", "Benfica", 1, 2,
                                statut="STATUS_FINAL_AET")]

    monkeypatch.setattr(espn_module, "evenements", faux)
    sortie = europe_data.charger_coupes_espn(
        1, ["Olympiakos", "Benfica"], aujourdhui=date(2025, 11, 1))
    assert len(sortie) == 2                          # la prolongation est ecartee
    assert set(sortie["dom"]) == {"Olympiakos", "Ferencvaros"}
    assert set(sortie["championnat"]) == {"UEL"}
