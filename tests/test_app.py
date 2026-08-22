"""Tests de app.py sans serveur graphique : tkinter est remplace par un bouchon.

Ils attrapent les erreurs de definition et les attributs manquants, pas les
fenetres vides : la checklist de doc/A_FAIRE_INTERFACE.md reste indispensable.
"""

from __future__ import annotations

import pathlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def app_module(monkeypatch):
    for nom in ("tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog"):
        module = types.ModuleType(nom)
        module.__getattr__ = lambda n: MagicMock()
        monkeypatch.setitem(sys.modules, nom, module)
    tk = sys.modules["tkinter"]
    tk.Tk = type("Tk", (), {"__init__": lambda s: None,
                            "__getattr__": lambda s, n: MagicMock()})
    tk.ttk = sys.modules["tkinter.ttk"]
    monkeypatch.delitem(sys.modules, "app", raising=False)
    import app
    return app


def test_import_sans_serveur_graphique(app_module):
    assert hasattr(app_module, "Application")


def test_ecran_de_premier_lancement_contient_les_garde_fous(app_module):
    message = app_module.MESSAGE_PREMIER_LANCEMENT
    assert "2 %" in message or "2%" in message      # plafond de mise
    assert "CLV" in message                          # CLV avant P&L
    assert "ESTIMATIONS" in message.upper()          # marge d'erreur


def test_aucun_libelle_interdit(app_module):
    texte = (RACINE / "app.py").read_text(encoding="utf-8").lower()
    for interdit in ("match sur", "banker", "pronostic garanti"):
        assert interdit not in texte


def test_forme_sur_six_matchs(app_module):
    matchs = pd.DataFrame({
        "dom": ["A", "B", "A"], "ext": ["B", "A", "C"],
        "buts_dom": [2, 0, 1], "buts_ext": [1, 0, 3]})
    forme, marques, encaisses = app_module.Application._forme(matchs, "A")
    assert forme == "VND"
    assert (marques, encaisses) == (2 + 0 + 1, 1 + 0 + 3)


# --------------------------------------------------------------------------
# Bug #1 : l'interface doit passer par l'entrainement du CLI
# --------------------------------------------------------------------------

def test_l_interface_utilise_l_entrainement_du_cli(app_module):
    """Sans cela, interface et ligne de commande divergent sur les memes donnees."""
    import cli
    assert app_module.entrainer_modele is cli.entrainer_modele
    source = (RACINE / "app.py").read_text(encoding="utf-8")
    assert "dc.entrainer(" not in source


# --------------------------------------------------------------------------
# Manque #2 : statistiques brutes de l'onglet Forces
# --------------------------------------------------------------------------

def test_stats_brutes_moyennes_par_match(app_module):
    matchs = pd.DataFrame({
        "dom": ["A", "B"], "ext": ["B", "A"],
        "tirs_dom": [10.0, 8.0], "tirs_ext": [4.0, 6.0],
        "tirs_cadres_dom": [5.0, 3.0], "tirs_cadres_ext": [1.0, 3.0],
        "corners_dom": [7.0, 1.0], "corners_ext": [3.0, 5.0]})
    tirs, cadres, corners = app_module.Application._stats_brutes(matchs, "A")
    assert (tirs, cadres, corners) == ("8.0", "4.0", "6.0")


def test_stats_brutes_colonnes_absentes(app_module):
    matchs = pd.DataFrame({"dom": ["A"], "ext": ["B"]})
    assert app_module.Application._stats_brutes(matchs, "A") == ("-", "-", "-")


# --------------------------------------------------------------------------
# Manque #1 : vraies affiches de la journee
# --------------------------------------------------------------------------

def test_affiches_a_venir_resout_les_noms(app_module):
    cotes = pd.DataFrame({
        "date": ["2026-08-22T18:00:00Z", "2026-08-22T20:00:00Z"],
        "dom": ["Paris SG", "Lens"], "ext": ["Marseille", "Inconnu FC"],
        "bookmaker": ["b1", "b1"]})
    faux = SimpleNamespace(contexte=SimpleNamespace(
        cotes=cotes, modele=SimpleNamespace(equipes=["Paris SG", "Marseille", "Lens"])))
    assert app_module.Application.affiches_a_venir(faux) == [("Paris SG", "Marseille")]


def test_affiches_a_venir_sans_cotes(app_module):
    faux = SimpleNamespace(contexte=SimpleNamespace(cotes=None, modele=None))
    assert app_module.Application.affiches_a_venir(faux) == []


# --------------------------------------------------------------------------
# Bloc 4 : annulation et preferences
# --------------------------------------------------------------------------

class _FausseRacine:
    """Remplace la fenetre : memorise les rappels au lieu de les programmer."""

    def __init__(self) -> None:
        self.rappels: list = []
        self.annules: list = []

    def after(self, _delai: int, fonction) -> str:
        self.rappels.append(fonction)
        return f"after#{len(self.rappels)}"

    def after_cancel(self, identifiant: str) -> None:
        self.annules.append(identifiant)


def _preparer(app_module, recus: list):
    """Travailleur avec une tache 'charger' en vol, sans lancer de thread."""
    travailleur = app_module.Travailleur(_FausseRacine())
    travailleur._rappels["charger"] = (recus.append, None)
    generation = travailleur._nouvelle_generation("charger")
    travailleur.en_cours.add("charger")
    return travailleur, generation


def test_sans_annulation_le_resultat_arrive(app_module):
    recus: list = []
    travailleur, generation = _preparer(app_module, recus)
    travailleur.file.put(("charger", (generation, "ok", 42)))
    travailleur._sonder()
    assert recus == [42]
    assert travailleur.en_cours == set()


def test_annuler_jette_le_resultat(app_module):
    recus: list = []
    travailleur, generation = _preparer(app_module, recus)
    assert travailleur.annuler("charger") is True
    travailleur.file.put(("charger", (generation, "ok", 42)))
    travailleur._sonder()
    assert recus == []
    assert travailleur.annuler("charger") is False


def test_relancer_perime_le_resultat_precedent(app_module):
    """Annuler puis relancer : seul le resultat de la relance doit s'afficher."""
    recus: list = []
    travailleur, ancienne = _preparer(app_module, recus)
    travailleur.annuler("charger")
    nouvelle = travailleur._nouvelle_generation("charger")
    travailleur.en_cours.add("charger")
    travailleur.file.put(("charger", (ancienne, "ok", "perime")))
    travailleur.file.put(("charger", (nouvelle, "ok", "frais")))
    travailleur._sonder()
    assert recus == ["frais"]


def test_annuler_tout(app_module):
    recus: list = []
    travailleur, generation = _preparer(app_module, recus)
    travailleur._rappels["cotes"] = (recus.append, None)
    generation_cotes = travailleur._nouvelle_generation("cotes")
    travailleur.en_cours.add("cotes")
    assert travailleur.annuler() is True
    travailleur.file.put(("charger", (generation, "ok", 1)))
    travailleur.file.put(("cotes", (generation_cotes, "ok", 2)))
    travailleur._sonder()
    assert recus == []


def test_une_erreur_passe_par_le_rappel_d_echec(app_module):
    messages: list = []
    travailleur = app_module.Travailleur(_FausseRacine())
    travailleur._rappels["charger"] = (None, messages.append)
    generation = travailleur._nouvelle_generation("charger")
    travailleur.en_cours.add("charger")
    travailleur.file.put(("charger", (generation, "erreur", "pas de reseau")))
    travailleur._sonder()
    assert messages == ["pas de reseau"]


def test_preferences_aller_retour(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "CHEMIN_PREFERENCES", tmp_path / "preferences.json")
    assert app_module.lire_preferences() == {}
    app_module.ecrire_preferences({"championnat": "E0", "bankroll": 500.0})
    assert app_module.lire_preferences()["championnat"] == "E0"


def test_preferences_fichier_illisible(app_module, tmp_path, monkeypatch):
    chemin = tmp_path / "preferences.json"
    chemin.write_text("ceci n'est pas du json", encoding="utf-8")
    monkeypatch.setattr(app_module, "CHEMIN_PREFERENCES", chemin)
    assert app_module.lire_preferences() == {}


# --------------------------------------------------------------------------
# Fumee : on construit la fenetre entiere et on declenche chaque action.
# Sans serveur graphique on ne voit aucun pixel, mais toute erreur d'ordre
# d'initialisation ou d'attribut manquant se voit ici.
# --------------------------------------------------------------------------

class _Variable:
    """Variable tkinter bouchon : elle garde vraiment sa valeur."""

    def __init__(self, valeur=None):
        self._valeur = valeur

    def get(self):
        return self._valeur

    def set(self, valeur):
        self._valeur = valeur


class _Combobox(MagicMock):
    """Combobox bouchon : get() renvoie une vraie chaine, pas un MagicMock."""

    def get(self):
        return self.__dict__.get("_texte", "F1 — Ligue 1")

    def set(self, valeur):
        self.__dict__["_texte"] = valeur

    def current(self, _index):
        self.__dict__["_texte"] = "F1 — Ligue 1"


@pytest.fixture()
def fenetre(app_module, monkeypatch):
    tk = sys.modules["tkinter"]
    tk.StringVar = lambda value="", **k: _Variable(value)
    tk.DoubleVar = lambda value=0.0, **k: _Variable(float(value))
    tk.IntVar = lambda value=0, **k: _Variable(int(value))
    tk.BooleanVar = lambda value=False, **k: _Variable(bool(value))
    sys.modules["tkinter.ttk"].Combobox = lambda *a, **k: _Combobox()
    # aucun thread ne doit survivre au test : on note l'appel sans l'executer
    lancees: list[str] = []
    monkeypatch.setattr(app_module.Travailleur, "lancer",
                        lambda self, nom, *a, **k: lancees.append(nom))
    fenetre = app_module.Application()
    fenetre.taches_lancees = lancees
    return fenetre


_ACTIONS_SANS_DONNEES = (
    "calculer_journee", "rafraichir_cotes", "analyser_match", "appliquer_ajustement",
    "lancer_backtest", "reinitialiser_ajustements", "verifier_noms", "afficher_forces",
    "exporter_journee", "rafraichir_bandeau", "rafraichir_journal", "annuler_tache",
    "rafraichir_onglet_actif", "memoriser_preferences", "enregistrer_pari",
    "tracer_bankroll",
)


def test_les_sept_onglets_sont_construits(fenetre):
    onglets = [nom for nom in vars(fenetre) if nom.startswith("onglet_")]
    assert len(onglets) == 7
    assert "onglet_journal" in onglets


@pytest.mark.parametrize("action", _ACTIONS_SANS_DONNEES)
def test_action_sans_donnees_chargees(fenetre, action):
    """Sans championnat charge : un message, jamais une trace."""
    getattr(fenetre, action)()


def test_aucun_calcul_lance_sans_donnees(fenetre):
    for action in _ACTIONS_SANS_DONNEES:
        getattr(fenetre, action)()
    assert fenetre.taches_lancees == []


def test_graphiques_de_backtest_fichier_absent(fenetre):
    fenetre.afficher_graphiques_backtest({"bankroll": "/inexistant.png"})


def test_fermeture_memorise_les_preferences(fenetre, app_module):
    fenetre.bankroll.set(500.0)
    fenetre.fermer()
    assert app_module.CHEMIN_PREFERENCES.exists()
    assert app_module.lire_preferences()["bankroll"] == 500.0
    assert fenetre.travailleur.arret.is_set()


def test_fermer_annule_le_sondage_programme(app_module):
    """Sinon le after() en attente se declenche apres destroy() et Tk proteste."""
    racine = _FausseRacine()
    travailleur = app_module.Travailleur(racine)
    travailleur.fermer()
    assert racine.annules == ["after#1"]
    assert travailleur.arret.is_set()
    travailleur.fermer()                      # deux fermetures : pas de double annulation
    assert racine.annules == ["after#1"]


def test_matplotlib_est_importe_avant_tkinter(app_module):
    """Ordre critique : voir le commentaire en tete de app.py.

    Inverse, le rendu des glyphes de matplotlib casse et l'interface ne produit
    plus aucun PNG. Ca ne se voit qu'a l'execution, d'ou ce garde-fou.
    """
    source = (RACINE / "app.py").read_text(encoding="utf-8")
    assert source.index("import matplotlib.ft2font") < source.index("import tkinter as tk")
