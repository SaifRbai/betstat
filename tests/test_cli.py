"""Tests de cli.py (T16) : chaque sous-commande existe et les erreurs sont lisibles."""

from __future__ import annotations

import pandas as pd
import pytest

import cli


def test_toutes_les_sous_commandes_sont_declarees() -> None:
    analyseur = cli.construire_analyseur()
    action = next(a for a in analyseur._actions if a.dest == "commande")
    attendues = {"charger", "match", "journee", "forces", "cotes", "opportunites",
                 "parier", "regler", "bilan", "clotures", "backtest", "calibrer",
                 "diagnostic"}
    assert attendues <= set(action.choices)


def test_aide_lisible() -> None:
    texte = cli.construire_analyseur().format_help()
    assert "opportunites" in texte and "backtest" in texte
    assert "estimations" in texte  # l'avertissement legal figure dans l'aide


def test_resolution_exacte() -> None:
    assert cli.resoudre_equipe("Lyon", ["Lyon", "Nice"]) == "Lyon"
    assert cli.resoudre_equipe("lyon", ["Lyon", "Nice"]) == "Lyon"


def test_resolution_par_alias_canonique() -> None:
    assert cli.resoudre_equipe("PSG", ["Paris SG", "Marseille"]) == "Paris SG"
    assert cli.resoudre_equipe("Paris Saint-Germain", ["Paris SG"]) == "Paris SG"


def test_resolution_floue_proche() -> None:
    assert cli.resoudre_equipe("Marseill", ["Marseille", "Monaco"]) == "Marseille"


def test_echec_propose_trois_noms() -> None:
    with pytest.raises(SystemExit) as erreur:
        cli.resoudre_equipe("Zzzzzz Club", ["Lyon", "Nice", "Metz", "Lens"])
    message = str(erreur.value)
    assert "Voulais-tu dire" in message
    assert message.count("'") >= 6  # trois propositions entre quotes


def test_championnat_inconnu_renvoie_code_2(capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["charger", "--championnat", "ZZ"])
    assert code == 2
    sortie = capsys.readouterr().out
    assert "Championnat inconnu" in sortie
    assert "Liste complete" in sortie


def test_diagnostic(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["diagnostic"]) == 0
    sortie = capsys.readouterr().out
    assert "Dossier de donnees" in sortie
    assert "Quota The Odds API" in sortie


def test_bilan_journal_vide(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["bilan"]) == 0
    assert "Journal vide" in capsys.readouterr().out


def test_parier_sans_liste_prealable(capsys: pytest.CaptureFixture,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "CHEMIN_OPPORTUNITES",
                        cli.config.DOSSIER_DONNEES / "inexistant.json")
    assert cli.main(["parier", "--id", "1"]) == 2
    assert "opportunites" in capsys.readouterr().out


def test_regler_identifiant_inexistant(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["regler", "--id", "99999", "--resultat", "gagne"]) == 2
    assert "Aucun pari numero" in capsys.readouterr().out


def test_filtres_journee_couvrent_les_marches() -> None:
    tableau = pd.DataFrame({
        "dom": ["A", "B"], "ext": ["B", "A"], "p1": [0.6, 0.3], "pN": [0.2, 0.3],
        "p2": [0.2, 0.4], "plus25": [0.4, 0.7], "plus35": [0.2, 0.5],
        "moins25": [0.6, 0.3], "btts": [0.4, 0.6], "buts_attendus": [2.1, 3.2]})
    assert cli._filtrer_journee(tableau, "favori").iloc[0]["dom"] == "A"
    assert cli._filtrer_journee(tableau, "over25").iloc[0]["dom"] == "B"
    assert cli._filtrer_journee(tableau, "under25").iloc[0]["dom"] == "A"
    assert cli._filtrer_journee(tableau, "btts").iloc[0]["dom"] == "B"


# --------------------------------------------------------------------------
# outils/verifier_noms.py
# --------------------------------------------------------------------------

def test_taux_mesure_dans_le_bon_sens() -> None:
    """Une equipe sans match a venir n'est pas un trou dans la table.

    Pour un flux de cotes, seuls les matchs a venir sont cotes : on mesure donc
    la part des noms du bookmaker qui sont resolus, pas la part des equipes du
    championnat. Pour FBref/Understat, qui couvrent des saisons entieres, c'est
    l'inverse.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "outils"))
    import verifier_noms

    rapport = {"surs": [("a", "a", "A"), ("b", "b", "B")], "a_verifier": [],
               "orphelins_a": ["c", "d", "e"], "orphelins_b": []}
    equipes_fd = ["a", "b", "c", "d", "e"]
    equipes_b = ["a", "b"]

    ligne_cotes = verifier_noms._ligne_taux(rapport, equipes_fd, equipes_b, "odds_api")
    assert "100.0%" in ligne_cotes and "2/2" in ligne_cotes

    ligne_xg = verifier_noms._ligne_taux(rapport, equipes_fd, equipes_b, "understat")
    assert "40.0%" in ligne_xg and "2/5" in ligne_xg
