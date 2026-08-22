"""Tests du journal de paris et du CLV (T12, T13). Aucun acces reseau."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data import cotes as cotes_module
from paris import journal


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _trois_paris(base: Path) -> tuple[int, int, int]:
    a = journal.enregistrer("Lyon", "Nice", "1N2", "1", 2.10, 20.0, "2026-09-01",
                            championnat="F1", bookmaker="bk1", p_modele=0.55,
                            p_marche=0.52, p_finale=0.54, esperance=0.13, chemin_db=base)
    b = journal.enregistrer("Lens", "Brest", "1N2", "2", 3.40, 10.0, "2026-09-02",
                            championnat="F1", bookmaker="bk2", chemin_db=base)
    c = journal.enregistrer("Arsenal", "Chelsea", "plus_moins_2.5", "plus", 1.90, 15.0,
                            "2026-09-03", championnat="E0", chemin_db=base)
    return a, b, c


def test_enregistrer_regler_et_bilan(base: Path) -> None:
    a, b, _ = _trois_paris(base)
    journal.regler(a, "gagne", base)
    journal.regler(b, "perdu", base)

    resultat = journal.bilan(base)
    assert resultat["nb_paris"] == 3
    assert resultat["nb_en_cours"] == 1
    assert resultat["mise_totale"] == pytest.approx(30.0)
    assert resultat["pnl"] == pytest.approx(12.0)
    assert resultat["roi"] == pytest.approx(0.40)
    assert resultat["exposition"] == pytest.approx(15.0)


def test_schema_prevoit_cote_cloture_et_clv(base: Path) -> None:
    _trois_paris(base)
    colonnes = journal.lister(chemin_db=base).columns
    assert "cote_cloture" in colonnes and "clv" in colonnes


def test_bilan_par_marche_et_championnat(base: Path) -> None:
    a, b, _ = _trois_paris(base)
    journal.regler(a, "gagne", base)
    journal.regler(b, "perdu", base)
    marches = journal.bilan_par_marche(base)
    assert set(marches["marche"]) == {"1N2", "plus_moins_2.5"}
    assert marches[marches["marche"] == "1N2"]["pnl"].iloc[0] == pytest.approx(12.0)
    assert set(journal.bilan_par_championnat(base)["championnat"]) == {"F1", "E0"}


def test_remboursement_neutre(base: Path) -> None:
    a, *_ = _trois_paris(base)
    assert journal.regler(a, "rembourse", base)["gain"] == 0.0


def test_regler_pari_inexistant(base: Path) -> None:
    with pytest.raises(KeyError, match="Aucun pari numero"):
        journal.regler(999, "gagne", base)


def test_resultat_invalide(base: Path) -> None:
    a, *_ = _trois_paris(base)
    with pytest.raises(ValueError, match="Resultat invalide"):
        journal.regler(a, "presque_gagne", base)


def test_cote_ou_mise_invalide(base: Path) -> None:
    with pytest.raises(ValueError, match="Cote invalide"):
        journal.enregistrer("A", "B", "1N2", "1", 0.9, 10.0, "2026-09-01", chemin_db=base)
    with pytest.raises(ValueError, match="mise"):
        journal.enregistrer("A", "B", "1N2", "1", 2.0, 0.0, "2026-09-01", chemin_db=base)


def test_courbe_bankroll(base: Path) -> None:
    a, b, _ = _trois_paris(base)
    journal.regler(a, "gagne", base)
    journal.regler(b, "perdu", base)
    courbe = journal.courbe_bankroll(100.0, base)
    assert courbe["bankroll"].tolist() == [122.0, 112.0]


def test_export_csv(base: Path, tmp_path: Path) -> None:
    _trois_paris(base)
    chemin = journal.exporter_csv(tmp_path / "export.csv", base)
    assert len(pd.read_csv(chemin)) == 3


# --------------------------------------------------------------------------
# CLV
# --------------------------------------------------------------------------

def _instantane_cloture() -> pd.DataFrame:
    lignes = []
    for bookmaker, prix in {"bk1": (2.00, 3.40, 3.60), "bk2": (1.95, 3.50, 3.70),
                            "bk3": (2.02, 3.35, 3.55)}.items():
        for selection, cote in zip(("1", "N", "2"), prix):
            lignes.append({"bookmaker": bookmaker, "selection": selection, "cote": cote})
    return pd.DataFrame(lignes)


def test_cote_cloture_devigguee_retire_la_marge() -> None:
    instantane = _instantane_cloture()
    cloture = journal.cote_cloture_devigguee(instantane, "1N2", "1")
    assert cloture is not None
    # La cote equitable est forcement meilleure que la cote brute proposee.
    assert cloture > 2.02


def test_calcul_du_clv() -> None:
    assert journal.calculer_clv(2.10, 2.00) == pytest.approx(0.05)
    assert journal.calculer_clv(1.90, 2.00) == pytest.approx(-0.05)


def test_capturer_clotures_sans_reseau(base: Path) -> None:
    """Le CLV se calcule a partir des cotes deja historisees, sans rappeler l'API."""
    identifiant = journal.enregistrer("Lyon", "Nice", "1N2", "1", 2.30, 20.0,
                                      "2026-09-01T19:00:00Z", championnat="F1", chemin_db=base)
    instantane = _instantane_cloture()
    instantane["date"] = "2026-09-01T19:00:00Z"
    instantane["dom"], instantane["ext"] = "Lyon", "Nice"
    instantane["championnat"], instantane["marche"] = "F1", "1N2"
    instantane["horodatage"] = "2026-09-01T18:45:00"
    cotes_module.historiser(instantane, base)

    resultat = journal.capturer_clotures(base, rafraichir_api=False,
                                         maintenant="2026-09-01T18:50:00")
    assert resultat["traites"] == 1
    pari = journal.lister(chemin_db=base).iloc[0]
    assert pari["cote_cloture"] > 2.02
    assert pari["clv"] == pytest.approx(2.30 / pari["cote_cloture"] - 1)


def test_bilan_clv_message_quand_vide(base: Path) -> None:
    _trois_paris(base)
    resultat = journal.bilan_clv(base)
    assert resultat["n"] == 0
    assert "capturer_clotures" in resultat["message"]


def test_interpretation_clv_explique_pourquoi() -> None:
    texte = journal.interpreter_clv(0.03, 0.62, 120)
    assert "cote de cloture" in texte and "chance" in texte
    assert "signal le plus solide" in texte
    negatif = journal.interpreter_clv(-0.02, 0.30, 150)
    assert "CLV negatif" in negatif


def test_capturer_clotures_journal_vide(base: Path) -> None:
    assert journal.capturer_clotures(base, rafraichir_api=False)["traites"] == 0
