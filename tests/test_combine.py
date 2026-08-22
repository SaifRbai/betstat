"""Tests du calcul de combine.

Le point delicat est la correlation : deux jambes du meme match ne se
multiplient pas. On verifie contre un calcul force brute sur la matrice.
"""

from __future__ import annotations

import numpy as np
import pytest

import config
from paris import combine as combine_module
from paris.combine import Jambe


class ModeleBouchon:
    """Deux matchs, deux matrices de scores fabriquees a la main."""

    equipes = ["A", "B", "C", "D"]

    def __init__(self) -> None:
        # matrice 4x4 : indices [buts domicile, buts exterieur]
        matrice = np.array([
            [0.10, 0.08, 0.04, 0.01],
            [0.12, 0.14, 0.06, 0.02],
            [0.09, 0.10, 0.05, 0.01],
            [0.04, 0.03, 0.01, 0.00],
        ])
        self._matrices = {
            ("A", "B"): matrice / matrice.sum(),
            ("C", "D"): (matrice.T / matrice.sum()),
        }

    def matrice_scores(self, dom, ext, taille=None):
        return self._matrices[(dom, ext)]


@pytest.fixture()
def modele():
    return ModeleBouchon()


def _force_brute(matrice, conditions) -> float:
    """Somme des cases qui satisfont toutes les conditions, sans passer par le module."""
    total = 0.0
    for i in range(matrice.shape[0]):
        for j in range(matrice.shape[1]):
            if all(c(i, j) for c in conditions):
                total += matrice[i, j]
    return total


# --------------------------------------------------------------------------
# Masques
# --------------------------------------------------------------------------

def test_masque_1n2(modele):
    matrice = modele.matrice_scores("A", "B")
    for selection, condition in (("1", lambda i, j: i > j),
                                 ("N", lambda i, j: i == j),
                                 ("2", lambda i, j: i < j)):
        obtenu = float(matrice[combine_module.masque(matrice, "1N2", selection)].sum())
        assert obtenu == pytest.approx(_force_brute(matrice, [condition]))


def test_masque_totaux(modele):
    matrice = modele.matrice_scores("A", "B")
    plus = float(matrice[combine_module.masque(matrice, "plus_moins_2.5", "plus")].sum())
    moins = float(matrice[combine_module.masque(matrice, "plus_moins_2.5", "moins")].sum())
    assert plus == pytest.approx(_force_brute(matrice, [lambda i, j: i + j > 2.5]))
    assert plus + moins == pytest.approx(1.0)


def test_masque_refuse_un_marche_inconnu(modele):
    with pytest.raises(ValueError, match="Marche non gere"):
        combine_module.masque(modele.matrice_scores("A", "B"), "handicap", "dom")


# --------------------------------------------------------------------------
# Probabilite jointe
# --------------------------------------------------------------------------

def test_une_seule_jambe_redonne_sa_probabilite(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 2.0)]
    reelle, independante, notes = combine_module.probabilite_jointe(modele, jambes)
    matrice = modele.matrice_scores("A", "B")
    assert reelle == pytest.approx(_force_brute(matrice, [lambda i, j: i > j]))
    assert reelle == pytest.approx(independante)
    assert notes == []


def test_deux_jambes_du_meme_match_ne_se_multiplient_pas(modele):
    """Le coeur du sujet : « A gagne » et « plus de 2.5 buts » sont lies."""
    jambes = [Jambe("A", "B", "1N2", "1", 2.0),
              Jambe("A", "B", "plus_moins_2.5", "plus", 1.9)]
    reelle, independante, notes = combine_module.probabilite_jointe(modele, jambes)
    matrice = modele.matrice_scores("A", "B")
    attendu = _force_brute(matrice, [lambda i, j: i > j, lambda i, j: i + j > 2.5])
    assert reelle == pytest.approx(attendu)
    assert reelle != pytest.approx(independante)
    assert len(notes) == 1 and "meme match" in notes[0]


def test_jambes_incompatibles_donnent_zero(modele):
    """« A gagne » et « B gagne » dans le meme match : impossible."""
    jambes = [Jambe("A", "B", "1N2", "1", 2.0), Jambe("A", "B", "1N2", "2", 3.0)]
    reelle, independante, _ = combine_module.probabilite_jointe(modele, jambes)
    assert reelle == 0.0
    assert independante > 0.0          # c'est bien la multiplication qui ment


def test_deux_matchs_differents_sont_multiplies(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 2.0), Jambe("C", "D", "1N2", "1", 2.5)]
    reelle, independante, notes = combine_module.probabilite_jointe(modele, jambes)
    assert reelle == pytest.approx(independante)
    assert notes == []


def test_probabilites_par_jambe(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 2.0), Jambe("C", "D", "1N2", "N", 3.4)]
    par_jambe = combine_module.probabilites_par_jambe(modele, jambes)
    reelle, _, _ = combine_module.probabilite_jointe(modele, jambes)
    assert reelle == pytest.approx(par_jambe[0] * par_jambe[1])


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

def test_un_combine_sans_valeur_est_rejete_avec_son_motif(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 1.5), Jambe("C", "D", "1N2", "1", 1.5)]
    resultat = combine_module.evaluer(modele, jambes, bankroll=1000.0)
    assert not resultat.retenu
    assert resultat.mise == 0.0
    assert any("esperance" in m for m in resultat.motifs)


def test_la_cote_totale_est_le_produit(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 2.0), Jambe("C", "D", "1N2", "1", 3.0)]
    resultat = combine_module.evaluer(modele, jambes, bankroll=1000.0)
    assert resultat.cote_totale == pytest.approx(6.0)
    assert resultat.cote_equitable == pytest.approx(1.0 / resultat.p_modele)


def test_au_dela_de_trois_jambes_le_combine_est_ecarte(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 9.0),
              Jambe("A", "B", "plus_moins_2.5", "plus", 9.0),
              Jambe("C", "D", "1N2", "1", 9.0),
              Jambe("C", "D", "plus_moins_2.5", "plus", 9.0)]
    resultat = combine_module.evaluer(modele, jambes, bankroll=1000.0)
    assert not resultat.retenu
    assert any("jambes" in m and "marge" in m for m in resultat.motifs)


def test_la_mise_reste_plafonnee(modele):
    """Meme sur un combine tres favorable, le plafond de 2 % tient."""
    jambes = [Jambe("A", "B", "1N2", "1", 50.0)]
    resultat = combine_module.evaluer(modele, jambes, bankroll=1000.0)
    assert resultat.mise <= 1000.0 * config.PLAFOND_MISE_BANKROLL + 1e-9


def test_une_selection_en_double_est_refusee(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 2.0), Jambe("A", "B", "1N2", "1", 2.0)]
    with pytest.raises(ValueError, match="deux fois"):
        combine_module.evaluer(modele, jambes, bankroll=1000.0)


def test_un_combine_vide_est_refuse(modele):
    with pytest.raises(ValueError, match="au moins une"):
        combine_module.evaluer(modele, [], bankroll=1000.0)


def test_le_resume_dit_la_probabilite_que_tout_passe(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 2.0), Jambe("C", "D", "1N2", "1", 3.0)]
    texte = combine_module.evaluer(modele, jambes, bankroll=1000.0).resume()
    assert "Probabilite que TOUT passe" in texte
    assert "Cote equitable" in texte


# --------------------------------------------------------------------------
# Recalage sur le marche
# --------------------------------------------------------------------------

def test_sans_p_finale_on_garde_le_modele_brut(modele):
    jambes = [Jambe("A", "B", "1N2", "1", 3.0), Jambe("C", "D", "1N2", "1", 3.0)]
    resultat = combine_module.evaluer(modele, jambes, bankroll=1000.0)
    assert resultat.p_modele == pytest.approx(resultat.p_modele_seul)


def test_le_recalage_suit_le_melange_des_jambes(modele):
    """Le modele est plus extreme que le marche : recale, le combine baisse."""
    p_seules = combine_module.probabilites_par_jambe(modele, [
        Jambe("A", "B", "1N2", "1", 3.0), Jambe("C", "D", "1N2", "1", 3.0)])
    # on donne des p_finale plus basses que le modele, comme le fait le melange
    jambes = [Jambe("A", "B", "1N2", "1", 3.0, p_finale=p_seules[0] * 0.8),
              Jambe("C", "D", "1N2", "1", 3.0, p_finale=p_seules[1] * 0.9)]
    resultat = combine_module.evaluer(modele, jambes, bankroll=1000.0)
    assert resultat.p_modele == pytest.approx(resultat.p_modele_seul * 0.8 * 0.9)
    assert resultat.p_modele < resultat.p_modele_seul


def test_le_recalage_preserve_la_correlation(modele):
    """Deux jambes du meme match restent correlees apres recalage."""
    brut = [Jambe("A", "B", "1N2", "1", 2.0),
            Jambe("A", "B", "plus_moins_2.5", "plus", 1.9)]
    p_seules = combine_module.probabilites_par_jambe(modele, brut)
    recale = [Jambe("A", "B", "1N2", "1", 2.0, p_finale=p_seules[0] * 0.9),
              Jambe("A", "B", "plus_moins_2.5", "plus", 1.9, p_finale=p_seules[1] * 0.9)]
    resultat = combine_module.evaluer(modele, recale, bankroll=1000.0)
    assert resultat.p_modele == pytest.approx(resultat.p_modele_seul * 0.81)
    # et non le produit des p_finale, qui ignorerait la correlation
    assert resultat.p_modele != pytest.approx(recale[0].p_finale * recale[1].p_finale)


def test_l_esperance_utilise_la_probabilite_recalee(modele):
    p_seules = combine_module.probabilites_par_jambe(modele, [Jambe("A", "B", "1N2", "1", 3.0)])
    jambe = Jambe("A", "B", "1N2", "1", 3.0, p_finale=p_seules[0] * 0.5)
    resultat = combine_module.evaluer(modele, [jambe], bankroll=1000.0)
    assert resultat.esperance == pytest.approx(resultat.p_modele * 3.0 - 1.0)
