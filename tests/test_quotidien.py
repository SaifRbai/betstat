"""Tests de paris/quotidien.py : la selection du jour, et ses garde-fous."""

from __future__ import annotations

import pandas as pd
import pytest

import config
from paris import quotidien


def _cotes(p_favori_cotes: dict[str, list[float]], dom="PSV", ext="Groningen",
           date="2026-08-23T12:30:00Z") -> pd.DataFrame:
    """Un match 1N2 cote par plusieurs bookmakers."""
    lignes = []
    for i, (sel, valeurs) in enumerate(p_favori_cotes.items()):
        for j, cote in enumerate(valeurs):
            lignes.append({"date": date, "dom": dom, "ext": ext, "championnat": "N1",
                           "bookmaker": f"book{j}", "marche": "1N2",
                           "selection": sel, "cote": cote, "horodatage": "x"})
    return pd.DataFrame(lignes)


# Favori net (~72 %), un book paie nettement mieux que les autres.
FAVORI_NET = _cotes({"1": [1.34, 1.34, 1.34, 1.42],
                     "N": [5.0, 5.0, 5.0, 5.0],
                     "2": [8.0, 8.0, 8.0, 8.0]})


def test_un_favori_net_bien_paye_est_retenu() -> None:
    retenus = quotidien.candidats(FAVORI_NET, "N1")
    assert len(retenus) == 1
    s = retenus[0]
    assert s.selection == "1" and s.equipe == "PSV"
    assert s.p_marche > quotidien.SEUIL_PROBABILITE
    assert s.cote == 1.42                      # la meilleure, pas la mediane
    assert s.bookmaker == "book3"
    assert s.prime > quotidien.PRIME_MINIMALE


def test_un_favori_trop_faible_est_ecarte() -> None:
    """Sous le seuil, le ROI mesure retombe vers zero : on n'y va pas."""
    tiede = _cotes({"1": [1.90, 1.90, 1.90, 2.10], "N": [3.5]*4, "2": [4.0]*4})
    assert quotidien.candidats(tiede, "N1") == []


def test_un_favori_net_mais_mal_paye_est_ecarte() -> None:
    """Tout l'avantage recherche est dans le prix : sans prime, pas de pari."""
    aligne = _cotes({"1": [1.34]*4, "N": [5.0]*4, "2": [8.0]*4})
    assert quotidien.candidats(aligne, "N1") == []


def test_trop_peu_de_bookmakers() -> None:
    maigre = _cotes({"1": [1.34, 1.42], "N": [5.0, 5.0], "2": [8.0, 8.0]})
    assert len(maigre["bookmaker"].unique()) < config.MIN_BOOKMAKERS_CONSENSUS
    assert quotidien.candidats(maigre, "N1") == []


def test_le_tri_se_fait_sur_la_prime_pas_sur_l_esperance() -> None:
    autre = _cotes({"1": [1.34, 1.34, 1.34, 1.50], "N": [5.0]*4, "2": [8.0]*4},
                   dom="Feyenoord", ext="Cambuur")
    selections = quotidien.du_jour({"N1": FAVORI_NET, "SP1": autre})
    assert len(selections) == 2
    assert selections[0].prime > selections[1].prime
    assert selections[0].dom == "Feyenoord"


def test_aucune_cote_ne_plante_pas() -> None:
    assert quotidien.candidats(pd.DataFrame(), "N1") == []
    assert quotidien.du_jour({}) == []


def test_un_marche_sans_1n2_est_ignore() -> None:
    totaux = FAVORI_NET.copy()
    totaux["marche"] = "plus_moins_2.5"
    assert quotidien.candidats(totaux, "N1") == []


def test_la_mise_est_plate_et_plafonnee() -> None:
    assert quotidien.mise(1000.0) == 10.0
    # Le plafond de 2 % du projet s'applique aussi ici.
    assert quotidien.mise(1000.0, fraction=0.10) == 1000.0 * config.PLAFOND_MISE_BANKROLL


def test_l_avertissement_dit_que_l_intervalle_contient_zero() -> None:
    """Sans cette phrase, la regle se lirait comme un avantage demontre."""
    texte = quotidien.avertissement()
    assert "CONTIENT ZERO" in texte
    assert "pas une regle gagnante" in texte
    # Et il rappelle que suivre le modele est la seule regle prouvee perdante.
    assert "-3.4 %" in texte
    bas, haut = quotidien.ROI_IC
    assert bas < 0 < haut


# --------------------------------------------------------------------------
# Combine vers un objectif de gain
# --------------------------------------------------------------------------

def _favori(p: float, cote: float, nom: str) -> quotidien.Selection:
    return quotidien.Selection(
        date="2026-08-22", championnat="XX", dom=nom, ext="adversaire",
        selection="1", equipe=nom, p_marche=p, cote=cote, bookmaker="book",
        cote_mediane=cote, prime=0.0, nb_bookmakers=10, esperance=p * cote - 1)


FAVORIS = [_favori(0.82, 1.21, "A"), _favori(0.80, 1.24, "B"),
           _favori(0.77, 1.27, "C"), _favori(0.75, 1.32, "D"),
           _favori(0.72, 1.36, "E"), _favori(0.70, 1.40, "F"),
           _favori(0.68, 1.44, "G"), _favori(0.65, 1.50, "H")]


def test_le_combine_empile_les_plus_surs_d_abord() -> None:
    """Moins de jambes = moins d'occasions de tout perdre."""
    c = quotidien.combine_vers(FAVORIS, cible=3.0)
    assert c is not None
    assert [j.equipe for j in c.jambes] == ["A", "B", "C", "D", "E"][:len(c.jambes)]
    assert c.cote_totale >= 3.0


def test_la_probabilite_du_combine_est_le_produit_des_jambes() -> None:
    c = quotidien.combine_vers(FAVORIS, cible=2.0)
    attendu = 1.0
    for j in c.jambes:
        attendu *= j.p_marche
    assert c.probabilite == pytest.approx(attendu)
    assert c.une_fois_sur == pytest.approx(1 / attendu)


def test_la_marge_payee_est_positive_et_croit_avec_les_jambes() -> None:
    """Chaque jambe ajoute la marge du bookmaker, et elles se multiplient."""
    court = quotidien.combine_vers(FAVORIS, cible=1.6)
    long = quotidien.combine_vers(FAVORIS, cible=6.0)
    assert 0 < court.marge_payee < long.marge_payee
    assert long.esperance < court.esperance < 0


def test_le_gain_correspond_a_la_mise_demandee() -> None:
    c = quotidien.combine_vers(FAVORIS, cible=9.0, mise=10.0)
    assert c is not None
    assert c.gain == pytest.approx(10.0 * c.cote_totale)
    assert c.gain >= 90.0            # 10 EUR qui rapportent au moins 90


def test_l_annee_simulee_est_coherente() -> None:
    c = quotidien.combine_vers(FAVORIS, cible=9.0, mise=10.0)
    annee = c.sur_une_annee(365)
    assert annee["engage"] == pytest.approx(3650.0)
    assert annee["retour_attendu"] == pytest.approx(3650.0 * c.probabilite * c.cote_totale)
    assert annee["retour_attendu"] < annee["engage"]     # esperance negative
    assert annee["jours_gagnants"] == pytest.approx(365 * c.probabilite)


def test_un_objectif_inatteignable_renvoie_rien() -> None:
    assert quotidien.combine_vers(FAVORIS, cible=1000.0) is None
    assert quotidien.combine_vers([], cible=2.0) is None
    # Le plafond de jambes est respecte.
    assert quotidien.combine_vers(FAVORIS, cible=5.0, maximum_jambes=2) is None


def test_l_avertissement_porte_les_trois_faits_qui_comptent() -> None:
    c = quotidien.combine_vers(FAVORIS, cible=9.0, mise=10.0)
    texte = quotidien.avertissement_combine(c)
    assert "1 fois sur" in texte                   # la frequence reelle
    assert "marge payee" in texte                  # ce que prend le bookmaker
    assert "statistiquement etablie" in texte      # le pari simple est pire
    assert f"{quotidien.ROI_BANDE_LONGUE:+.1%}" in texte

# --------------------------------------------------------------------------
# Objectif hors d'atteinte
# --------------------------------------------------------------------------

def _selection(cote: float, p_marche: float, jour: str = "2026-08-21") -> quotidien.Selection:
    return quotidien.Selection(
        date=pd.Timestamp(f"{jour}T18:00:00Z"), championnat="F1",
        dom="A", ext="B", selection="1", equipe="A",
        p_marche=p_marche, cote=cote, bookmaker="b",
        cote_mediane=cote, prime=0.0, nb_bookmakers=8,
        esperance=p_marche * cote - 1.0)


def test_sans_le_drapeau_un_objectif_inatteignable_reste_un_refus() -> None:
    """Non-regression : le comportement historique ne change pas."""
    assert quotidien.combine_vers([_selection(1.25, 0.80)], cible=9.0) is None


def test_au_mieux_rend_le_maximum_possible_plutot_que_rien() -> None:
    """Une journee ordinaire n'offre que deux ou trois favoris. Repondre
    « impossible » ressemblerait a une panne ; le vrai renseignement est
    « voila tout ce qu'il y a, et ca ne suffit pas »."""
    combine = quotidien.combine_vers(
        [_selection(1.25, 0.80), _selection(1.58, 0.65)], cible=9.0, au_mieux=True)
    assert combine is not None
    assert combine.objectif_atteint is False
    assert len(combine.jambes) == 2
    assert combine.cote_totale == pytest.approx(1.25 * 1.58)


def test_un_objectif_atteint_reste_marque_comme_tel() -> None:
    combine = quotidien.combine_vers(
        [_selection(3.0, 0.34), _selection(3.5, 0.30)], cible=9.0, au_mieux=True)
    assert combine is not None and combine.objectif_atteint is True


def test_au_mieux_sans_aucun_candidat_ne_fabrique_rien() -> None:
    assert quotidien.combine_vers([], cible=9.0, au_mieux=True) is None


def test_les_plus_probables_sont_empiles_en_premier() -> None:
    """L'ordre minimise le nombre de jambes, donc d'occasions de tout perdre."""
    combine = quotidien.combine_vers(
        [_selection(2.0, 0.50), _selection(1.2, 0.83), _selection(1.5, 0.66)],
        cible=100.0, au_mieux=True)
    assert [round(j.cote, 2) for j in combine.jambes] == [1.2, 1.5, 2.0]
