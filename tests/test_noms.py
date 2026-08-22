"""Tests de la table de correspondance des noms d'equipes (T03)."""

from __future__ import annotations

import pytest

from data import noms


def test_normalisation_accents_et_ponctuation() -> None:
    assert noms.normaliser("Saint-Étienne") == "saint etienne"
    assert noms.normaliser("Nott'm Forest") == "nott m forest"
    assert noms.normaliser("  Paris   SG ") == "paris sg"


def test_noyau_retire_mots_generiques() -> None:
    assert noms.noyau("AS Monaco") == "monaco"
    assert noms.noyau("Olympique Lyonnais") == "lyonnais"
    # Si tout est generique on garde le nom complet plutot que rien.
    assert noms.noyau("FC") == "fc"


def test_canoniser_alias_par_source() -> None:
    assert noms.canoniser("Paris SG", "football_data") == "Paris Saint-Germain"
    assert noms.canoniser("PSG", "fbref") == "Paris Saint-Germain"
    assert noms.canoniser("Paris Saint Germain", "understat") == "Paris Saint-Germain"
    assert noms.canoniser("Man United", "football_data") == "Manchester United"
    assert noms.canoniser("Wolves", "football_data") == "Wolverhampton Wanderers"


def test_canoniser_insensible_casse_et_accents() -> None:
    assert noms.canoniser("st etienne", "football_data") == "Saint-Etienne"
    assert noms.canoniser("Saint-Étienne", "fbref") == "Saint-Etienne"


def test_canoniser_inconnu_renvoie_none() -> None:
    assert noms.canoniser("Racing Club de Nulle Part", "football_data") is None


def test_canoniser_identite_si_inconnu() -> None:
    assert noms.canoniser("Equipe X", "football_data", identite_si_inconnu=True) == "Equipe X"


def test_jamais_d_appariement_silencieux_sur_similarite() -> None:
    """Deux noms proches mais inconnus ne doivent JAMAIS etre apparies seuls."""
    resultat = noms.rapprocher(["Manchester Rovers"], ["Manchester Wanderers"])
    assert resultat["surs"] == []
    assert len(resultat["a_verifier"]) == 1
    nom_a, nom_b, score = resultat["a_verifier"][0]
    assert (nom_a, nom_b) == ("Manchester Rovers", "Manchester Wanderers")
    assert 0 < score < 1


def test_rapprocher_appariements_surs() -> None:
    resultat = noms.rapprocher(
        ["Paris SG", "Man United"], ["PSG", "Manchester Utd"], "football_data", "fbref"
    )
    canoniques = {c for _, _, c in resultat["surs"]}
    assert canoniques == {"Paris Saint-Germain", "Manchester United"}
    assert resultat["a_verifier"] == []


def test_rapprocher_orphelins() -> None:
    resultat = noms.rapprocher(["Paris SG", "Club Zzz"], ["PSG"], "football_data", "fbref")
    assert resultat["orphelins_a"] == ["Club Zzz"]
    assert resultat["orphelins_b"] == []


def test_suggerer_trois_plus_proches() -> None:
    propositions = noms.suggerer("Manchester Utd", noms.noms_canoniques(), 3)
    assert propositions[0][0] == "Manchester United"
    assert len(propositions) == 3


def test_taux_appariement_ligue_1() -> None:
    equipes = ["Paris SG", "Lyon", "Marseille", "St Etienne", "Le Havre"]
    assert noms.taux_appariement(equipes, "football_data") == 1.0


def test_ajouter_alias_sans_ecriture(monkeypatch: pytest.MonkeyPatch) -> None:
    noms.charger_table(forcer=True)
    noms.ajouter_alias("Lyon", "odds_api", "Olympique Lyonnais Test", ecrire=False)
    assert noms.canoniser("Olympique Lyonnais Test", "odds_api") == "Lyon"
    noms.charger_table(forcer=True)  # rechargement depuis le disque : alias oublie
    assert noms.canoniser("Olympique Lyonnais Test", "odds_api") is None


# --------------------------------------------------------------------------
# Non-regression : les pieges reels rencontres en remplissant la table
# --------------------------------------------------------------------------

def test_ath_madrid_n_est_pas_real_madrid() -> None:
    """Piege classique : "Ath Madrid" ressemble a "Real Madrid" pour un algorithme.

    Le rapprochement automatique proposait bien ce couple (similarite 0.59).
    S'il avait ete accepte en silence, tout le modele espagnol aurait ete faux.
    """
    assert noms.canoniser("Ath Madrid", "football_data") == "Atletico Madrid"
    assert noms.canoniser("Real Madrid", "football_data") == "Real Madrid"
    assert noms.canoniser("Ath Bilbao", "football_data") == "Athletic Club"
    assert noms.canoniser("Ath Madrid", "football_data") != noms.canoniser(
        "Real Madrid", "football_data")


def test_alias_des_cinq_grands_championnats() -> None:
    """Les correspondances validees a la main doivent rester stables."""
    attendus = {
        ("Espanol", "football_data"): "Espanyol",
        ("M'gladbach", "football_data"): "Borussia Monchengladbach",
        ("FC Koln", "football_data"): "FC Cologne",
        ("Milan", "football_data"): "AC Milan",
        ("Hellas Verona", "fbref"): "Verona",
        ("RasenBallsport Leipzig", "understat"): "RB Leipzig",
        ("Parma Calcio 1913", "understat"): "Parma",
        ("Dep. A Coruna", "football_data"): "Deportivo La Coruna",
        ("Le Mans FC", "odds_api"): "Le Mans",
    }
    for (nom, source), canonique in attendus.items():
        assert noms.canoniser(nom, source) == canonique, f"{nom} ({source})"


def test_couverture_des_cinq_grands_championnats() -> None:
    """Assez d'equipes en table pour couvrir les 5 championnats a xG."""
    assert len(noms.noms_canoniques()) >= 120


# --------------------------------------------------------------------------
# resoudre_vers : mapper un nom d'une source sur les equipes d'un modele
# --------------------------------------------------------------------------

CANDIDATS = ["Utrecht", "Cambuur", "Den Haag", "Nijmegen", "Feyenoord", "Ajax"]


def test_resoudre_egalite_exacte() -> None:
    assert noms.resoudre_vers("Feyenoord", CANDIDATS) == "Feyenoord"


def test_resoudre_par_la_table() -> None:
    """La table sait que le "ADO Den Haag" des books est le "Den Haag" des CSV."""
    assert noms.resoudre_vers("ADO Den Haag", CANDIDATS) == "Den Haag"
    assert noms.resoudre_vers("NEC Nijmegen", CANDIDATS) == "Nijmegen"


def test_resoudre_par_retrait_des_mots_generiques() -> None:
    """"FC Utrecht" et "Utrecht" se ramenent au meme noyau : c'est deterministe."""
    assert noms.resoudre_vers("FC Utrecht", CANDIDATS) == "Utrecht"
    assert noms.resoudre_vers("SC Cambuur", CANDIDATS) == "Cambuur"


def test_resoudre_refuse_l_ambiguite() -> None:
    """Deux candidats se ramenant a la meme forme : on ne tranche pas."""
    assert noms.resoudre_vers("FC Ajax", ["Ajax", "AFC Ajax"]) is None


def test_resoudre_ne_devine_jamais_sur_une_ressemblance() -> None:
    """Aucun score de similarite n'intervient : un nom different reste None.

    "For Dusseldorf" ~ "Fortuna Dusseldorf" a une similarite elevee et
    designe bien le meme club — mais tant que la table ne le dit pas
    explicitement, on refuse de le decider a la place de l'humain.
    """
    assert noms.resoudre_vers("Fortuna Dusseldorf", ["For Dusseldorf"]) is None
    assert noms.resoudre_vers("Manchester Rovers", ["Manchester United"]) is None
    assert noms.resoudre_vers("Real Madrid", ["Ath Madrid"]) is None


def test_resoudre_ne_confond_pas_les_deux_madrid() -> None:
    """Le piege qui aurait tout casse, verifie aussi au niveau de la resolution."""
    assert noms.resoudre_vers("Atletico Madrid", ["Real Madrid", "Ath Madrid"]) == "Ath Madrid"
    assert noms.resoudre_vers("Real Madrid", ["Real Madrid", "Ath Madrid"]) == "Real Madrid"


def test_resoudre_inconnu() -> None:
    assert noms.resoudre_vers("Club Inexistant", CANDIDATS) is None
    assert noms.resoudre_vers("Feyenoord", []) is None

# --------------------------------------------------------------------------
# Alias du flux de cotes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cote,football_data", [
    ("Coventry City", "Coventry"), ("West Bromwich Albion", "West Brom"),
    ("Queens Park Rangers", "QPR"), ("Peterborough United", "Peterboro"),
    ("Athletic Bilbao", "Ath Bilbao"), ("Sporting Gijon", "Sp Gijon"),
    ("USL Dunkerque", "Dunkerque"), ("Stade Lavallois", "Laval"),
    ("Standard Liege", "Standard"), ("Erzurum BB", "Erzurumspor"),
    ("Basaksehir", "Buyuksehyr"), ("FC Schalke 04", "Schalke 04"),
    ("Olympiakos Piraeus", "Olympiakos"), ("Sporting Lisbon", "Sp Lisbon"),
])
def test_le_flux_de_cotes_est_rattache_a_l_historique(cote, football_data):
    """Sans ces alias, le match est ecarte et disparait de la page."""
    canonique = noms.canoniser(cote, "odds_api", identite_si_inconnu=True)
    assert noms.resoudre_vers(canonique, [football_data]) == football_data


@pytest.mark.parametrize("cote,piege", [
    ("Inter Milan", "Milan"),        # l'Inter n'est pas l'AC Milan
    ("Le Mans FC", "Lens"),          # deux clubs sans rapport
    ("Hellas Verona", "Venezia"),
    ("Celta Fortuna", "La Coruna"),
])
def test_les_ressemblances_de_chaine_ne_font_pas_une_identite(cote, piege):
    """difflib propose ces rapprochements ; ils sont faux, et une fausse
    correspondance salit toutes les estimations du club concerne."""
    canonique = noms.canoniser(cote, "odds_api", identite_si_inconnu=True)
    assert noms.resoudre_vers(canonique, [piege]) is None
