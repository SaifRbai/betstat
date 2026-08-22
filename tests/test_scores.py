"""Tests des scores du jour meme (API-Football).

Aucun appel reseau : les reponses sont des bouchons. Ce qui est verifie ici est
la selection — quelles rencontres on retient, et surtout lesquelles on refuse.
"""

from __future__ import annotations

import pytest

from data import scores


def _rencontre(dom: str, ext: str, buts_dom, buts_ext, statut: str = "FT") -> dict:
    return {
        "fixture": {"status": {"short": statut}},
        "teams": {"home": {"name": dom}, "away": {"name": ext}},
        "goals": {"home": buts_dom, "away": buts_ext},
    }


@pytest.fixture()
def sans_reseau(monkeypatch):
    def poser(rencontres):
        monkeypatch.setattr(scores, "recuperer_brut",
                            lambda jour, forcer=False: (rencontres, True))
    return poser


def test_une_rencontre_terminee_est_retenue(sans_reseau):
    sans_reseau([_rencontre("Marseille", "Strasbourg", 4, 0)])
    index = scores.du_jour("2026-08-21", ["Marseille", "Strasbourg"])
    assert index[("Marseille", "Strasbourg")]["buts_dom"] == 4.0
    assert index[("Marseille", "Strasbourg")]["source"] == "api_football"


@pytest.mark.parametrize("statut", ["1H", "2H", "HT", "NS", "PST", "ABD"])
def test_un_match_non_termine_est_ignore(sans_reseau, statut):
    """Juger un prono sur un score de mi-temps le jugerait sur rien."""
    sans_reseau([_rencontre("Marseille", "Strasbourg", 1, 0, statut)])
    assert scores.du_jour("2026-08-21", ["Marseille", "Strasbourg"]) == {}


@pytest.mark.parametrize("statut", ["FT", "AET", "PEN"])
def test_les_prolongations_et_tirs_au_but_comptent(sans_reseau, statut):
    sans_reseau([_rencontre("Marseille", "Strasbourg", 2, 2, statut)])
    assert scores.du_jour("2026-08-21", ["Marseille", "Strasbourg"])


def test_une_equipe_non_resolue_fait_ecarter_la_rencontre(sans_reseau):
    """Apparier a moitie reviendrait a deviner le match."""
    sans_reseau([_rencontre("Marseille", "Equipe Inconnue XYZ", 4, 0)])
    assert scores.du_jour("2026-08-21", ["Marseille", "Strasbourg"]) == {}


def test_un_score_absent_est_ignore(sans_reseau):
    sans_reseau([_rencontre("Marseille", "Strasbourg", None, None)])
    assert scores.du_jour("2026-08-21", ["Marseille", "Strasbourg"]) == {}


def test_les_alias_api_football_sont_appliques(sans_reseau):
    """« RED Star FC 93 » et « Erzurumspor FK » ne s'ecrivent pas comme dans
    l'historique : sans alias, ces matchs restent sans verdict."""
    sans_reseau([_rencontre("Boulogne", "RED Star FC 93", 1, 1),
                 _rencontre("Erzurumspor FK", "Galatasaray", 0, 4)])
    index = scores.du_jour("2026-08-21",
                           ["Boulogne", "Red Star", "Erzurumspor", "Galatasaray"])
    assert ("Boulogne", "Red Star") in index
    assert ("Erzurumspor", "Galatasaray") in index


def test_une_panne_reseau_ne_fait_pas_tomber_la_page(monkeypatch):
    """Sans score, la page affiche « pas encore publie », ce qui reste vrai."""
    def casse(jour, forcer=False):
        raise RuntimeError("reseau indisponible")

    monkeypatch.setattr(scores, "recuperer_brut", casse)
    assert scores.du_jour("2026-08-21", ["Marseille"]) == {}


def test_le_compteur_de_quota_est_celui_des_absences():
    """Meme cle, meme budget de cent appels par jour : deux compteurs separes
    laisseraient chacun croire qu'il lui en reste."""
    from data import absences
    assert hasattr(absences, "enregistrer_appel")
    assert scores.absences_module is absences

# --------------------------------------------------------------------------
# Corners et cartons : un appel de plus, par rencontre
# --------------------------------------------------------------------------

def _statistiques(corners_dom, jaunes_dom, corners_ext, jaunes_ext) -> list:
    def camp(corners, jaunes):
        return {"statistics": [{"type": "Corner Kicks", "value": corners},
                               {"type": "Yellow Cards", "value": jaunes},
                               {"type": "Total Shots", "value": 11}]}
    return [camp(corners_dom, jaunes_dom), camp(corners_ext, jaunes_ext)]


def test_les_comptes_completent_le_score(monkeypatch):
    monkeypatch.setattr(scores, "statistiques",
                        lambda fixture, forcer=False: _statistiques(3, 1, 4, 2))
    entree = scores.comptes({"buts_dom": 4.0, "buts_ext": 0.0, "fixture": 42},
                            "Marseille", "Strasbourg")
    assert entree["corners_dom"] == 3.0 and entree["corners_ext"] == 4.0
    assert entree["jaunes_dom"] == 1.0 and entree["jaunes_ext"] == 2.0
    assert entree["buts_dom"] == 4.0, "le score ne doit pas etre touche"


def test_un_compte_absent_ne_devient_jamais_zero(monkeypatch):
    """Un zero serait un chiffre, et il serait faux : le prono corners doit
    rester non verifiable, pas etre juge sur une valeur inventee."""
    monkeypatch.setattr(scores, "statistiques",
                        lambda fixture, forcer=False: [
                            {"statistics": [{"type": "Total Shots", "value": 9}]},
                            {"statistics": [{"type": "Total Shots", "value": 4}]}])
    entree = scores.comptes({"buts_dom": 1.0, "buts_ext": 0.0, "fixture": 42}, "A", "B")
    assert "corners_dom" not in entree and "jaunes_dom" not in entree


def test_sans_identifiant_de_rencontre_on_ne_demande_rien(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("appel API sans identifiant de rencontre")

    monkeypatch.setattr(scores, "statistiques", interdit)
    entree = {"buts_dom": 1.0, "buts_ext": 0.0}
    assert scores.comptes(entree, "A", "B") == entree


def test_un_quota_epuise_laisse_les_comptes_inconnus(monkeypatch):
    from data import absences

    def epuise(fixture, forcer=False):
        raise absences.QuotaEpuise("quota atteint")

    monkeypatch.setattr(scores, "statistiques", epuise)
    entree = scores.comptes({"buts_dom": 1.0, "buts_ext": 0.0, "fixture": 7}, "A", "B")
    assert "corners_dom" not in entree
    assert entree["buts_dom"] == 1.0, "le score reste, seuls les comptes manquent"


def test_le_score_porte_l_identifiant_pour_aller_chercher_les_comptes(sans_reseau):
    sans_reseau([{**_rencontre("Marseille", "Strasbourg", 4, 0),
                  "fixture": {"id": 1552733, "status": {"short": "FT"}}}])
    index = scores.du_jour("2026-08-21", ["Marseille", "Strasbourg"])
    assert index[("Marseille", "Strasbourg")]["fixture"] == 1552733
