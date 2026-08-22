#!/usr/bin/env python3
"""Interface graphique tkinter de foot_edge (7 onglets).

Principe de fonctionnement : l'interface ne calcule jamais rien elle-meme.
Tout traitement long part dans un thread daemon qui depose ses resultats dans
une queue.Queue ; la boucle tkinter sonde cette queue avec after(). L'interface
ne se fige donc jamais, et fermer la fenetre pendant un telechargement ne
laisse pas de thread zombie (threads daemon + evenement d'arret).
"""

from __future__ import annotations

import json
import queue
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# A LAISSER AVANT L'IMPORT DE TKINTER.
# matplotlib embarque sa propre FreeType (2.14.3) et en exporte les symboles ;
# tkinter charge celle du systeme (2.14.2) via Xft. La premiere arrivee
# l'emporte pour tout le process. Chargee apres tkinter, matplotlib appelle
# donc une FreeType d'une autre version que celle de ses structures, et le
# rendu des glyphes part en « FT_Render_Glyph ... raster overflow » : plus un
# seul PNG, ni pour le backtest ni pour la courbe de bankroll. Ce simple import
# lui donne la priorite. Sans matplotlib installe, il n'y a rien a faire.
try:
    import matplotlib.ft2font  # noqa: F401
except ImportError:  # pragma: no cover - matplotlib est une dependance optionnelle
    pass

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as erreur:  # pragma: no cover - depend du systeme
    raise SystemExit(
        "tkinter n'est pas disponible sur cette installation de Python.\n"
        "  Debian/Ubuntu : sudo apt install python3-tk\n"
        "  Fedora        : sudo dnf install python3-tkinter\n"
        "  macOS/Windows : reinstalle Python depuis python.org (tkinter est inclus)\n"
        "  En attendant, tout est accessible en ligne de commande : python cli.py --help"
    ) from erreur

import config
from cli import entrainer_modele
from data import cotes as cotes_module
from data import noms as noms_module
from data import resultats as resultats_module
from data import xg as xg_module
from modele import backtest as backtest_module
from modele import calibration
from modele import dixon_coles as dc
from paris import journal as journal_module
from paris import valeur as valeur_module

CHEMIN_PREFERENCES = config.DOSSIER_DONNEES / "preferences.json"

COULEUR_ALERTE = "#ffd8a8"   # orange doux : divergence modele/marche
COULEUR_RETENUE = "#d3f9d8"  # vert doux : opportunite qui passe les 4 conditions

MESSAGE_PREMIER_LANCEMENT = (
    "Avant de commencer\n\n"
    "1. Les probabilites affichees sont des ESTIMATIONS, avec une marge d'erreur "
    "reelle. Ce ne sont pas des pronostics.\n\n"
    "2. La mise conseillee est un Kelly divise par 4, plafonne a "
    f"{config.PLAFOND_MISE_BANKROLL:.0%} de ta bankroll. "
    "Ce plafond n'est pas modifiable : le Kelly complet ruine statistiquement "
    "un joueur dont l'estimation de probabilite est un peu fausse.\n\n"
    "3. Ton P&L sur quelques centaines de paris ne prouve rien : il est domine par "
    "la chance. Le CLV (onglet Mon journal) converge beaucoup plus vite et repond "
    "vraiment a la question \"est-ce que mon avantage existe ?\".\n\n"
    "4. Le marche est l'estimateur le plus precis disponible. Ce logiciel sert a "
    "reperer les endroits ou il semble decale, pas a le remplacer.\n\n"
    + config.AVERTISSEMENT_LEGAL
)


@dataclass
class Contexte:
    """Etat partage entre les onglets : donnees chargees et modele entraine."""

    championnat: str = "F1"
    matchs: Any = None
    modele: dc.ModeleDixonColes | None = None
    rapport_xg: dict | None = None
    cotes: Any = None
    opportunites: list[valeur_module.Opportunite] | None = None

    def pret(self) -> bool:
        return self.modele is not None


# --------------------------------------------------------------------------
# Infrastructure de threads
# --------------------------------------------------------------------------

class Travailleur:
    """Execute des taches longues hors du thread graphique."""

    def __init__(self, racine: "tk.Misc") -> None:
        self.racine = racine
        self.file: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.arret = threading.Event()
        self._rappels: dict[str, tuple[Callable, Callable | None]] = {}
        self._generation: dict[str, int] = {}
        self.en_cours: set[str] = set()
        self._rappel_sondage: str | None = self.racine.after(100, self._sonder)

    def lancer(self, nom: str, tache: Callable[[], Any],
               succes: Callable[[Any], None],
               echec: Callable[[str], None] | None = None) -> None:
        self._rappels[nom] = (succes, echec)
        generation = self._nouvelle_generation(nom)
        self.en_cours.add(nom)

        def executer() -> None:
            try:
                resultat = tache()
            except Exception as erreur:  # remonte proprement dans l'interface
                self.file.put((nom, (generation, "erreur", _message_erreur(erreur))))
            else:
                if not self.arret.is_set():
                    self.file.put((nom, (generation, "ok", resultat)))

        threading.Thread(target=executer, name=f"foot_edge-{nom}", daemon=True).start()

    def _nouvelle_generation(self, nom: str) -> int:
        """Perime tout resultat en vol pour cette tache."""
        self._generation[nom] = self._generation.get(nom, 0) + 1
        return self._generation[nom]

    def annuler(self, nom: str | None = None) -> bool:
        """Ignore le resultat d'une tache en cours (ou de toutes si nom vaut None).

        Un thread Python ne s'interrompt pas de l'exterieur : le telechargement
        deja lance va jusqu'a son terme, mais son resultat est jete et la main
        est rendue tout de suite. C'est un thread daemon, il ne survit pas au
        process.
        """
        cibles = set(self.en_cours) if nom is None else (self.en_cours & {nom})
        if not cibles:
            return False
        for cible in cibles:
            self._nouvelle_generation(cible)
        self.en_cours.difference_update(cibles)
        return True

    def _sonder(self) -> None:
        try:
            while True:
                nom, (generation, etat, charge) = self.file.get_nowait()
                if generation != self._generation.get(nom):
                    continue      # annulee, ou relancee depuis : resultat perime
                self.en_cours.discard(nom)
                succes, echec = self._rappels.get(nom, (None, None))
                if etat == "ok" and succes:
                    succes(charge)
                elif etat == "erreur":
                    (echec or _erreur_par_defaut)(charge)
        except queue.Empty:
            pass
        if not self.arret.is_set():
            self._rappel_sondage = self.racine.after(100, self._sonder)

    def fermer(self) -> None:
        """Arrete le sondage et annule le rappel deja programme.

        Sans cette annulation, le after() en attente se declenche apres
        destroy() et Tk se plaint d'un « invalid command name ..._sonder ».
        """
        self.arret.set()
        if self._rappel_sondage is not None:
            self.racine.after_cancel(self._rappel_sondage)
            self._rappel_sondage = None


def _message_erreur(erreur: Exception) -> str:
    """Message actionnable plutot qu'une trace brute."""
    if isinstance(erreur, config.CleApiManquante):
        return str(erreur)
    if isinstance(erreur, (ValueError, KeyError, RuntimeError)):
        return str(erreur).strip("'")
    if isinstance(erreur, OSError):
        return ("Probleme reseau ou disque : " + str(erreur) +
                "\nVerifie ta connexion. Les donnees deja telechargees restent utilisables.")
    return f"{type(erreur).__name__} : {erreur}\n\n{traceback.format_exc(limit=3)}"


def _erreur_par_defaut(message: str) -> None:
    messagebox.showerror("foot_edge", message)


def lire_preferences() -> dict:
    """Dernier championnat et derniere bankroll, d'une session a l'autre."""
    try:
        contenu = json.loads(CHEMIN_PREFERENCES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return contenu if isinstance(contenu, dict) else {}


def ecrire_preferences(preferences: dict) -> None:
    """Echec silencieux : perdre une preference ne doit pas empecher de fermer."""
    try:
        config.creer_dossiers()
        CHEMIN_PREFERENCES.write_text(
            json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass    # une preference perdue ne doit jamais empecher de fermer


# --------------------------------------------------------------------------
# Widgets reutilisables
# --------------------------------------------------------------------------

def tableau(parent: "tk.Misc", colonnes: tuple[str, ...],
            largeurs: dict[str, int] | None = None) -> "ttk.Treeview":
    """Treeview triable par clic sur les en-tetes, avec ses deux ascenseurs.

    L'ascenseur horizontal n'est pas un ornement : les douze colonnes des
    opportunites font 1320 px, soit plus que la fenetre par defaut. Sans lui,
    la colonne « statut » — celle qui porte le motif de rejet — reste hors
    d'atteinte.
    """
    vue = ttk.Treeview(parent, columns=colonnes, show="headings", height=16)
    largeurs = largeurs or {}
    for colonne in colonnes:
        vue.heading(colonne, text=colonne,
                    command=lambda c=colonne: _trier(vue, c, False))
        vue.column(colonne, width=largeurs.get(colonne, 90), anchor="center",
                   stretch=False)
    vertical = ttk.Scrollbar(parent, orient="vertical", command=vue.yview)
    horizontal = ttk.Scrollbar(parent, orient="horizontal", command=vue.xview)
    vue.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    vue.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    return vue


def _trier(vue: "ttk.Treeview", colonne: str, decroissant: bool) -> None:
    valeurs = [(vue.set(ligne, colonne), ligne) for ligne in vue.get_children("")]

    def cle(couple: tuple[str, str]):
        texte = couple[0].replace("%", "").replace("+", "").replace(",", ".")
        try:
            return (0, float(texte))
        except ValueError:
            return (1, couple[0])

    valeurs.sort(key=cle, reverse=decroissant)
    for position, (_, ligne) in enumerate(valeurs):
        vue.move(ligne, "", position)
    vue.heading(colonne, command=lambda: _trier(vue, colonne, not decroissant))


def remplir(vue: "ttk.Treeview", lignes: list[tuple], etiquettes: list[str] | None = None) -> None:
    vue.delete(*vue.get_children())
    for index, ligne in enumerate(lignes):
        marque = (etiquettes[index],) if etiquettes else ()
        vue.insert("", "end", values=ligne, tags=marque)


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

class Application(tk.Tk):
    """Fenetre principale : sept onglets partageant un meme Contexte."""

    def __init__(self) -> None:
        super().__init__()
        self.title("foot_edge — analyse statistique football")
        self.geometry("1180x760")
        self.contexte = Contexte()
        self.travailleur = Travailleur(self)
        self.preferences = lire_preferences()
        self.bankroll = tk.DoubleVar(value=float(self.preferences.get("bankroll", 1000.0)))
        self.seuil = tk.DoubleVar(value=config.SEUIL_ESPERANCE_DEFAUT * 100)
        self.poids_melange = tk.DoubleVar(value=0.30)

        self.carnet = ttk.Notebook(self)
        self.carnet.pack(fill="both", expand=True)
        self._construire_onglets()

        self.progression = ttk.Progressbar(self, mode="indeterminate")
        self.progression.pack(fill="x", side="bottom")
        pied = ttk.Frame(self)
        pied.pack(fill="x", side="bottom")
        self.etat = tk.StringVar(value="Pret.")
        ttk.Label(pied, textvariable=self.etat, anchor="w").pack(side="left", fill="x",
                                                                expand=True)
        self.bouton_annuler = ttk.Button(pied, text="Annuler", state="disabled",
                                         command=self.annuler_tache)
        self.bouton_annuler.pack(side="right", padx=4)

        self.protocol("WM_DELETE_WINDOW", self.fermer)
        self.bind_all("<F5>", self.rafraichir_onglet_actif)
        self.after(400, self._premier_lancement)

    # -- cycle de vie ------------------------------------------------------

    def fermer(self) -> None:
        """Arrete la boucle de sondage : les threads daemon meurent avec le process."""
        self.memoriser_preferences()
        self.travailleur.fermer()
        self.destroy()

    def memoriser_preferences(self) -> None:
        """Retient le dernier championnat et la derniere bankroll."""
        try:
            preferences = {"championnat": self.championnat_choisi(),
                           "bankroll": float(self.bankroll.get()),
                           "saisons": int(self.var_saisons.get()),
                           "source": self.var_source.get()}
        except Exception:      # saisie invalide : ne doit pas empecher de fermer
            return
        ecrire_preferences(preferences)

    def _premier_lancement(self) -> None:
        marqueur = config.DOSSIER_DONNEES / ".premier_lancement_vu"
        if marqueur.exists():
            return
        messagebox.showinfo("foot_edge — a lire une fois", MESSAGE_PREMIER_LANCEMENT)
        config.creer_dossiers()
        marqueur.write_text("vu", encoding="utf-8")

    def occupe(self, message: str) -> None:
        self.etat.set(message)
        self.progression.start(12)
        self.bouton_annuler.state(["!disabled"])

    def libre(self, message: str = "Pret.") -> None:
        self.progression.stop()
        self.etat.set(message)
        self.bouton_annuler.state(["disabled"])

    def annuler_tache(self) -> None:
        """Rend la main immediatement : le resultat du calcul en cours est ignore."""
        if self.travailleur.annuler():
            self.libre("Calcul annule (le telechargement deja lance se termine en fond).")
        else:
            self.libre()

    def _construire_onglets(self) -> None:
        self.onglet_donnees = self._ajouter_onglet("Donnees", self._construire_donnees)
        self.onglet_match = self._ajouter_onglet("Un match", self._construire_match)
        self.onglet_journee = self._ajouter_onglet("Journee", self._construire_journee)
        self.onglet_forces = self._ajouter_onglet("Forces", self._construire_forces)
        self.onglet_opportunites = self._ajouter_onglet("Opportunites",
                                                        self._construire_opportunites)
        self.onglet_journal = self._ajouter_onglet("Mon journal", self._construire_journal)
        self.onglet_ajustements = self._ajouter_onglet("Ajustements",
                                                       self._construire_ajustements)

    def _ajouter_onglet(self, titre: str, constructeur: Callable[["ttk.Frame"], None]) -> "ttk.Frame":
        cadre = ttk.Frame(self.carnet)
        self.carnet.add(cadre, text=titre)
        constructeur(cadre)
        return cadre

    def activer_onglet(self, cadre: "ttk.Frame") -> None:
        """Bascule sur un onglet par programme (apres l'enregistrement d'un pari)."""
        self.carnet.select(cadre)

    def rafraichir_onglet_actif(self, _evenement: object = None) -> None:
        """F5 : relance l'action de rafraichissement de l'onglet visible."""
        actions = {
            str(self.onglet_journee): self.calculer_journee,
            str(self.onglet_forces): self.afficher_forces,
            str(self.onglet_opportunites): self.rafraichir_cotes,
            str(self.onglet_journal): self.rafraichir_journal,
        }
        action = actions.get(str(self.carnet.select()))
        if action is not None:
            action()

    # ------------------------------------------------------------------
    # Onglet 1 — Donnees
    # ------------------------------------------------------------------

    def _construire_donnees(self, cadre: "ttk.Frame") -> None:
        barre = ttk.Frame(cadre)
        barre.pack(fill="x", padx=8, pady=8)

        ttk.Label(barre, text="Championnat").grid(row=0, column=0, padx=4)
        choix = [f"{code} — {info['nom']}" for code, info in config.CHAMPIONNATS.items()]
        self.liste_championnats = ttk.Combobox(barre, values=choix, width=28, state="readonly")
        memorise = self.preferences.get("championnat", "F1")
        codes = list(config.CHAMPIONNATS)
        self.liste_championnats.current(codes.index(memorise) if memorise in codes
                                        else codes.index("F1"))
        self.liste_championnats.grid(row=0, column=1, padx=4)

        ttk.Label(barre, text="Saisons").grid(row=0, column=2, padx=4)
        self.var_saisons = tk.IntVar(value=int(self.preferences.get("saisons", 4)))
        ttk.Spinbox(barre, from_=1, to=10, textvariable=self.var_saisons,
                    width=5).grid(row=0, column=3, padx=4)

        ttk.Label(barre, text="Source").grid(row=0, column=4, padx=4)
        self.var_source = tk.StringVar(value=self.preferences.get("source", "buts"))
        ttk.Combobox(barre, values=["buts", "xg", "melange"], textvariable=self.var_source,
                     width=10, state="readonly").grid(row=0, column=5, padx=4)

        ttk.Button(barre, text="Charger", command=self.charger_donnees).grid(
            row=0, column=6, padx=8)
        ttk.Button(barre, text="Verifier les noms d'equipes",
                   command=self.verifier_noms).grid(row=0, column=7, padx=4)

        self.zone_journal = tk.Text(cadre, height=26, wrap="word")
        self.zone_journal.pack(fill="both", expand=True, padx=8, pady=8)
        self.tracer("Choisis un championnat puis clique sur Charger.\n"
                    "Le telechargement se fait en arriere-plan : l'interface reste utilisable.\n")

    def tracer(self, texte: str) -> None:
        self.zone_journal.insert("end", texte)
        self.zone_journal.see("end")

    def championnat_choisi(self) -> str:
        return self.liste_championnats.get().split(" — ")[0]

    def charger_donnees(self) -> None:
        championnat = self.championnat_choisi()
        saisons = int(self.var_saisons.get())
        source = self.var_source.get()
        self.occupe(f"Chargement de {config.nom_championnat(championnat)}...")
        self.tracer(f"\n--- {config.nom_championnat(championnat)} "
                    f"({saisons} saisons, source {source}) ---\n")

        def tache() -> dict:
            if source == "buts":
                matchs = resultats_module.charger(championnat, saisons)
                rapport = None
            else:
                matchs, rapport = xg_module.charger_avec_xg(championnat, saisons)
            if matchs.empty:
                raise ValueError(
                    f"Aucune donnee pour {championnat}. Verifie ta connexion internet ; "
                    "les saisons deja telechargees restent utilisables hors ligne.")
            modele = entrainer_modele(matchs, source, championnat)
            return {"matchs": matchs, "modele": modele, "rapport": rapport,
                    "championnat": championnat}

        self.travailleur.lancer("charger", tache, self._donnees_chargees, self._echec_chargement)

    def _donnees_chargees(self, resultat: dict) -> None:
        self.contexte.championnat = resultat["championnat"]
        self.contexte.matchs = resultat["matchs"]
        self.contexte.modele = resultat["modele"]
        self.contexte.rapport_xg = resultat["rapport"]
        matchs = resultat["matchs"]
        equipes = resultats_module.equipes(matchs)
        self.tracer(f"{len(matchs)} matchs, {len(equipes)} equipes, "
                    f"saisons {', '.join(sorted(matchs['saison'].unique()))}\n")
        if resultat["rapport"]:
            self.tracer(xg_module.afficher_rapport(resultat["rapport"],
                                                   resultat["championnat"]) + "\n")
        taux = noms_module.taux_appariement(equipes)
        self.tracer(f"Noms d'equipes reconnus : {taux:.0%}\n")
        if taux < 0.95:
            self.tracer("  Certaines equipes ne sont pas dans la table de correspondance : "
                        "utilise le bouton 'Verifier les noms d'equipes'.\n")
        modele = resultat["modele"]
        self.tracer(f"Modele entraine : gamma {modele.gamma:.3f}, rho {modele.rho:.3f}, "
                    f"convergence {'oui' if modele.convergence else 'NON'}\n")
        self._rafraichir_dependants(equipes)
        self.libre(f"{config.nom_championnat(resultat['championnat'])} : "
                   f"{len(matchs)} matchs charges.")

    def _echec_chargement(self, message: str) -> None:
        self.libre("Echec du chargement.")
        self.tracer(f"ERREUR : {message}\n")
        messagebox.showwarning("Chargement impossible", message)

    def _rafraichir_dependants(self, equipes: list[str]) -> None:
        for liste in (self.liste_dom, self.liste_ext, self.liste_ajustement):
            liste["values"] = equipes
            if equipes and not liste.get():
                liste.set(equipes[0])
        self.afficher_forces()

    def verifier_noms(self) -> None:
        if self.contexte.matchs is None:
            messagebox.showinfo("foot_edge", "Charge d'abord un championnat.")
            return
        equipes = resultats_module.equipes(self.contexte.matchs)
        inconnues = [e for e in equipes if noms_module.canoniser(e, "football_data") is None]
        if not inconnues:
            self.tracer("Toutes les equipes sont dans la table de correspondance.\n")
            return
        self.tracer("Equipes absentes de data/noms_equipes.json :\n")
        for equipe in inconnues:
            proches = ", ".join(n for n, _ in noms_module.suggerer(equipe, noms_module.noms_canoniques(), 3))
            self.tracer(f"  {equipe}  (proches : {proches})\n")
        self.tracer("  Complete le fichier avec : "
                    f"python outils/verifier_noms.py --championnat {self.contexte.championnat}\n")

    # ------------------------------------------------------------------
    # Onglet 2 — Un match
    # ------------------------------------------------------------------

    def _construire_match(self, cadre: "ttk.Frame") -> None:
        barre = ttk.Frame(cadre)
        barre.pack(fill="x", padx=8, pady=8)
        ttk.Label(barre, text="Domicile").grid(row=0, column=0, padx=4)
        self.liste_dom = ttk.Combobox(barre, width=22, state="readonly")
        self.liste_dom.grid(row=0, column=1, padx=4)
        ttk.Label(barre, text="Exterieur").grid(row=0, column=2, padx=4)
        self.liste_ext = ttk.Combobox(barre, width=22, state="readonly")
        self.liste_ext.grid(row=0, column=3, padx=4)

        ttk.Label(barre, text="Cotes 1 / N / 2").grid(row=0, column=4, padx=(16, 4))
        self.cotes_saisies = []
        for i in range(3):
            champ = ttk.Entry(barre, width=6)
            champ.grid(row=0, column=5 + i, padx=2)
            self.cotes_saisies.append(champ)
        ttk.Button(barre, text="Analyser", command=self.analyser_match).grid(
            row=0, column=8, padx=10)

        self.zone_match = tk.Text(cadre, wrap="word")
        self.zone_match.pack(fill="both", expand=True, padx=8, pady=8)

    def analyser_match(self) -> None:
        if not self.contexte.pret():
            messagebox.showinfo("foot_edge", "Charge d'abord un championnat (onglet Donnees).")
            return
        dom, ext = self.liste_dom.get(), self.liste_ext.get()
        if dom == ext:
            messagebox.showinfo("foot_edge", "Choisis deux equipes differentes.")
            return
        prediction = self.contexte.modele.predire(dom, ext)
        self.zone_match.delete("1.0", "end")
        self.zone_match.insert("end", self._texte_match(prediction, dom, ext))

    def _texte_match(self, prediction: dict, dom: str, ext: str) -> str:
        p = prediction["1N2"]
        lignes = [
            f"{dom} - {ext}",
            f"Buts attendus : {prediction['lambda_dom']:.2f} - {prediction['lambda_ext']:.2f}",
            "",
            f"1  {p['1']:6.1%}     N  {p['N']:6.1%}     2  {p['2']:6.1%}",
            f"Double chance : 1N {prediction['double_chance']['1N']:.1%}   "
            f"12 {prediction['double_chance']['12']:.1%}   "
            f"N2 {prediction['double_chance']['N2']:.1%}",
            "",
            "Totaux de buts :",
        ]
        for seuil in (0.5, 1.5, 2.5, 3.5, 4.5):
            lignes.append(f"   +{seuil} : {prediction['totaux'][f'plus_{seuil}']:6.1%}   "
                          f"-{seuil} : {prediction['totaux'][f'moins_{seuil}']:6.1%}")
        lignes += [
            "",
            f"BTTS oui {prediction['btts']['oui']:.1%} / non {prediction['btts']['non']:.1%}",
            f"Clean sheet : {dom} {prediction['clean_sheet']['dom']:.1%}   "
            f"{ext} {prediction['clean_sheet']['ext']:.1%}",
            "",
            "Scores les plus probables :",
        ]
        lignes += [f"   {score}  {proba:.1%}" for score, proba in prediction["scores_probables"][:8]]
        lignes += ["", "Handicap asiatique (point de vue domicile) :"]
        for handicap, valeurs in prediction["handicap_asiatique"].items():
            lignes.append(f"   {handicap} : dom {valeurs['dom']:.1%}  ext {valeurs['ext']:.1%}"
                          + (f"  remb. {valeurs['remboursement']:.1%}"
                             if valeurs["remboursement"] > 1e-6 else ""))
        lignes += self._comparaison_cotes(p)
        lignes += self._confrontations(dom, ext)
        lignes += ["", config.AVERTISSEMENT_LEGAL]
        return "\n".join(lignes)

    def _comparaison_cotes(self, probas: dict[str, float]) -> list[str]:
        saisies = []
        for champ in self.cotes_saisies:
            try:
                saisies.append(float(champ.get().replace(",", ".")))
            except ValueError:
                return []
        try:
            marche = calibration.deviguer(saisies)
        except ValueError as erreur:
            return ["", f"Cotes saisies inutilisables : {erreur}"]
        lignes = ["", f"Comparaison au marche (surcote {calibration.surcote(saisies):.1%}) :"]
        for i, selection in enumerate(("1", "N", "2")):
            esperance = probas[selection] * saisies[i] - 1
            lignes.append(f"   {selection} @ {saisies[i]:.2f} : marche {marche[i]:.1%} | "
                          f"modele {probas[selection]:.1%} | esperance {esperance:+.1%}")
        return lignes

    def _confrontations(self, dom: str, ext: str) -> list[str]:
        matchs = self.contexte.matchs
        if matchs is None:
            return []
        directes = matchs[((matchs["dom"] == dom) & (matchs["ext"] == ext))
                          | ((matchs["dom"] == ext) & (matchs["ext"] == dom))]
        if directes.empty:
            return ["", "Aucune confrontation directe dans l'historique charge."]
        lignes = ["", "Confrontations directes :"]
        for ligne in directes.tail(6).itertuples():
            lignes.append(f"   {ligne.date:%d/%m/%Y}  {ligne.dom} {ligne.buts_dom:.0f}-"
                          f"{ligne.buts_ext:.0f} {ligne.ext}")
        return lignes

    # ------------------------------------------------------------------
    # Onglet 3 — Journee
    # ------------------------------------------------------------------

    _COLONNES_JOURNEE = ("dom", "ext", "1", "N", "2", "+2.5", "+3.5",
                         "-2.5", "BTTS", "buts attendus")

    def _construire_journee(self, cadre: "ttk.Frame") -> None:
        barre = ttk.Frame(cadre)
        barre.pack(fill="x", padx=8, pady=8)
        ttk.Label(barre, text="Trier par").pack(side="left", padx=4)
        self.var_filtre = tk.StringVar(value="favori")
        ttk.Combobox(barre, textvariable=self.var_filtre, width=12, state="readonly",
                     values=["favori", "+2.5", "+3.5", "-2.5", "BTTS"]).pack(side="left")
        self.var_a_venir = tk.BooleanVar(value=False)
        ttk.Checkbutton(barre, text="Seulement les matchs a venir",
                        variable=self.var_a_venir).pack(side="left", padx=12)
        ttk.Button(barre, text="Calculer",
                   command=self.calculer_journee).pack(side="left", padx=10)
        ttk.Button(barre, text="Exporter en CSV",
                   command=self.exporter_journee).pack(side="left")

        conteneur = ttk.Frame(cadre)
        conteneur.pack(fill="both", expand=True, padx=8, pady=8)
        self.vue_journee = tableau(conteneur, self._COLONNES_JOURNEE,
                                   {"dom": 150, "ext": 150})

    def affiches_a_venir(self) -> list[tuple[str, str]]:
        """Vraies affiches du flux de cotes, ramenees aux noms du modele.

        Aucune requete supplementaire : on relit les cotes deja recuperees.
        """
        cotes = self.contexte.cotes
        if cotes is None or cotes.empty:
            return []
        equipes = self.contexte.modele.equipes
        couples = []
        for ligne in cotes_module.matchs_a_venir(cotes).itertuples():
            dom = noms_module.resoudre_vers(ligne.dom, equipes)
            ext = noms_module.resoudre_vers(ligne.ext, equipes)
            if dom is not None and ext is not None and dom != ext:
                couples.append((dom, ext))
        return couples

    def calculer_journee(self) -> None:
        if not self.contexte.pret():
            messagebox.showinfo("foot_edge", "Charge d'abord un championnat.")
            return
        modele = self.contexte.modele
        tri = self.var_filtre.get()
        couples = None
        if self.var_a_venir.get():
            couples = self.affiches_a_venir()
            if not couples:
                messagebox.showinfo(
                    "foot_edge",
                    "Aucun match a venir connu : utilise d'abord 'Rafraichir les cotes' "
                    "dans l'onglet Opportunites. Les affiches viennent de ce flux.")
                return
        self.occupe("Calcul des confrontations...")

        def tache() -> list[dict]:
            duels = couples if couples is not None else [
                (dom, ext) for dom in modele.equipes for ext in modele.equipes
                if dom != ext]
            lignes = []
            for dom, ext in duels:
                p = modele.predire(dom, ext)
                lignes.append({
                    "dom": dom, "ext": ext,
                    "1": p["1N2"]["1"], "N": p["1N2"]["N"], "2": p["1N2"]["2"],
                    "+2.5": p["totaux"]["plus_2.5"], "+3.5": p["totaux"]["plus_3.5"],
                    "-2.5": p["totaux"]["moins_2.5"], "BTTS": p["btts"]["oui"],
                    "buts attendus": p["buts_attendus"]["total"],
                })
            cle = {"favori": "1"}.get(tri, tri)
            return sorted(lignes, key=lambda l: l[cle], reverse=True)

        self.travailleur.lancer("journee", tache, self._journee_calculee)

    def _journee_calculee(self, lignes: list[dict]) -> None:
        self.lignes_journee = lignes
        remplir(self.vue_journee, [
            (l["dom"], l["ext"], f"{l['1']:.1%}", f"{l['N']:.1%}", f"{l['2']:.1%}",
             f"{l['+2.5']:.1%}", f"{l['+3.5']:.1%}", f"{l['-2.5']:.1%}",
             f"{l['BTTS']:.1%}", f"{l['buts attendus']:.2f}")
            for l in lignes])
        precision = "matchs a venir" if self.var_a_venir.get() else "confrontations"
        self.libre(f"{len(lignes)} {precision} calcules.")

    def exporter_journee(self) -> None:
        if not getattr(self, "lignes_journee", None):
            messagebox.showinfo("foot_edge", "Calcule d'abord les confrontations.")
            return
        chemin = filedialog.asksaveasfilename(defaultextension=".csv",
                                              filetypes=[("CSV", "*.csv")])
        if not chemin:
            return
        import pandas as pd
        pd.DataFrame(self.lignes_journee).to_csv(chemin, index=False)
        self.libre(f"Exporte vers {chemin}")

    # ------------------------------------------------------------------
    # Onglet 4 — Forces
    # ------------------------------------------------------------------

    def _construire_forces(self, cadre: "ttk.Frame") -> None:
        ttk.Button(cadre, text="Rafraichir", command=self.afficher_forces).pack(
            anchor="w", padx=8, pady=8)
        conteneur = ttk.Frame(cadre)
        conteneur.pack(fill="both", expand=True, padx=8, pady=8)
        self.vue_forces = tableau(conteneur, ("equipe", "attaque", "defense", "note",
                                              "forme 6 matchs", "buts marques",
                                              "buts encaisses", "tirs/match",
                                              "tirs cadres/match", "corners/match"),
                                  {"equipe": 160, "forme 6 matchs": 110,
                                   "tirs cadres/match": 120})

    def afficher_forces(self) -> None:
        if not self.contexte.pret():
            return
        forces = self.contexte.modele.forces()
        matchs = self.contexte.matchs
        lignes = []
        for ligne in forces.itertuples():
            forme, marques, encaisses = self._forme(matchs, ligne.equipe)
            tirs, cadres, corners = self._stats_brutes(matchs, ligne.equipe)
            lignes.append((ligne.equipe, f"{ligne.attaque:.2f}", f"{ligne.defense:.2f}",
                           f"{ligne.note:.2f}", forme, marques, encaisses,
                           tirs, cadres, corners))
        remplir(self.vue_forces, lignes)

    @staticmethod
    def _forme(matchs, equipe: str) -> tuple[str, int, int]:
        """Forme sur les 6 derniers matchs, du plus ancien au plus recent."""
        joues = matchs[(matchs["dom"] == equipe) | (matchs["ext"] == equipe)].tail(6)
        lettres, marques, encaisses = [], 0, 0
        for ligne in joues.itertuples():
            a_domicile = ligne.dom == equipe
            pour = ligne.buts_dom if a_domicile else ligne.buts_ext
            contre = ligne.buts_ext if a_domicile else ligne.buts_dom
            marques += int(pour)
            encaisses += int(contre)
            lettres.append("V" if pour > contre else ("N" if pour == contre else "D"))
        return "".join(lettres), marques, encaisses

    @staticmethod
    def _stats_brutes(matchs, equipe: str) -> tuple[str, str, str]:
        """Tirs, tirs cadres et corners par rencontre, sur l'historique charge.

        Un tiret quand la colonne est absente du fichier source : football-data
        ne fournit ces statistiques que pour une partie des championnats.
        """
        joues = matchs[(matchs["dom"] == equipe) | (matchs["ext"] == equipe)]
        a_domicile = joues["dom"] == equipe
        moyennes = []
        for base in ("tirs", "tirs_cadres", "corners"):
            colonnes = (f"{base}_dom", f"{base}_ext")
            if any(colonne not in joues.columns for colonne in colonnes):
                moyennes.append("-")
                continue
            valeurs = joues[colonnes[0]].where(a_domicile, joues[colonnes[1]]).dropna()
            moyennes.append(f"{valeurs.mean():.1f}" if len(valeurs) else "-")
        return moyennes[0], moyennes[1], moyennes[2]

    # ------------------------------------------------------------------
    # Onglet 5 — Opportunites
    # ------------------------------------------------------------------

    _COLONNES_OPPORTUNITES = ("date", "match", "marche", "selection", "p modele",
                              "p marche", "ecart", "cote", "bookmaker",
                              "esperance", "mise", "statut")

    def _construire_opportunites(self, cadre: "ttk.Frame") -> None:
        barre = ttk.Frame(cadre)
        barre.pack(fill="x", padx=8, pady=8)
        ttk.Button(barre, text="Rafraichir les cotes",
                   command=self.rafraichir_cotes).pack(side="left")
        self.var_quota = tk.StringVar(value=cotes_module.message_quota().splitlines()[0])
        ttk.Label(barre, textvariable=self.var_quota).pack(side="left", padx=12)
        ttk.Button(barre, text="Enregistrer ce pari",
                   command=self.enregistrer_pari).pack(side="right")

        conteneur = ttk.Frame(cadre)
        conteneur.pack(fill="both", expand=True, padx=8, pady=4)
        self.vue_opportunites = tableau(conteneur, self._COLONNES_OPPORTUNITES,
                                        {"match": 220, "marche": 110, "bookmaker": 120,
                                         "statut": 150})
        self.vue_opportunites.tag_configure("alerte", background=COULEUR_ALERTE)
        self.vue_opportunites.tag_configure("retenue", background=COULEUR_RETENUE)
        self.vue_opportunites.bind("<<TreeviewSelect>>", self._detail_opportunite)

        self.zone_detail = tk.Text(cadre, height=5, wrap="word")
        self.zone_detail.pack(fill="x", padx=8)

        self._construire_bandeau(cadre)

    def _construire_bandeau(self, cadre: "ttk.Frame") -> None:
        """Bandeau permanent : bankroll, exposition, rappel d'incertitude.

        Il n'a volontairement aucun bouton de fermeture ni de repli.
        """
        bandeau = ttk.Frame(cadre, relief="ridge", borderwidth=2)
        bandeau.pack(fill="x", side="bottom", padx=8, pady=8)
        self.var_bandeau = tk.StringVar()
        ttk.Label(bandeau, textvariable=self.var_bandeau,
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=8, pady=(6, 0))
        ttk.Label(bandeau, wraplength=1100, justify="left",
                  text="Les probabilites affichees sont des estimations assorties d'une marge "
                       "d'erreur reelle. Une ligne surlignee en orange signale un ecart "
                       "important entre le modele et le marche : c'est un signal d'alerte "
                       "(information manquante cote modele), pas une recommandation. "
                       "Mise plafonnee a "
                       f"{config.PLAFOND_MISE_BANKROLL:.0%} de la bankroll, Kelly divise par 4."
                  ).pack(anchor="w", padx=8, pady=(0, 6))
        self.rafraichir_bandeau()

    def rafraichir_bandeau(self) -> None:
        exposition = journal_module.exposition()
        bankroll = self.bankroll.get()
        part = exposition / bankroll if bankroll else 0.0
        self.var_bandeau.set(
            f"Bankroll : {bankroll:.2f}   |   Exposition en cours : {exposition:.2f} "
            f"({part:.1%})   |   Mise maximale par pari : "
            f"{bankroll * config.PLAFOND_MISE_BANKROLL:.2f}")

    def rafraichir_cotes(self) -> None:
        if not self.contexte.pret():
            messagebox.showinfo("foot_edge", "Charge d'abord un championnat (onglet Donnees).")
            return
        championnat = self.contexte.championnat
        modele = self.contexte.modele
        bankroll = self.bankroll.get()
        seuil = self.seuil.get() / 100.0
        poids = self.poids_melange.get()
        self.occupe("Recuperation des cotes...")

        def tache() -> dict:
            cotes = cotes_module.recuperer(championnat)
            opportunites = valeur_module.detecter(cotes, modele, bankroll,
                                                  a_melange=poids, seuil_esperance=seuil)
            return {"cotes": cotes, "opportunites": opportunites,
                    "quota": cotes_module.message_quota()}

        self.travailleur.lancer("cotes", tache, self._cotes_recuperees, self._echec_cotes)

    def _echec_cotes(self, message: str) -> None:
        self.libre("Cotes indisponibles.")
        messagebox.showwarning("Cotes indisponibles", message)

    def _cotes_recuperees(self, resultat: dict) -> None:
        self.contexte.cotes = resultat["cotes"]
        self.contexte.opportunites = resultat["opportunites"]
        self.var_quota.set(resultat["quota"].splitlines()[0])
        lignes, etiquettes = [], []
        for o in resultat["opportunites"]:
            statut = "retenue" if o.recommandee else (
                "divergence" if o.alerte else (o.raisons_rejet[0][:28] if o.raisons_rejet else ""))
            lignes.append((
                str(o.date)[:16], f"{o.dom} - {o.ext}", o.marche, o.selection,
                f"{o.p_modele:.1%}", f"{o.p_marche:.1%}", f"{o.ecart_modele_marche:.1%}",
                f"{o.cote:.2f}", o.bookmaker, f"{o.esperance:+.1%}",
                f"{o.mise:.2f}" if o.mise else "-", statut))
            etiquettes.append("alerte" if o.alerte else ("retenue" if o.recommandee else ""))
        remplir(self.vue_opportunites, lignes, etiquettes)
        self.rafraichir_bandeau()
        retenues = sum(1 for o in resultat["opportunites"] if o.recommandee)
        self.libre(f"{len(resultat['opportunites'])} selections analysees, "
                   f"{retenues} passent les quatre conditions.")

    def _opportunite_selectionnee(self) -> valeur_module.Opportunite | None:
        selection = self.vue_opportunites.selection()
        if not selection or not self.contexte.opportunites:
            return None
        index = self.vue_opportunites.index(selection[0])
        enfants = self.vue_opportunites.get_children("")
        valeurs = self.vue_opportunites.item(enfants[index])["values"]
        for o in self.contexte.opportunites:
            if (f"{o.dom} - {o.ext}" == valeurs[1] and o.marche == valeurs[2]
                    and o.selection == str(valeurs[3])):
                return o
        return None

    def _detail_opportunite(self, _evenement: object) -> None:
        opportunite = self._opportunite_selectionnee()
        self.zone_detail.delete("1.0", "end")
        if opportunite is None:
            return
        texte = opportunite.resume()
        if opportunite.alerte:
            texte += f"\nALERTE : {opportunite.alerte}"
        self.zone_detail.insert("end", texte)

    def enregistrer_pari(self) -> None:
        opportunite = self._opportunite_selectionnee()
        if opportunite is None:
            messagebox.showinfo("foot_edge", "Selectionne une ligne du tableau.")
            return
        mise = opportunite.mise
        if mise <= 0:
            if not messagebox.askyesno(
                    "foot_edge",
                    "Cette selection ne passe pas les quatre conditions :\n"
                    + " ; ".join(opportunite.raisons_rejet)
                    + "\n\nL'enregistrer quand meme, a la mise plafond ?"):
                return
            mise = round(self.bankroll.get() * config.PLAFOND_MISE_BANKROLL, 2)
        identifiant = journal_module.enregistrer(
            dom=opportunite.dom, ext=opportunite.ext, marche=opportunite.marche,
            selection=opportunite.selection, cote_prise=opportunite.cote, mise=mise,
            date_match=str(opportunite.date), championnat=self.contexte.championnat,
            bookmaker=opportunite.bookmaker, p_modele=opportunite.p_modele,
            p_marche=opportunite.p_marche, p_finale=opportunite.p_finale,
            esperance=opportunite.esperance)
        self.rafraichir_journal()
        self.rafraichir_bandeau()
        self.libre(f"Pari #{identifiant} enregistre (mise {mise:.2f}).")
        self.activer_onglet(self.onglet_journal)
        messagebox.showinfo("foot_edge", f"Pari #{identifiant} ajoute a ton journal.")

    # ------------------------------------------------------------------
    # Onglet 6 — Mon journal
    # ------------------------------------------------------------------

    _COLONNES_JOURNAL = ("id", "date match", "match", "marche", "selection",
                         "cote", "mise", "statut", "gain", "cote cloture", "CLV")

    def _construire_journal(self, cadre: "ttk.Frame") -> None:
        barre = ttk.Frame(cadre)
        barre.pack(fill="x", padx=8, pady=8)
        ttk.Button(barre, text="Rafraichir", command=self.rafraichir_journal).pack(side="left")
        for libelle, resultat in (("Gagne", "gagne"), ("Perdu", "perdu"),
                                  ("Rembourse", "rembourse")):
            ttk.Button(barre, text=f"Marquer {libelle.lower()}",
                       command=lambda r=resultat: self.regler_selection(r)).pack(
                side="left", padx=4)
        ttk.Button(barre, text="Capturer les cotes de cloture",
                   command=self.capturer_clotures).pack(side="left", padx=12)
        ttk.Button(barre, text="Exporter en CSV",
                   command=self.exporter_journal).pack(side="left")
        ttk.Button(barre, text="Courbe de bankroll",
                   command=self.tracer_bankroll).pack(side="left", padx=4)

        conteneur = ttk.Frame(cadre)
        conteneur.pack(fill="both", expand=True, padx=8, pady=4)
        self.vue_paris = tableau(conteneur, self._COLONNES_JOURNAL,
                                 {"id": 40, "match": 200, "marche": 110})

        self.zone_bilan = tk.Text(cadre, height=14, wrap="word")
        self.zone_bilan.pack(fill="x", padx=8, pady=8)
        self.rafraichir_journal()

    def rafraichir_journal(self) -> None:
        paris = journal_module.lister()
        lignes = []
        for pari in paris.itertuples():
            clv = f"{pari.clv:+.2%}" if pari.clv is not None and pari.clv == pari.clv else "-"
            cloture = (f"{pari.cote_cloture:.2f}" if pari.cote_cloture is not None
                       and pari.cote_cloture == pari.cote_cloture else "-")
            lignes.append((pari.id, str(pari.date_match)[:10], f"{pari.dom} - {pari.ext}",
                           pari.marche, pari.selection, f"{pari.cote_prise:.2f}",
                           f"{pari.mise:.2f}", pari.statut, f"{pari.gain:+.2f}", cloture, clv))
        remplir(self.vue_paris, lignes)
        self.zone_bilan.delete("1.0", "end")
        self.zone_bilan.insert("end", journal_module.texte_bilan())
        self.rafraichir_bandeau()

    def _identifiant_selectionne(self) -> int | None:
        selection = self.vue_paris.selection()
        if not selection:
            messagebox.showinfo("foot_edge", "Selectionne un pari dans la liste.")
            return None
        return int(self.vue_paris.item(selection[0])["values"][0])

    def regler_selection(self, resultat: str) -> None:
        identifiant = self._identifiant_selectionne()
        if identifiant is None:
            return
        try:
            journal_module.regler(identifiant, resultat)
        except (KeyError, ValueError) as erreur:
            messagebox.showwarning("foot_edge", str(erreur))
            return
        self.rafraichir_journal()

    def capturer_clotures(self) -> None:
        self.occupe("Capture des cotes de cloture...")

        def tache() -> dict:
            return journal_module.capturer_clotures()

        def fini(resultat: dict) -> None:
            self.rafraichir_journal()
            self.libre(f"Cotes de cloture capturees pour {resultat['traites']} pari(s).")
            if resultat.get("sans_donnees"):
                messagebox.showinfo(
                    "foot_edge",
                    f"{len(resultat['sans_donnees'])} pari(s) sans cote de cloture.\n"
                    "Les cotes doivent avoir ete recuperees AVANT le coup d'envoi : "
                    "elles ne sont pas recuperables apres coup.")

        self.travailleur.lancer("clotures", tache, fini)

    def exporter_journal(self) -> None:
        chemin = filedialog.asksaveasfilename(defaultextension=".csv",
                                              filetypes=[("CSV", "*.csv")])
        if chemin:
            journal_module.exporter_csv(Path(chemin))
            self.libre(f"Journal exporte vers {chemin}")

    def tracer_bankroll(self) -> None:
        courbe = journal_module.courbe_bankroll(self.bankroll.get())
        if courbe.empty:
            messagebox.showinfo("foot_edge", "Aucun pari solde : pas encore de courbe.")
            return
        chemin = config.DOSSIER_RAPPORTS / "bankroll_journal.png"
        trace = backtest_module.tracer_bankroll(
            courbe, self.bankroll.get(), chemin, "(journal)")
        if trace:
            messagebox.showinfo("foot_edge", f"Courbe enregistree :\n{trace}")
        else:
            messagebox.showinfo(
                "foot_edge",
                "Graphique non genere : matplotlib est absent, ou son rendu a "
                "echoue sur cette installation. Le journal, le bilan et le CLV "
                "restent complets : seule l'image manque.")

    # ------------------------------------------------------------------
    # Onglet 7 — Ajustements
    # ------------------------------------------------------------------

    def _construire_ajustements(self, cadre: "ttk.Frame") -> None:
        reglages = ttk.LabelFrame(cadre, text="Reglages de mise")
        reglages.pack(fill="x", padx=8, pady=8)
        ttk.Label(reglages, text="Bankroll").grid(row=0, column=0, padx=6, pady=6)
        ttk.Entry(reglages, textvariable=self.bankroll, width=12).grid(row=0, column=1)
        ttk.Label(reglages, text="Seuil d'esperance (%)").grid(row=0, column=2, padx=6)
        ttk.Entry(reglages, textvariable=self.seuil, width=8).grid(row=0, column=3)
        ttk.Label(reglages, text="Poids du modele dans le melange").grid(row=0, column=4, padx=6)
        ttk.Entry(reglages, textvariable=self.poids_melange, width=8).grid(row=0, column=5)
        ttk.Button(reglages, text="Appliquer",
                   command=self.rafraichir_bandeau).grid(row=0, column=6, padx=10)
        ttk.Label(reglages, text=f"Plafond de mise fixe a "
                                 f"{config.PLAFOND_MISE_BANKROLL:.0%} de la bankroll "
                                 "(non modifiable)").grid(row=1, column=0, columnspan=7,
                                                          sticky="w", padx=6, pady=(0, 6))

        corrections = ttk.LabelFrame(
            cadre, text="Correction manuelle d'une equipe "
                        "(blessure, transfert, turnover avant coupe d'Europe)")
        corrections.pack(fill="x", padx=8, pady=8)
        ttk.Label(corrections, text="Equipe").grid(row=0, column=0, padx=6, pady=6)
        self.liste_ajustement = ttk.Combobox(corrections, width=24, state="readonly")
        self.liste_ajustement.grid(row=0, column=1)
        ttk.Label(corrections, text="Attaque %").grid(row=0, column=2, padx=6)
        self.var_att = tk.DoubleVar(value=0.0)
        ttk.Entry(corrections, textvariable=self.var_att, width=8).grid(row=0, column=3)
        ttk.Label(corrections, text="Defense %").grid(row=0, column=4, padx=6)
        self.var_def = tk.DoubleVar(value=0.0)
        ttk.Entry(corrections, textvariable=self.var_def, width=8).grid(row=0, column=5)
        ttk.Button(corrections, text="Appliquer",
                   command=self.appliquer_ajustement).grid(row=0, column=6, padx=8)
        ttk.Button(corrections, text="Tout reinitialiser",
                   command=self.reinitialiser_ajustements).grid(row=0, column=7)

        analyse = ttk.LabelFrame(cadre, text="Backtest")
        analyse.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(analyse, text="Lancer un backtest walk-forward",
                   command=self.lancer_backtest).pack(anchor="w", padx=8, pady=6)
        # la bande de graphiques est packee en premier : elle reserve ainsi la
        # hauteur dont elle a besoin, et c'est le rapport qui prend le reste
        self.cadre_graphiques = ttk.Frame(analyse)
        self.cadre_graphiques.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        self._images_backtest: list = []
        self.zone_backtest = tk.Text(analyse, wrap="none", height=14)
        self.zone_backtest.pack(fill="both", expand=True, padx=8, pady=8)

    def appliquer_ajustement(self) -> None:
        if not self.contexte.pret():
            messagebox.showinfo("foot_edge", "Charge d'abord un championnat.")
            return
        equipe = self.liste_ajustement.get()
        if not equipe:
            return
        try:
            self.contexte.modele.ajuster(equipe, self.var_att.get(), self.var_def.get())
        except (ValueError, KeyError) as erreur:
            messagebox.showwarning("foot_edge", str(erreur))
            return
        self.afficher_forces()
        self.libre(f"Ajustement applique a {equipe} "
                   f"(attaque {self.var_att.get():+.0f} %, defense {self.var_def.get():+.0f} %).")

    def reinitialiser_ajustements(self) -> None:
        if self.contexte.pret():
            self.contexte.modele.reinitialiser_ajustements()
            self.afficher_forces()
            self.libre("Ajustements manuels effaces.")

    def lancer_backtest(self) -> None:
        if self.contexte.matchs is None:
            messagebox.showinfo("foot_edge", "Charge d'abord un championnat.")
            return
        matchs = self.contexte.matchs
        championnat = self.contexte.championnat
        source = self.var_source.get()
        seuil = self.seuil.get() / 100.0
        bankroll = self.bankroll.get()
        self.occupe("Backtest en cours (plusieurs minutes selon l'historique)...")
        self.zone_backtest.delete("1.0", "end")
        self.zone_backtest.insert("end", "Calcul en cours, l'interface reste utilisable...\n")

        def tache() -> dict:
            resultat = backtest_module.backtester(
                matchs, championnat=championnat, cible=source,
                seuil_esperance=seuil, bankroll_initiale=bankroll,
                frequence_reentrainement=3)
            return {"rapport": resultat.rapport, "graphiques": resultat.graphiques}

        def fini(resultat: dict) -> None:
            self.zone_backtest.delete("1.0", "end")
            self.zone_backtest.insert("end", resultat["rapport"])
            self.afficher_graphiques_backtest(resultat["graphiques"])
            self.libre("Backtest termine.")

        self.travailleur.lancer("backtest", tache, fini, self._echec_backtest)

    def afficher_graphiques_backtest(self, graphiques: dict[str, str]) -> None:
        """Montre les PNG du backtest dans l'onglet (Tk 8.6 lit le PNG nativement)."""
        for enfant in self.cadre_graphiques.winfo_children():
            enfant.destroy()
        self._images_backtest = []
        for nom, chemin in (graphiques or {}).items():
            bloc = ttk.Frame(self.cadre_graphiques)
            bloc.pack(side="left", padx=6)
            ttk.Label(bloc, text=nom).pack(anchor="w")
            try:
                image = tk.PhotoImage(file=chemin)
                # deux graphiques cote a cote doivent tenir dans la fenetre :
                # 7 pouces a 110 dpi font 770 px de large, on vise la moitie
                image = image.subsample(max(1, round(image.width() / 380)))
            except Exception:   # Tk 8.5 ne lit pas le PNG, fichier absent, ...
                ttk.Label(bloc, text=f"{chemin}\n(affichage impossible : "
                                     "Tk 8.6 est requis pour lire un PNG)").pack(anchor="w")
                continue
            self._images_backtest.append(image)
            ttk.Label(bloc, image=image).pack()

    def _echec_backtest(self, message: str) -> None:
        self.libre("Backtest impossible.")
        self.zone_backtest.delete("1.0", "end")
        self.zone_backtest.insert("end", message)


def main() -> int:
    Application().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
