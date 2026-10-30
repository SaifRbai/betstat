"""Tests de data/resultats.py. Aucun acces reseau : tout part de fixtures locales."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data import resultats

CSV_FACTICE = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,AvgH,AvgD,AvgA,MaxH,MaxD,MaxA,Avg>2.5,Avg<2.5,B365>2.5,B365<2.5
F1,09/08/2019,19:45,Monaco,Lyon,0,3,A,0,2,A,7,13,1,3,10,17,2,5,2,2,1,0,3,3.3,2.37,2.95,3.35,2.4,3.1,3.4,2.5,1.9,1.9,1.85,1.95
F1,10/08/19,17:00,Marseille,Reims,0,2,A,0,1,A,15,8,4,3,12,9,7,2,1,3,0,0,1.75,3.6,4.8,1.8,3.5,4.6,1.9,3.7,5.0,1.75,2.05,1.7,2.1
F1,11/08/2019,15:00,Nice,Amiens,2,1,H,1,0,H,14,9,6,2,11,13,6,3,2,1,0,0,1.9,3.5,4.2,1.95,3.45,4.0,2.0,3.6,4.3,1.95,1.85,1.9,1.9
F1,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
"""


@pytest.fixture()
def fichier_csv(tmp_path: Path) -> Path:
    chemin = tmp_path / "F1_1920.csv"
    chemin.write_text(CSV_FACTICE, encoding="utf-8")
    return chemin


def test_saison_courante() -> None:
    assert resultats.saison_courante(date(2025, 8, 15)) == "2526"
    assert resultats.saison_courante(date(2026, 3, 1)) == "2526"
    assert resultats.saison_courante(date(2026, 7, 1)) == "2627"


def test_dernieres_saisons_ordre_chronologique() -> None:
    codes = resultats.dernieres_saisons(3, date(2025, 9, 1))
    assert codes == ["2324", "2425", "2526"]


def test_dernieres_saisons_minimum_une() -> None:
    assert len(resultats.dernieres_saisons(0, date(2025, 9, 1))) == 1


def test_dates_deux_formats_melanges(fichier_csv: Path) -> None:
    """%d/%m/%Y et %d/%m/%y peuvent cohabiter dans un meme fichier."""
    brut = resultats.lire_csv(fichier_csv)
    dates = resultats.analyser_dates(brut["Date"])
    assert dates.iloc[0] == pd.Timestamp("2019-08-09")
    assert dates.iloc[1] == pd.Timestamp("2019-08-10")  # format court
    assert pd.isna(dates.iloc[3])  # ligne vide de fin de fichier


def test_nettoyage_colonnes_et_lignes(fichier_csv: Path) -> None:
    propre = resultats.nettoyer(resultats.lire_csv(fichier_csv), "1920", "F1")
    assert list(propre.columns) == list(resultats.COLONNES_NORMALISEES)
    assert len(propre) == 3  # la ligne vide est ecartee
    assert propre["total_buts"].tolist() == [3, 2, 3]
    assert propre["dom"].tolist() == ["Monaco", "Marseille", "Nice"]
    assert propre["saison"].unique().tolist() == ["1920"]


def test_cotes_moyennes_et_max(fichier_csv: Path) -> None:
    propre = resultats.nettoyer(resultats.lire_csv(fichier_csv), "1920", "F1")
    premiere = propre.iloc[0]
    assert premiere["cote_1"] == pytest.approx(2.95)     # AvgH prioritaire
    assert premiere["cote_1_max"] == pytest.approx(3.1)  # MaxH prioritaire
    assert premiere["cote_plus25"] == pytest.approx(1.9)


def test_csv_sans_colonnes_attendues() -> None:
    bidon = pd.DataFrame({"n_importe_quoi": [1, 2]})
    propre = resultats.nettoyer(bidon, "1920", "F1")
    assert propre.empty
    assert list(propre.columns) == list(resultats.COLONNES_NORMALISEES)


def test_championnat_inconnu_message_clair() -> None:
    with pytest.raises(ValueError, match="Championnat inconnu"):
        resultats.charger("XX", 1)


def test_charger_sans_reseau_ni_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reseau coupe et cache vide : DataFrame vide, pas d'exception."""
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)

    def pas_de_reseau(*_args: object, **_kwargs: object) -> None:
        raise OSError("reseau injoignable")

    monkeypatch.setattr(resultats.requests, "get", pas_de_reseau)
    df = resultats.charger("F1", 2)
    assert df.empty
    assert list(df.columns) == list(resultats.COLONNES_NORMALISEES)


def test_repli_sur_cache_quand_reseau_coupe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    saison = resultats.dernieres_saisons(1)[0]
    (tmp_path / f"F1_{saison}.csv").write_text(CSV_FACTICE, encoding="utf-8")

    def pas_de_reseau(*_args: object, **_kwargs: object) -> None:
        raise OSError("reseau injoignable")

    monkeypatch.setattr(resultats.requests, "get", pas_de_reseau)
    df = resultats.charger("F1", 1)
    assert len(df) == 3


def test_equipes_triees(fichier_csv: Path) -> None:
    propre = resultats.nettoyer(resultats.lire_csv(fichier_csv), "1920", "F1")
    assert resultats.equipes(propre) == ["Amiens", "Lyon", "Marseille", "Monaco", "Nice", "Reims"]


# --------------------------------------------------------------------------
# Page HTML deguisee en CSV (vu en vrai le 20/08/2026 sur la saison 2627)
# --------------------------------------------------------------------------

PAGE_ERREUR = (
    b'<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" '
    b'"http://www.w3.org/TR/html4/strict.dtd">\n<html><head>\n'
    b'<title>300 Multiple Choices</title>\n</head><body>\n'
    b'<h1>Multiple Choices</h1>\nThe document name you requested '
    b'(<code>/mmz4281/2627/F1.csv</code>) could not be found on this server.\n'
    b'However, we found documents with similar names, one, two, three.\n'
    b'</body></html>\n')

CSV_VALIDE = (b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
              + b"F1,15/08/2026,19:00,Paris SG,Marseille,2,1,H\n" * 10)


def test_une_page_html_n_est_pas_un_csv():
    """Elle contient des virgules et arrive en HTTP 300 : le seul indice est <html>."""
    assert resultats._ressemble_a_du_csv(PAGE_ERREUR) is False
    assert resultats._ressemble_a_du_csv(CSV_VALIDE) is True
    assert resultats._ressemble_a_du_csv(b"Div,Date\n") is False


def test_un_cache_empoisonne_est_ignore(tmp_path, monkeypatch):
    """Un cache deja pollue doit se purger tout seul, pas rester a vie."""
    chemin = tmp_path / "F1_2627.csv"
    chemin.write_bytes(PAGE_ERREUR)
    monkeypatch.setattr(resultats, "saison_courante", lambda: "2526")
    assert resultats._cache_valide(chemin, "2627") is False
    chemin.write_bytes(CSV_VALIDE)
    assert resultats._cache_valide(chemin, "2627") is True


def test_compter_les_matchs_par_equipe():
    """Un promu avec deux matchs ne doit pas passer pour une equipe bien connue."""
    matchs = pd.DataFrame({
        "dom": ["Ajax", "Cambuur", "Ajax"], "ext": ["Cambuur", "Ajax", "PSV"]})
    comptes = resultats.nb_matchs_par_equipe(matchs)
    assert comptes == {"Ajax": 3, "Cambuur": 2, "PSV": 1}
    assert resultats.nb_matchs_par_equipe(pd.DataFrame()) == {}


# --------------------------------------------------------------------------
# Saison pas encore publiee : cas normal en aout, pas une panne
# --------------------------------------------------------------------------

class _Reponse:
    def __init__(self, contenu: bytes) -> None:
        self.content = contenu

    def raise_for_status(self) -> None:
        pass


def test_une_saison_non_publiee_ne_crie_pas_a_la_panne(tmp_path, monkeypatch, caplog):
    """football-data repond « 300 Multiple Choices » avant la publication."""
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    appels = []
    monkeypatch.setattr(resultats.requests, "get",
                        lambda *a, **k: appels.append(1) or _Reponse(PAGE_ERREUR))

    with caplog.at_level("WARNING"):
        assert resultats.telecharger("F1", "2627") is None
    assert caplog.records == []                       # aucun avertissement
    assert (tmp_path / "F1_2627.absente").exists()

    # deuxieme appel : on ne redemande pas un fichier dont on sait qu'il manque
    assert resultats.telecharger("F1", "2627") is None
    assert len(appels) == 1


def test_une_vraie_panne_reseau_avertit_toujours(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)

    def coupure(*_a, **_k):
        raise resultats.requests.ConnectionError("pas de reseau")

    monkeypatch.setattr(resultats.requests, "get", coupure)
    with caplog.at_level("WARNING"):
        assert resultats.telecharger("F1", "2627") is None
    assert any("Verifie ta connexion" in r.message for r in caplog.records)
    assert not (tmp_path / "F1_2627.absente").exists()   # on reessaiera


def test_la_publication_efface_le_marqueur(tmp_path, monkeypatch):
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    marqueur = tmp_path / "F1_2627.absente"
    marqueur.write_text("")
    monkeypatch.setattr(resultats.requests, "get", lambda *a, **k: _Reponse(CSV_VALIDE))
    chemin = resultats.telecharger("F1", "2627", forcer=True)
    assert chemin is not None and chemin.exists()
    assert not marqueur.exists()


def test_un_faux_csv_est_retire_du_cache(tmp_path, monkeypatch):
    """Une page HTML enregistree sous un nom .csv ne redeviendra jamais valide."""
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    poison = tmp_path / "F1_2627.csv"
    poison.write_bytes(PAGE_ERREUR)
    monkeypatch.setattr(resultats.requests, "get", lambda *a, **k: _Reponse(PAGE_ERREUR))
    assert resultats.telecharger("F1", "2627") is None
    assert not poison.exists()


# --------------------------------------------------------------------------
# Championnats sur l'annee civile : un fichier unique "new/BRA.csv"
# --------------------------------------------------------------------------

CSV_ANNEE_CIVILE = (
    "﻿Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,"
    "MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,BFECH,BFECD,BFECA,B365CH,B365CD,B365CA\n"
    "Brazil,Serie A,2023,15/04/2023,20:00,Flamengo RJ,Coritiba,3,0,H,"
    "1.3,5.5,11,1.35,5.8,12,1.31,5.2,10,,,,1.3,5.25,10\n"
    "Brazil,Serie A,2025,30/03/2025,20:00,Palmeiras,Botafogo RJ,0,0,D,"
    "1.9,3.4,4.5,1.95,3.5,4.6,1.88,3.3,4.3,,,,1.9,3.4,4.33\n"
    "Brazil,Serie A,2026,18/10/2026,22:30,Bahia,Flamengo RJ,0,3,A,"
    ",,,4.2,3.8,2,3.69,3.71,1.88,4,4,1.97,4.2,3.6,1.86\n"
)


def test_dernieres_annees_civiles() -> None:
    assert resultats.dernieres_annees(3, date(2026, 10, 26)) == ["2024", "2025", "2026"]
    assert resultats.dernieres_annees(0, date(2026, 1, 5)) == ["2026"]


def test_le_bresil_se_charge_depuis_le_fichier_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un seul fichier, decoupe par sa colonne Season, colonnes ramenees au format mmz."""
    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    (tmp_path / "BRA_tout.csv").write_text(CSV_ANNEE_CIVILE, encoding="utf-8")
    monkeypatch.setattr(resultats.requests, "get", lambda *a, **k: pytest.fail("reseau"))

    df = resultats.charger("BRA", 2, aujourdhui=date(2026, 10, 26))
    assert df["saison"].tolist() == ["2025", "2026"]          # 2023 hors fenetre
    assert df["dom"].tolist() == ["Palmeiras", "Bahia"]
    assert df["buts_ext"].tolist() == [0, 3]
    assert df["resultat"].tolist() == ["D", "A"]
    # Seules les cotes de cloture existent : elles servent de dernier recours.
    assert df["cote_1"].tolist() == [1.88, 3.69]
    assert df["cote_2_max"].tolist() == [4.6, 2.0]


def test_le_fichier_unique_perime_comme_la_saison_en_cours(tmp_path):
    """Il contient la saison en cours : il ne doit pas etre fige pour toujours."""
    chemin = tmp_path / "BRA_tout.csv"
    chemin.write_text(CSV_ANNEE_CIVILE, encoding="utf-8")
    assert resultats._cache_valide(chemin, resultats.FICHIER_UNIQUE) is True
    vieux = chemin.stat().st_mtime - resultats.config.TTL_SAISON_COURANTE_SECONDES - 60
    os.utime(chemin, (vieux, vieux))
    assert resultats._cache_valide(chemin, resultats.FICHIER_UNIQUE) is False


# --------------------------------------------------------------------------
# Serie B bresilienne : API-Football converti au format football-data
# --------------------------------------------------------------------------

def _match_api_football(jour: str, dom: str, ext: str, buts: tuple, statut: str = "FT") -> dict:
    return {
        "fixture": {"date": f"{jour}T22:00:00+00:00", "status": {"short": statut}},
        "teams": {"home": {"name": dom}, "away": {"name": ext}},
        "score": {"fulltime": {"home": buts[0], "away": buts[1]},
                  "halftime": {"home": 0, "away": 0}},
    }


def test_la_serie_b_vient_d_api_football(tmp_path, monkeypatch) -> None:
    """Matchs termines seulement, et noms ramenes a l'orthographe football-data."""
    from data import absences

    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    monkeypatch.setattr(resultats.config, "DOSSIER_DONNEES", tmp_path)
    matchs = [
        _match_api_football("2024-04-19", "Botafogo SP", "America Mineiro", (1, 1)),
        _match_api_football("2024-04-20", "Novorizontino", "Atletico Goianiense", (2, 0)),
        _match_api_football("2024-11-24", "Avai", "CRB", (None, None), statut="NS"),
    ] * 4                                       # au-dela des 200 octets d'un vrai CSV
    appels = []

    def faux_appel(chemin: str, parametres: dict):
        appels.append((chemin, parametres))
        return matchs, 90

    monkeypatch.setattr(absences, "_appeler", faux_appel)
    df = resultats.charger("BRB", 1, aujourdhui=date(2024, 12, 31))

    assert appels == [("fixtures", {"league": 72, "season": "2024"})]
    assert df["saison"].unique().tolist() == ["2024"]
    assert set(df["dom"]) == {"Botafogo-SP", "Novorizontino"}       # "NS" ignore
    assert set(df["ext"]) == {"America MG", "Atletico GO"}           # comme en Serie A
    assert df["resultat"].tolist()[:2] == ["D", "H"]
    assert absences.lire_compteur()["appels"] == 1


def test_une_saison_hors_abonnement_ne_casse_rien(tmp_path, monkeypatch) -> None:
    """Le plan gratuit refuse 2025 : un historique vide, et pas de nouvel appel avant 6 h."""
    from data import absences

    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    monkeypatch.setattr(resultats.config, "DOSSIER_DONNEES", tmp_path)
    appels = []

    def refus(chemin: str, parametres: dict):
        appels.append(parametres)
        raise absences.HorsFenetre("Free plans do not have access to this season")

    monkeypatch.setattr(absences, "_appeler", refus)
    assert resultats.charger("BRB", 1, aujourdhui=date(2025, 6, 1)).empty
    assert resultats.charger("BRB", 1, aujourdhui=date(2025, 6, 1)).empty
    assert len(appels) == 1


def test_un_historique_api_football_trop_ancien_n_est_pas_utilise(tmp_path, monkeypatch) -> None:
    """2023-2024 seulement en octobre 2026 : rien plutot que des pronos perimes."""
    from data import absences

    monkeypatch.setattr(resultats.config, "DOSSIER_CACHE_RESULTATS", tmp_path)
    monkeypatch.setattr(resultats.config, "DOSSIER_DONNEES", tmp_path)
    vieux = [_match_api_football("2024-04-19", "Botafogo SP", "Avai", (1, 0))] * 8

    def appel(chemin: str, parametres: dict):
        if parametres["season"] in ("2023", "2024"):
            return vieux, 90
        raise absences.HorsFenetre("Free plans do not have access to this season")

    monkeypatch.setattr(absences, "_appeler", appel)
    assert resultats.charger("BRB", 4, aujourdhui=date(2026, 10, 26)).empty
    assert not resultats.charger("BRB", 4, aujourdhui=date(2025, 3, 1)).empty   # 2024 = la precedente
