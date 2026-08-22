"""Tests de la confrontation prono / resultat.

Ce module decide de ce qui s'affiche en vert et de ce qui s'affiche en rouge.
Une erreur ici ne fait pas planter la page : elle ment sur ce que l'outil a
predit, ce qui est pire.
"""

from __future__ import annotations

import pytest

from paris import verdict


def _match(dom: int, ext: int, **extra) -> dict:
    return {"buts_dom": dom, "buts_ext": ext, **extra}


# --------------------------------------------------------------------------
# 1N2
# --------------------------------------------------------------------------

@pytest.mark.parametrize("score,issue", [
    ((2, 1), "1"), ((0, 0), "N"), ((1, 3), "2"), ((0, 1), "2"), ((4, 4), "N"),
])
def test_l_issue_se_lit_du_score(score, issue):
    assert verdict.issue_1n2(_match(*score)) == issue


@pytest.mark.parametrize("score,selection,attendu", [
    ((2, 1), "1", verdict.JUSTE),
    ((2, 1), "N", verdict.RATE),
    ((2, 1), "2", verdict.RATE),
    ((1, 1), "N", verdict.JUSTE),
    ((0, 2), "2", verdict.JUSTE),
])
def test_le_prono_1n2_est_confronte_au_score(score, selection, attendu):
    assert verdict.verifier("1N2", selection, _match(*score)) == attendu


# --------------------------------------------------------------------------
# Double chance : le piege des selections a deux lettres
# --------------------------------------------------------------------------

@pytest.mark.parametrize("score,selection,attendu", [
    ((2, 1), "1N", verdict.JUSTE),
    ((1, 1), "1N", verdict.JUSTE),
    ((0, 1), "1N", verdict.RATE),
    ((2, 1), "12", verdict.JUSTE),      # « pas de match nul »
    ((1, 1), "12", verdict.RATE),
    ((0, 3), "12", verdict.JUSTE),
    ((0, 3), "N2", verdict.JUSTE),
    ((1, 1), "N2", verdict.JUSTE),
    ((3, 0), "N2", verdict.RATE),
])
def test_la_double_chance_couvre_bien_deux_issues(score, selection, attendu):
    assert verdict.verifier("double chance", selection, _match(*score)) == attendu


# --------------------------------------------------------------------------
# Totaux, BTTS, clean sheet
# --------------------------------------------------------------------------

@pytest.mark.parametrize("score,marche,selection,attendu", [
    ((2, 1), "totaux 2.5", "plus", verdict.JUSTE),      # 3 buts
    ((2, 1), "totaux 2.5", "moins", verdict.RATE),
    ((1, 1), "totaux 2.5", "moins", verdict.JUSTE),     # 2 buts
    ((0, 0), "totaux 0.5", "moins", verdict.JUSTE),
    ((1, 0), "totaux 0.5", "plus", verdict.JUSTE),
    ((3, 2), "totaux 4.5", "plus", verdict.JUSTE),
])
def test_les_totaux_sont_tranches_par_le_seuil(score, marche, selection, attendu):
    assert verdict.verifier(marche, selection, _match(*score)) == attendu


@pytest.mark.parametrize("score,selection,attendu", [
    ((1, 1), "oui", verdict.JUSTE),
    ((1, 0), "oui", verdict.RATE),
    ((1, 0), "non", verdict.JUSTE),
    ((0, 0), "non", verdict.JUSTE),
])
def test_btts(score, selection, attendu):
    assert verdict.verifier("btts", selection, _match(*score)) == attendu


@pytest.mark.parametrize("score,selection,attendu", [
    ((2, 0), "dom", verdict.JUSTE),     # le domicile n'encaisse pas
    ((2, 1), "dom", verdict.RATE),
    ((0, 3), "ext", verdict.JUSTE),
    ((1, 3), "ext", verdict.RATE),
])
def test_clean_sheet(score, selection, attendu):
    assert verdict.verifier("clean sheet", selection, _match(*score)) == attendu


# --------------------------------------------------------------------------
# Corners et cartons
# --------------------------------------------------------------------------

def test_les_comptes_annexes_utilisent_leurs_propres_colonnes():
    resultat = _match(1, 0, corners_dom=6, corners_ext=4, jaunes_dom=2, jaunes_ext=1)
    assert verdict.verifier_compte("corners", "plus", 8.5, resultat) == verdict.JUSTE
    assert verdict.verifier_compte("corners", "moins", 8.5, resultat) == verdict.RATE
    assert verdict.verifier_compte("cartons jaunes", "moins", 3.5, resultat) == verdict.JUSTE
    assert verdict.verifier_compte("cartons jaunes", "plus", 3.5, resultat) == verdict.RATE


def test_le_duel_designe_qui_en_obtient_le_plus():
    resultat = _match(1, 0, corners_dom=9, corners_ext=3)
    assert verdict.verifier_duel("1", "corners", resultat) == verdict.JUSTE
    assert verdict.verifier_duel("2", "corners", resultat) == verdict.RATE


# --------------------------------------------------------------------------
# L'inconnu : le cas qui protege du mensonge
# --------------------------------------------------------------------------

def test_un_resultat_absent_ne_vaut_jamais_juste():
    """Compter un prono non verifiable comme juste gonflerait tout bilan."""
    assert verdict.verifier("1N2", "1", {}) == verdict.INCONNU
    assert verdict.verifier("btts", "oui", {"buts_dom": 1}) == verdict.INCONNU
    assert verdict.issue_1n2({}) is None


def test_une_colonne_manquante_reste_inconnue():
    """football-data ne remplit pas les corners partout."""
    resultat = _match(1, 0)          # ni corners ni cartons
    assert verdict.verifier_compte("corners", "plus", 8.5, resultat) == verdict.INCONNU
    assert verdict.verifier_duel("1", "cartons jaunes", resultat) == verdict.INCONNU


def test_un_nan_est_traite_comme_une_absence():
    """pandas remplit les colonnes vides avec NaN, pas avec None."""
    assert verdict.verifier("1N2", "1", {"buts_dom": float("nan"),
                                         "buts_ext": 0}) == verdict.INCONNU


def test_un_marche_inconnu_ne_leve_pas():
    """Ajouter un marche a l'affichage ne doit pas casser la page."""
    assert verdict.verifier("handicap asiatique", "1", _match(2, 0)) == verdict.INCONNU


# --------------------------------------------------------------------------
# Ce qu'un verdict isole ne prouve pas
# --------------------------------------------------------------------------

def test_un_echec_annonce_est_rappele_comme_tel():
    """Un prono a 80 % DOIT echouer une fois sur cinq : une pastille rouge
    n'est pas la preuve d'une erreur du modele."""
    texte = verdict.nuance(0.80, verdict.RATE)
    assert "1 fois sur 5" in texte


def test_la_nuance_se_tait_quand_il_n_y_a_rien_a_dire():
    assert verdict.nuance(None, verdict.RATE) is None
    assert verdict.nuance(0.8, verdict.INCONNU) is None


def test_le_bilan_tient_les_inconnus_a_part():
    compte = verdict.bilan([verdict.JUSTE, verdict.JUSTE,
                            verdict.RATE, verdict.INCONNU])
    assert compte == {"juste": 2, "rate": 1, "inconnu": 1, "verifiables": 3}
