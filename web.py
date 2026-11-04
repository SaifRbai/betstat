#!/usr/bin/env python3
"""Interface web de consultation : les matchs a venir et leurs estimations.

Un serveur local ecrit avec la seule bibliotheque standard — TACHES.txt
section 3 n'autorise aucune dependance web, et une page de consultation pour
un utilisateur n'en demande pas. Il ecoute sur 127.0.0.1 : rien n'est expose
au reseau.

Deux caches, pour deux raisons differentes :
  - le modele est entraine une fois par championnat puis garde en memoire,
    sinon chaque rechargement couterait quelques secondes de calcul ;
  - les cotes sont lues du cache disque et jamais rafraichies toutes seules,
    parce qu'un rechargement de page ne doit pas consommer de credit API.
    Le rafraichissement est un bouton, un geste explicite.

    python web.py                 puis http://127.0.0.1:8000
    python web.py --reseau        pour l'ouvrir depuis un telephone sur le
                                  meme Wi-Fi, et l'installer comme application
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import socket
import threading
import webbrowser
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd

import config
from cli import charger_donnees, entrainer_modele
from data import absences as absences_module
from data import cotes as cotes_module
from data import espn as espn_module
from data import noms as noms_module
from data import resultats as resultats_module
from data import scores as scores_module
from modele import annexes
from modele import calibration
from modele import europe as europe_module
from modele import inter_divisions as inter_module
from paris import combine as combine_module
from paris import confiance as confiance_module
from paris import journal as journal_module
from paris import performance as performance_module
from paris import quotidien as quotidien_module
from paris import reussite as reussite_module
from paris import valeur as valeur_module
from paris import verdict as verdict_module

_journal = logging.getLogger("foot_edge.web")

HOTE = "127.0.0.1"
PORT = 8000
DOSSIER_WEB = Path(__file__).resolve().parent / "web"
PAGE = DOSSIER_WEB / "page.html"
# Fichiers de l'application installable (PWA), servis tels quels. Une liste
# fermee plutot qu'un dossier statique : aucun chemin venu de l'URL ne touche
# le disque.
STATIQUES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icones/icone-180.png": ("icones/icone-180.png", "image/png"),
    "/icones/icone-192.png": ("icones/icone-192.png", "image/png"),
    "/icones/icone-512.png": ("icones/icone-512.png", "image/png"),
    # L'application Android, construite par android/construire.sh : le
    # telephone la telecharge depuis le serveur, sans cable.
    "/RAB3OUNSTAT.apk": ("../android/build/RAB3OUNSTAT.apk",
                         "application/vnd.android.package-archive"),
}
SAISONS = 4
SOURCE = "buts"
# Pas de limite au nombre de matchs du combine du jour : il empile les favoris
# jusqu'a l'objectif demande, aussi loin qu'il le faut (choix de l'utilisateur).
MAX_JAMBES_COMBINE_JOUR: int | None = None

# La page groupe les matchs avec `toLocaleDateString` du navigateur, donc dans
# le fuseau de la machine. Le serveur doit decouper ses journees dans le meme,
# sinon un match a 22h30 UTC tombe un jour ici et le lendemain la-bas.
_FUSEAU_LOCAL = datetime.now().astimezone().tzinfo


def _jour_local(instant: Any) -> str:
    """Date locale d'un instant, au format AAAA-MM-JJ."""
    horodatage = pd.Timestamp(instant)
    if horodatage.tzinfo is None:
        horodatage = horodatage.tz_localize(timezone.utc)
    return str(horodatage.tz_convert(_FUSEAU_LOCAL).date())


def aujourdhui() -> str:
    """La journee en cours, telle que la page la nomme « Aujourd'hui »."""
    return _jour_local(datetime.now(timezone.utc))


def _cotes_a_venir(cotes: pd.DataFrame, jour: date | None = None) -> pd.DataFrame:
    """Tous les matchs du cache a partir de la date locale, sans limite de semaine."""
    if cotes.empty:
        return cotes
    debut = jour or date.fromisoformat(aujourdhui())
    dates = pd.to_datetime(cotes["date"], utc=True, errors="coerce")
    jours = dates.dt.tz_convert(_FUSEAU_LOCAL).dt.date
    return cotes.loc[dates.notna() & (jours >= debut)].copy()


def _bornes_du_jour(jour: str) -> tuple[str, str]:
    """[minuit, minuit suivant) d'une journee locale, en instants UTC ISO."""
    debut = pd.Timestamp(jour).tz_localize(_FUSEAU_LOCAL)
    return (debut.tz_convert(timezone.utc).isoformat(),
            (debut + pd.Timedelta(days=1)).tz_convert(timezone.utc).isoformat())


def _avec_matchs_commences(championnat: str, cotes: pd.DataFrame,
                           jour: str) -> pd.DataFrame:
    """Le cache, complete des matchs du jour qu'il ne contient plus.

    Des le coup d'envoi, un match sort du flux de l'API au rafraichissement
    suivant. Sans l'historique, la vue du jour perdrait ses matchs en cours et
    termines : on les reprend avec les dernieres cotes vues avant le coup
    d'envoi.
    """
    try:
        anciens = cotes_module.derniers_instantanes(championnat, *_bornes_du_jour(jour))
    except Exception as erreur:                       # jamais au prix de la page
        _journal.info("Historique des cotes %s illisible : %s", championnat, erreur)
        return cotes
    if anciens.empty:
        return cotes
    connus = set(zip(cotes["dom"], cotes["ext"])) if not cotes.empty else set()
    manquants = anciens[[(d, e) not in connus
                         for d, e in zip(anciens["dom"], anciens["ext"])]]
    if manquants.empty:
        return cotes
    if cotes.empty:
        return manquants.reset_index(drop=True)
    return pd.concat([cotes, manquants], ignore_index=True)


# Au-dela, un match commence sans score connu est tenu pour termine : 90
# minutes, la mi-temps et les arrets de jeu tiennent largement en deux heures.
DUREE_MATCH = pd.Timedelta(hours=2)


def _calendrier_sans_cotes(championnat: str, jour: str | None = None) -> pd.DataFrame:
    """Programme ESPN d'une ligue sans cotes, a partir de minuit local.

    Les matchs deja commences de la journee restent : comme pour les ligues
    cotees, la vue du jour montre aussi ce qui se joue et ce qui s'est joue.
    """
    debut = pd.Timestamp(jour or aujourdhui()).tz_localize(_FUSEAU_LOCAL)
    return espn_module.calendrier(championnat, debut.tz_convert(timezone.utc).to_pydatetime())


# --------------------------------------------------------------------------
# Cache des modeles
# --------------------------------------------------------------------------

class Cache:
    """Modeles entraines, gardes d'une requete a l'autre.

    Le verrou serialise les entrainements : deux onglets ouverts en meme temps
    ne doivent pas lancer deux fois le meme calcul de trois secondes.
    """

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._modeles: dict[str, tuple[Any, Any]] = {}
        self._annexes: dict[str, dict[str, Any]] = {}
        self._inter: dict[str, tuple[Any, Any, Any] | None] = {}
        self._coupes: dict[str, dict] = {}

    def modele(self, championnat: str) -> tuple[Any, Any]:
        if config.est_coupe(championnat):
            return self._modele_de_coupe(championnat)
        with self._verrou:
            if championnat not in self._modeles:
                matchs, _ = charger_donnees(championnat, SAISONS, SOURCE)
                if matchs.empty and resultats_module.ligue_api_football(championnat):
                    raise ValueError(
                        f"Aucune donnee recente pour {championnat} : ses resultats "
                        "viennent d'API-Football, dont le plan gratuit s'arrete a la "
                        "saison 2024. Un abonnement payant est necessaire pour estimer "
                        "la saison en cours.")
                if matchs.empty:
                    raise ValueError(
                        f"Aucune donnee pour {championnat}. Verifie ta connexion : "
                        "les saisons deja telechargees restent utilisables hors ligne.")
                self._modeles[championnat] = (
                    matchs, entrainer_modele(matchs, SOURCE, championnat))
                self._annexes[championnat] = _entrainer_annexes(matchs, championnat)
            return self._modeles[championnat]

    def _modele_de_coupe(self, coupe: str) -> tuple[Any, Any]:
        """Le modele d'emprunt d'une coupe (voir config.COUPES).

        Hors du verrou : une coupe nationale emprunte `inter_divisions` ou
        `modele`, qui le prennent eux-memes, et le verrou n'est pas reentrant.
        """
        coupe = coupe.upper()
        if coupe in self._modeles:
            return self._modeles[coupe]
        info = config.COUPES[coupe]
        if info["type"] == "europe":
            with self._verrou:
                if "@europe" not in self._modeles:
                    matchs, modele, rapport = europe_module.entrainer(SAISONS)
                    self._modeles["@europe"] = (matchs, modele)
                    self._coupes["@europe"] = {
                        "type": "europe", "championnats": list(rapport.championnats),
                        "matchs": rapport.nb_matchs, "equipes": rapport.nb_equipes,
                        "matchs_europeens": rapport.matchs_europeens,
                        "ecartes": sorted(rapport.ecartes)}
            resultat, description = self._modeles["@europe"], self._coupes["@europe"]
        else:
            rattachement = str(info["rattachement"])
            secours = self.inter_divisions(rattachement)
            if secours is not None:
                matchs, modele, rapport = secours
                resultat = (matchs, modele)
                description = {"type": "nationale", "championnats": list(rapport.divisions),
                               "matchs": rapport.nb_matchs, "equipes": rapport.nb_equipes}
            else:
                resultat = self.modele(rattachement)
                description = {"type": "nationale", "championnats": [rattachement],
                               "matchs": len(resultat[0]),
                               "equipes": len(resultat[1].equipes)}
        with self._verrou:
            self._modeles[coupe] = resultat
            self._coupes[coupe] = description
        return resultat

    def coupe(self, coupe: str) -> dict | None:
        """Sur quels championnats repose le modele de cette coupe, ou None."""
        if not config.est_coupe(coupe):
            return None
        self.modele(coupe)
        return self._coupes.get(coupe.upper())

    def annexes(self, championnat: str) -> dict[str, Any]:
        """Modeles corners et cartons, entraines avec le modele principal.

        Renvoie un dictionnaire eventuellement vide : un championnat sans
        colonnes de corners ou de cartons doit afficher moins, pas echouer.
        Toujours vide pour une coupe : ses modeles d'emprunt melangent des
        championnats dont les corners et cartons n'ont pas la meme echelle.
        """
        if config.est_coupe(championnat):
            return {}
        self.modele(championnat)
        return self._annexes.get(championnat, {})

    def inter_divisions(self, championnat: str) -> tuple[Any, Any, Any] | None:
        """Modele commun aux divisions du pays, ou None s'il est impossible.

        Entraine a la demande et jamais d'avance : il coute plus cher que le
        modele d'un championnat (cinq divisions anglaises font 7800 matchs), et
        la plupart des journees n'en ont aucun besoin. L'echec est memorise au
        meme titre que la reussite, sinon chaque match de coupe d'un pays sans
        deuxieme division relancerait le meme calcul pour rien.
        """
        with self._verrou:
            if championnat not in self._inter:
                try:
                    modele, rapport = inter_module.entrainer(championnat, SAISONS)
                    matchs = pd.concat(
                        [resultats_module.charger(c, SAISONS) for c in rapport.divisions],
                        ignore_index=True)
                    self._inter[championnat] = (matchs, modele, rapport)
                except (ValueError, KeyError) as erreur:
                    _journal.info("Pas de modele inter-divisions pour %s : %s",
                                  championnat, erreur)
                    self._inter[championnat] = None
            return self._inter[championnat]

    def oublier(self, championnat: str | None = None) -> None:
        with self._verrou:
            if championnat:
                self._modeles.pop(championnat, None)
                self._annexes.pop(championnat, None)
                self._inter.pop(championnat, None)
                self._coupes.pop(championnat, None)
            else:
                self._modeles.clear()
                self._annexes.clear()
                self._inter.clear()
                self._coupes.clear()


def _entrainer_annexes(matchs, championnat: str) -> dict:
    """Corners et cartons, chacun independamment du reste.

    Un type indisponible n'empeche pas l'autre : football-data.co.uk ne remplit
    pas ces colonnes partout, et une ligue sans corners doit garder ses cartons.
    """
    modeles: dict = {}
    for type_compte in annexes.TYPES:
        try:
            modeles[type_compte] = annexes.entrainer(matchs, type_compte)
        except (annexes.DonneesInsuffisantes, ValueError) as erreur:
            _journal.info("Modele %s indisponible pour %s : %s",
                          type_compte, championnat, erreur)
    return modeles


CACHE = Cache()


# --------------------------------------------------------------------------
# Donnees servies
# --------------------------------------------------------------------------

def _probabilites_de_marche(cotes) -> dict[tuple[str, str], dict[tuple[str, str], float]]:
    """Probabilites du marche, marge retiree, indexees par match puis selection.

    Sert a contredire le modele : sur les deux seuls marches que The Odds API
    cote, un ecart important entre les deux camps est presque toujours une
    information qui manque au modele, pas une occasion. Les autres marches
    (double chance, clean sheet...) n'ont aucune cote et restent donc
    invalidables — la page doit le dire plutot que de les presenter a egalite.
    """
    if cotes.empty:
        return {}
    try:
        consensus = calibration.consensus(cotes)
    except (ValueError, KeyError) as erreur:      # cotes trop partielles
        _journal.info("Consensus de marche indisponible (%s) : "
                      "les pronos seront affiches sans contre-expertise.", erreur)
        return {}
    par_match: dict[tuple[str, str], dict[tuple[str, str], float]] = {}
    for ligne in consensus.itertuples():
        par_match.setdefault((ligne.dom, ligne.ext), {})[
            (ligne.marche, ligne.selection)] = float(ligne.p_marche)
    return par_match


class _Absences:
    """Absences des jours concernes, un appel API par jour au maximum.

    Une page de huit matchs ne doit pas declencher huit appels : le plan
    gratuit n'en autorise que cent par jour, et un appel `/injuries?date=`
    couvre deja toutes les ligues de cette date.
    """

    def __init__(self) -> None:
        self._jours: dict[str, tuple[list, str, str]] = {}

    def _jour(self, jour: str) -> tuple[list, str, str]:
        if jour not in self._jours:
            self._jours[jour] = absences_module.etat_du_jour(jour)
        return self._jours[jour]

    def du_match(self, date_match, dom: str, ext: str, equipes: list[str]) -> dict:
        jour = str(date_match)[:10]
        lignes, statut, explication = self._jour(jour)
        par_equipe = absences_module.du_match(lignes, dom, ext, equipes)
        return {
            "statut": statut,
            "explication": explication,
            "jours_couverts": absences_module.jours_couverts(),
            "dom": par_equipe.get(dom, []),
            "ext": par_equipe.get(ext, []),
        }


class _Resultats:
    """Scores de la saison en cours, pour confronter les pronos au reel.

    Volontairement separe du cache de modeles : celui-ci fige l'historique au
    demarrage du serveur, et ne verrait donc jamais un match joue depuis. Ici on
    relit, avec le TTL de six heures de football-data — un match termine il y a
    une heure peut donc ne pas encore etre publie, et c'est dit plutot que
    devine.
    """

    def __init__(self) -> None:
        self._par_ligue: dict[str, dict[tuple[str, str], dict]] = {}
        self._par_jour: dict[tuple, dict[tuple[str, str], dict]] = {}
        self._en_direct: dict[tuple, dict[tuple[str, str], dict]] = {}
        self._annules: dict[tuple, dict[tuple[str, str], dict]] = {}

    def _ligue(self, championnat: str) -> dict[tuple[str, str], dict]:
        if championnat not in self._par_ligue:
            index: dict[tuple[str, str], dict] = {}
            if config.est_coupe(championnat):
                # Aucune coupe n'est publiee par football-data.co.uk : ses
                # scores viennent d'API-Football seulement (`_du_jour`).
                self._par_ligue[championnat] = index
                return index
            try:
                matchs = resultats_module.charger(championnat, 1)
            except Exception as erreur:               # jamais au prix de la page
                _journal.info("Resultats %s indisponibles : %s", championnat, erreur)
                matchs = None
            if matchs is not None and not matchs.empty:
                for ligne in matchs.itertuples():
                    index[(str(ligne.dom), str(ligne.ext))] = {
                        "date": str(getattr(ligne, "date", "")),
                        "buts_dom": getattr(ligne, "buts_dom", None),
                        "buts_ext": getattr(ligne, "buts_ext", None),
                        "corners_dom": getattr(ligne, "corners_dom", None),
                        "corners_ext": getattr(ligne, "corners_ext", None),
                        "jaunes_dom": getattr(ligne, "jaunes_dom", None),
                        "jaunes_ext": getattr(ligne, "jaunes_ext", None),
                    }
            self._par_ligue[championnat] = index
        return self._par_ligue[championnat]

    def _du_jour(self, jour: str, equipes: list[str]) -> dict[tuple[str, str], dict]:
        """Scores API-Football d'une date, un appel pour toutes les ligues."""
        cle = (jour, id(equipes))
        if cle not in self._par_jour:
            self._par_jour[cle] = scores_module.du_jour(jour, equipes)
        return self._par_jour[cle]

    def direct(self, jour: str, dom: str, ext: str,
               equipes: list[str]) -> dict | None:
        """Score et minute d'une rencontre en cours, ou None."""
        cle = (jour, id(equipes))
        if cle not in self._en_direct:
            self._en_direct[cle] = scores_module.en_direct(jour, equipes)
        return self._en_direct[cle].get((dom, ext))

    def annule(self, jour: str, dom: str, ext: str,
               equipes: list[str]) -> dict | None:
        """La rencontre reportee, annulee ou arretee, ou None."""
        cle = (jour, id(equipes))
        if cle not in self._annules:
            self._annules[cle] = scores_module.annules(jour, equipes)
        return self._annules[cle].get((dom, ext))

    def du_match(self, championnat: str, dom: str, ext: str,
                 equipes: list[str] | None = None,
                 jour: str | None = None) -> dict | None:
        """Le score d'une rencontre, ou None si personne ne l'a encore publie.

        Deux sources, dans cet ordre :
          1. football-data, qui porte aussi corners et cartons — donc les seuls
             verdicts possibles sur les comptes annexes ;
          2. API-Football, qui n'a que les buts mais les a le soir meme. Sans
             elle, un match joue disparait du flux de cotes bien avant que
             football-data ne publie, et aucun prono ne serait jamais confronte.

        L'appariement se fait sur les deux equipes, pas sur la date : le flux de
        cotes donne une heure de coup d'envoi et football-data une date de
        journee, qui divergent d'un fuseau. Deux equipes ne se rencontrent au
        plus qu'une fois par phase, l'ambiguite est negligeable.
        """
        publie = self._ligue(championnat).get((dom, ext))
        if publie is not None and jour and not _meme_journee(publie["date"], jour):
            # Dans les ligues ou l'on se rencontre trois ou quatre fois par
            # saison, la meme affiche a deja ete jouee : ce score est celui
            # d'un autre match.
            publie = None
        if publie is not None:
            return {**publie, "source": "football_data"}
        if jour and equipes:
            entree = self._du_jour(jour, equipes).get((dom, ext))
            if entree is None:
                return None
            # Les corners et les cartons demandent un appel de plus, par
            # rencontre. On ne le fait que pour les matchs reellement affiches,
            # et le resultat est garde definitivement : un match joue ne change
            # plus. Douze rencontres par jour sur un quota de cent.
            return scores_module.comptes(entree, dom, ext)
        return None


def _etat_espn(carte: dict, ligne: Any, ecoule: pd.Timedelta) -> None:
    """Statut et score d'un match commence d'une ligue sans cotes.

    ESPN donne le score de son propre calendrier, en direct puis final : pas
    besoin d'API-Football, dont les noms d'equipes ne seraient de toute facon
    pas ceux de l'historique ESPN.
    """
    def entier(valeur: Any) -> int | None:
        return None if valeur is None or pd.isna(valeur) else int(valeur)

    buts_dom, buts_ext = entier(ligne.buts_dom), entier(ligne.buts_ext)
    carte["joue"] = True
    carte["annulation"] = None
    carte["direct"] = None
    carte["resultat"] = None
    if ligne.statut == "in" and buts_dom is not None:
        carte["statut"] = "en_cours"
        carte["direct"] = {"buts_dom": buts_dom, "buts_ext": buts_ext,
                           "minute": None, "statut": "en cours"}
    elif ligne.statut == "post" and buts_dom is not None and buts_ext is not None:
        carte["statut"] = "termine"
        resultat = {"buts_dom": buts_dom, "buts_ext": buts_ext}
        carte["resultat"] = {**resultat, "corners_dom": None, "corners_ext": None,
                             "jaunes_dom": None, "jaunes_ext": None,
                             "issue": verdict_module.issue_1n2(resultat),
                             "source": "espn"}
        carte["verdicts"] = _verdicts_du_match(carte, resultat)
    else:
        carte["statut"] = "en_cours" if ecoule < DUREE_MATCH else "termine"


def _meme_journee(date_publiee: str, jour: str) -> bool:
    """Une date football-data et un jour local designent-ils le meme match ?

    Un jour d'ecart est tolere : la date du coup d'envoi en UTC et celle de
    football-data peuvent differer d'un fuseau.
    """
    try:
        ecart = abs((pd.Timestamp(date_publiee).date() - date.fromisoformat(jour)).days)
    except (ValueError, TypeError):
        return True                                   # date illisible : on ne refuse pas
    return ecart <= 1


def _verdicts_du_match(match: dict, resultat: dict) -> dict:
    """Rejoue chaque prono affiche sur cette carte contre le score final."""
    rendus: dict[str, Any] = {}
    verdicts: list[str] = []

    prono = match.get("prono_1n2")
    if prono:
        rendus["prono_1n2"] = verdict_module.verifier("1N2", prono["selection"], resultat)
        # Un prono a 80 % qui echoue n'est pas une faute du modele : il DOIT
        # echouer une fois sur cinq. Sans ce rappel, une pastille rouge se lit
        # comme une erreur et une verte comme une preuve.
        rendus["nuance"] = verdict_module.nuance(
            prono.get("p_modele"), rendus["prono_1n2"])
        verdicts.append(rendus["prono_1n2"])
        couverture = prono.get("couverture")
        if couverture:
            rendus["couverture"] = verdict_module.verifier(
                "double chance", couverture["selection"], resultat)
            verdicts.append(rendus["couverture"])

    rendus["annexes"] = []
    for annexe in match.get("pronos_annexes", []):
        rendus["annexes"].append(verdict_module.verifier_compte(
            annexe.get("libelle_compte", ""), annexe.get("selection", ""),
            annexe.get("ligne", 0), resultat))
    verdicts += rendus["annexes"]

    # Le bilan s'arrete ici, AVANT le catalogue complet des marches. Celui-ci
    # contient des paires complementaires — plus et moins de chaque seuil, les
    # trois issues du 1N2 — dont exactement la moitie est juste quel que soit le
    # score. Les compter donnerait un taux de reussite d'environ 50 % sur tous
    # les matchs, ce qui ressemblerait a une mesure et n'en serait pas une.
    rendus["bilan"] = verdict_module.bilan(verdicts)

    # Le detail du catalogue reste fourni, ligne par ligne, pour le tableau des
    # marches — mais il ne pese pas dans le compte affiche.
    rendus["pronos"] = [
        verdict_module.verifier(p["marche"], p["selection"], resultat)
        for p in match.get("pronos", [])]
    return rendus


def _reserves_absences(absences: dict, dom: str, ext: str) -> list[str]:
    """Une equipe trop amputee rend le modele moins pertinent, sans le corriger.

    Le modele est entraine sur des matchs joues par des effectifs complets.
    Quand trois titulaires manquent, il decrit une equipe qui n'entrera pas sur
    le terrain. On le dit ; on ne bricole pas la probabilite pour autant, faute
    d'une estimation de ce que chaque joueur apporte.
    """
    if absences.get("statut") != absences_module.DISPONIBILITE_OK:
        return []
    reserves = []
    for cle, equipe in (("dom", dom), ("ext", ext)):
        liste = absences.get(cle) or []
        if len(liste) >= config.SEUIL_ABSENCES_NOTABLES:
            suspendus = sum(1 for a in liste if a["suspension"])
            detail = f"{len(liste)} absent(s)"
            if suspendus:
                detail += f" dont {suspendus} suspendu(s)"
            reserves.append(
                f"{equipe} : {detail} — le modele decrit un effectif complet, "
                "pas celui qui jouera")
    return reserves


def _pronos_annexes(modeles: dict, dom: str, ext: str,
                    reserves: list[str], championnat: str = "") -> list[dict]:
    """Un prono par compte annexe disponible, pour ce match.

    Une equipe absente de ce modele-la (promue, ou match d'une saison ou la
    colonne manquait) fait sauter ce seul prono, pas la carte du match.
    """
    resultats = []
    for type_compte, modele in modeles.items():
        try:
            prediction = modele.predire(dom, ext)
        except KeyError as erreur:
            _journal.info("Prono %s impossible sur %s - %s : %s",
                          type_compte, dom, ext, erreur)
            continue
        prediction["championnat"] = championnat.upper()
        prono = confiance_module.prono_comptes(prediction, dom, ext, reserves)
        if prono:
            prono["validation"] = _validation_annexe(championnat, type_compte)
            resultats.append(prono)
    return resultats


def _validation_annexe(championnat: str, type_compte: str) -> dict | None:
    """La contre-expertise de ce modele, si elle a ete calculee.

    Aucun bookmaker ne cote ces marches : cette validation contre les comptes
    reels est la seule chose qui puisse remplacer l'etiquette « non verifiable »
    par un chiffre. Elle est produite par `cli.py valider-annexes`, jamais ici.
    """
    validation = annexes.charger_validation(championnat, type_compte)
    if validation is None:
        return None
    return {
        "matchs": validation.nb_matchs,
        "ecart_calibration_max": validation.ecart_calibration_max,
        "biais": validation.biais, "biais_ic": validation.biais_ic,
        "biais_significatif": validation.biais_significatif,
        "dispersion": validation.dispersion,
        "bat_la_moyenne_de_ligue": validation.bat_la_moyenne_de_ligue,
        "calibration": validation.calibration,
        "calcule_le": validation.calcule_le,
    }


def _stats_supplementaires(prediction: dict) -> dict:
    """Les statistiques que le modele calcule deja et que la page jetait.

    `matrice_scores` produit bien plus que le 1N2 : buts attendus par equipe,
    cages inviolees, scores exacts, handicaps asiatiques. Tout etait calcule a
    chaque requete puis ecarte. Les cles absentes sont tolerees pour la meme
    raison que dans paris/confiance.py : un modele plus pauvre doit degrader
    l'affichage, pas le faire echouer.
    """
    buts = prediction.get("buts_attendus", {})
    return {
        "buts_dom": buts.get("dom"), "buts_ext": buts.get("ext"),
        "clean_sheet": prediction.get("clean_sheet"),
        "scores_probables": prediction.get("scores_probables", [])[:6],
        "handicaps": prediction.get("handicap_asiatique"),
    }


def affiches(championnat: str,
             jour: str | None = None) -> tuple[list[dict], list[dict], float | None]:
    """Matchs a venir du flux de cotes, avec les estimations du modele.

    Renvoie aussi ceux qu'on a du ecarter. Une equipe promue n'a pas d'
    historique dans le championnat charge : le modele ne peut rien en dire, et
    la page doit le dire plutot que d'afficher sept matchs sur neuf sans un mot.

    `jour` (AAAA-MM-JJ local) restreint a une seule journee. Le filtre est
    applique avant `predire` : sur une vue du jour, calculer tous les matchs a
    venir pour n'en montrer qu'un serait payer plusieurs fois le meme prix.
    """
    matchs_charges, modele = CACHE.modele(championnat)
    modeles_annexes = CACHE.annexes(championnat)
    coupe = CACHE.coupe(championnat)
    comptes = resultats_module.nb_matchs_par_equipe(matchs_charges)
    cotes, age = cotes_module.lire_cache(championnat)
    cotes = _cotes_a_venir(cotes)
    if jour is not None:
        cotes = _avec_matchs_commences(championnat, cotes, jour)
    marche_devigge = _probabilites_de_marche(cotes)
    poids_modele = calibration.poids_melange(championnat)
    absences_cache = _Absences()
    resultats_cache = _Resultats()
    maintenant = datetime.now(timezone.utc)
    lignes, ecartes = [], []
    # Une ligue sans cotes tire son programme d'ESPN : sans lui, aucun match.
    sans_cotes = config.sans_cotes(championnat)
    a_venir = (_calendrier_sans_cotes(championnat, jour) if sans_cotes
               else cotes_module.matchs_a_venir(cotes))
    vus: set[tuple] = set()
    for ligne in a_venir.itertuples():
        if jour is not None and _jour_local(ligne.date) != jour:
            continue
        dom = noms_module.resoudre_vers(ligne.dom, modele.equipes)
        ext = noms_module.resoudre_vers(ligne.ext, modele.equipes)
        modele_du_match, comptes_du_match, inter = modele, comptes, None
        if (dom is None or ext is None) and coupe is None:
            # Un adversaire d'une autre division — un match de coupe. Le modele
            # du pays le connait peut-etre ; il n'est entraine que maintenant.
            secours = CACHE.inter_divisions(championnat)
            if secours is not None:
                matchs_pays, modele_pays, rapport = secours
                dom_pays = noms_module.resoudre_vers(ligne.dom, modele_pays.equipes)
                ext_pays = noms_module.resoudre_vers(ligne.ext, modele_pays.equipes)
                if dom_pays and ext_pays and dom_pays != ext_pays:
                    dom, ext = dom_pays, ext_pays
                    modele_du_match = modele_pays
                    comptes_du_match = resultats_module.nb_matchs_par_equipe(matchs_pays)
                    inter = {"divisions": list(rapport.divisions),
                             "matchs": rapport.nb_matchs,
                             "equipes": rapport.nb_equipes,
                             "ponts": dict(rapport.ponts)}
        if dom is None or ext is None or dom == ext:
            # Le modele se tait, le marche non : ses probabilites (marge
            # retiree) restent la meilleure estimation disponible. La page les
            # montre comme telles, sans prono du modele ni mise.
            marche = {}
            if not sans_cotes:
                brut = marche_devigge.get((ligne.dom, ligne.ext), {})
                for (nom_marche, selection), valeur in brut.items():
                    if nom_marche == cotes_module.MARCHE_1N2 and selection in ("1", "N", "2"):
                        marche[selection] = valeur
                    elif nom_marche == cotes_module.MARCHE_TOTAUX:
                        marche[selection] = valeur
            detail = ([] if sans_cotes else
                      cotes_module.par_bookmaker(cotes, ligne.date, ligne.dom, ligne.ext))
            ecartes.append({
                "date": str(ligne.date), "dom": ligne.dom, "ext": ligne.ext,
                "inconnues": [n for n, r in ((ligne.dom, dom), (ligne.ext, ext))
                              if r is None],
                "marche": marche,
                "meilleures_cotes": cotes_module.meilleures_cotes(detail),
                "bookmakers": len(detail)})
            continue
        # L'historique des cotes garde le nom brut d'avant un alias ajoute depuis
        # (« Bodø/Glimt » puis « Bodo/Glimt ») : resolus, c'est le meme match.
        cle = (pd.Timestamp(ligne.date), dom, ext)
        if cle in vus:
            continue
        vus.add(cle)
        p = modele_du_match.predire(dom, ext)
        detail = cotes_module.par_bookmaker(cotes, ligne.date, ligne.dom, ligne.ext)
        peu_de_donnees = [
            {"equipe": e, "matchs": comptes_du_match.get(e, 0)} for e in (dom, ext)
            if comptes_du_match.get(e, 0) < config.MIN_MATCHS_ESTIMATION_FIABLE]
        # Une equipe sans historique suffisant discredite TOUS les marches du
        # match, pas seulement le 1N2 : la reserve est portee par chaque prono.
        reserves = [f"{e['equipe']} n'a que {e['matchs']} match(s) dans l'historique charge"
                    for e in peu_de_donnees]
        absences = absences_cache.du_match(ligne.date, dom, ext, modele_du_match.equipes)
        reserves += _reserves_absences(absences, dom, ext)
        if sans_cotes:
            reserves.append(
                "aucun bookmaker ne cote ce championnat : probabilites du modele "
                "seul, sans comparaison au marche, et leur calibration n'a pas "
                "pu etre verifiee contre des cotes")
        if inter is not None:
            reserves.append(
                f"estimation inter-divisions ({'+'.join(inter['divisions'])}) : "
                "ni la calibration ni le ROI mesures sur ce championnat ne "
                "s'appliquent a ce match — ils n'ont ete etablis que sur des "
                "rencontres d'une seule division")
        if coupe is not None:
            reserves.append(
                f"match de coupe estime par un modele de championnats "
                f"({'+'.join(coupe['championnats'])}) : ni calibration ni ROI n'ont "
                "ete mesures sur des matchs de coupe"
                + (", et le niveau relatif des championnats ne repose que sur "
                   f"{coupe['matchs_europeens']} matchs de coupe d'Europe (C1, C3, C4)"
                   if coupe["type"] == "europe" else ""))
        p_marche = marche_devigge.get((ligne.dom, ligne.ext), {})
        meilleures = cotes_module.meilleures_cotes(detail)
        pronos = confiance_module.lister(p, dom, ext, p_marche, reserves)
        lignes.append({
            "date": str(ligne.date), "dom": dom, "ext": ext,
            "bookmakers": int(ligne.nb_bookmakers),
            "detail_bookmakers": detail,
            "meilleures_cotes": meilleures,
            "prono_1n2": confiance_module.prono_1n2(
                p, dom, ext, p_marche, meilleures, reserves, poids_modele),
            "choix_sur": confiance_module.choix_le_plus_sur(
                p, dom, ext, p_marche, meilleures, reserves, poids_modele),
            "pronos_annexes": [] if inter is not None else _pronos_annexes(
                modeles_annexes, dom, ext, reserves, championnat),
            "inter_divisions": inter,
            "coupe": coupe,
            "absences": absences,
            "1": p["1N2"]["1"], "N": p["1N2"]["N"], "2": p["1N2"]["2"],
            "plus25": p["totaux"]["plus_2.5"], "btts": p["btts"]["oui"],
            "buts": p["buts_attendus"]["total"],
            **_stats_supplementaires(p),
            "pronos": confiance_module.en_dictionnaires(pronos),
            "plus_surs": confiance_module.en_dictionnaires(
                confiance_module.les_plus_surs(pronos)),
            "peu_de_donnees": peu_de_donnees,
        })
        # Le match est-il joue, et sait-on ce qu'il a donne ?
        coup_envoi = pd.Timestamp(ligne.date)
        if coup_envoi.tzinfo is None:
            coup_envoi = coup_envoi.tz_localize(timezone.utc)
        lignes[-1]["statut"] = "a_venir"
        if coup_envoi < maintenant:
            # La date UTC, pas la date locale : c'est celle sous laquelle
            # API-Football range la rencontre. Un coup d'envoi a minuit heure
            # de Paris y est encore la veille.
            jour_match = str(coup_envoi.tz_convert(timezone.utc).date())
            if sans_cotes:
                _etat_espn(lignes[-1], ligne, maintenant - coup_envoi)
                continue
            # Le direct d'abord : il relit les scores API-Football toutes les
            # dix minutes, et un match qui vient de finir y apparait alors
            # aussi pour la lecture des scores finaux qui suit.
            direct = (resultats_cache.direct(jour_match, dom, ext, modele_du_match.equipes)
                      if maintenant - coup_envoi < DUREE_MATCH + pd.Timedelta(hours=1)
                      else None)
            # `modele_du_match.equipes` et non celles du championnat : sur un
            # match de coupe, c'est le modele inter-divisions qui a resolu les
            # noms, et la cle doit etre la meme des deux cotes.
            resultat = None if direct else resultats_cache.du_match(
                championnat, dom, ext, modele_du_match.equipes, jour_match)
            lignes[-1]["joue"] = True
            lignes[-1]["direct"] = None if direct is None else {
                cle: direct.get(cle) for cle in ("buts_dom", "buts_ext", "minute", "statut")}
            # Sans score ni direct, c'est l'heure qui tranche : un match commence
            # il y a moins de deux heures est sans doute encore en cours —
            # sauf si API-Football le dit reporte, annule ou arrete.
            annule = (None if direct is not None or resultat is not None
                      else resultats_cache.annule(jour_match, dom, ext,
                                                  modele_du_match.equipes))
            lignes[-1]["annulation"] = None if annule is None else annule["statut"]
            lignes[-1]["statut"] = (
                "en_cours" if direct is not None
                else "annule" if annule is not None
                else "en_cours" if resultat is None and maintenant - coup_envoi < DUREE_MATCH
                else "termine")
            if resultat is None:
                # Le TTL de six heures de football-data peut retarder la
                # publication : dire « pas encore » vaut mieux que se taire.
                lignes[-1]["resultat"] = None
            else:
                # `.get` et non `[...]` : une source de resultats sans colonne
                # de corners ne doit pas faire tomber la carte du match.
                lignes[-1]["resultat"] = {
                    cle: resultat.get(cle) for cle in (
                        "buts_dom", "buts_ext", "corners_dom", "corners_ext",
                        "jaunes_dom", "jaunes_ext")
                } | {"issue": verdict_module.issue_1n2(resultat),
                     "source": resultat.get("source", "football_data")}
                lignes[-1]["verdicts"] = _verdicts_du_match(lignes[-1], resultat)
    return lignes, ecartes, age


def opportunites(championnat: str, bankroll: float, forcer: bool = False,
                 jour: str | None = None) -> tuple[list[dict], float | None]:
    """Selections ou le marche semble decale. `forcer` consomme des credits."""
    _, modele = CACHE.modele(championnat)
    if config.sans_cotes(championnat):
        # Pas de prix, pas de valeur a chercher. Rafraichir ne coute rien ici :
        # c'est le calendrier ESPN qu'on relit, sans credit The Odds API.
        if forcer:
            espn_module.evenements(championnat, datetime.now(timezone.utc).year, ttl=0)
        return [], None
    if forcer:
        cotes, age = cotes_module.recuperer(championnat, forcer=True), 0.0
    else:
        cotes, age = cotes_module.lire_cache(championnat)
    cotes = _cotes_a_venir(cotes)
    if not cotes.empty and jour is not None:
        cotes = cotes[cotes["date"].map(_jour_local) == jour]
    if cotes.empty:
        return [], age
    return [{
        "date": str(o.date), "dom": o.dom, "ext": o.ext, "marche": o.marche,
        "selection": o.selection, "cote": o.cote, "bookmaker": o.bookmaker,
        "p_modele": o.p_modele, "p_marche": o.p_marche, "p_finale": o.p_finale,
        "ecart": o.ecart_modele_marche, "esperance": o.esperance, "mise": o.mise,
        "retenue": bool(o.recommandee), "alerte": o.alerte or "",
        "motifs": list(o.raisons_rejet),
    } for o in valeur_module.detecter(
        cotes, modele, bankroll,
        a_melange=calibration.poids_melange(championnat))], age


def _performance_mesuree(championnat: str) -> dict | None:
    """Ce que la regle a reellement rapporte sur l'historique de ce championnat.

    Jamais calcule ici : la mesure demande un walk-forward de plusieurs minutes.
    Elle est produite par `python cli.py performance -c XX` et relue telle
    quelle. Absente, la page le dit plutot que d'afficher la seule esperance
    annoncee, qui est la moitie flatteuse de l'information.
    """
    mesure = performance_module.charger(championnat)
    if mesure is None:
        return None
    return {
        "roi": mesure.roi, "roi_bas": mesure.roi_bas, "roi_haut": mesure.roi_haut,
        "esperance_annoncee": mesure.esperance_annoncee,
        "nb_paris": mesure.nb_paris, "matchs": mesure.matchs,
        "roi_favori_marche": mesure.roi_favori_marche,
        "roi_tout_parier": mesure.roi_tout_parier,
        "significatif": mesure.significatif, "verdict": mesure.verdict,
        "calcule_le": mesure.calcule_le,
    }


# Ce que sait faire CE processus. `web/page.html` est relu a chaque requete,
# donc l'interface est toujours a jour ; `web.py`, lui, est charge une seule fois
# au demarrage. Une page recente devant un serveur ancien affiche donc des
# libelles neufs sur des donnees qui n'existent pas. Ce drapeau permet de le
# dire au lieu de laisser croire a une absence de donnee.
CAPACITES = ("resultats_football_data", "resultats_api_football", "verdicts",
             "statut_des_matchs", "statuts_en_direct")


def etat_du_jour(championnat: str, bankroll: float, forcer: bool = False) -> dict:
    """Tout ce que la page affiche, en un seul aller-retour."""
    lignes, age = opportunites(championnat, bankroll, forcer)
    matchs, ecartes, _ = affiches(championnat)
    poids = calibration.poids_melange(championnat)
    return {
        "performance": _performance_mesuree(championnat),
        "poids_modele": poids,
        "poids_modele_defaut": config.POIDS_MELANGE_DEFAUT,
        "poids_mesure": calibration.charger_poids_melange(championnat) is not None,
        "championnat": championnat,
        "nom": config.nom_championnat(championnat),
        "matchs": matchs,
        "ecartes": ecartes,
        "opportunites": lignes,
        "age_cotes_minutes": round(age / 60, 1) if age is not None else None,
        "quota": cotes_module.message_quota().splitlines()[0],
        "bankroll": bankroll,
        "exposition": journal_module.exposition(),
        "plafond_mise": round(bankroll * config.PLAFOND_MISE_BANKROLL, 2),
        "part_plafond": config.PLAFOND_MISE_BANKROLL,
        "seuil_alerte_jour": config.SEUIL_ALERTE_EXPOSITION_JOUR,
        "saisons": SAISONS,
        "genere": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "avertissement": config.AVERTISSEMENT_LEGAL,
        "capacites": list(CAPACITES),
    }


# Champs d'une carte de match qui changent pendant qu'elle est jouee. Le reste
# (probabilites, cotes, pronos) est fige au coup d'envoi.
CHAMPS_EN_DIRECT = ("date", "dom", "ext", "statut", "direct", "resultat",
                    "joue", "verdicts", "annulation")


def statuts_du_jour(codes: list[str]) -> dict:
    """Statut, score en direct et resultat des matchs du jour de ces ligues.

    Sert au rafraichissement automatique de la page. Aucun credit The Odds API :
    seul le cache des cotes est lu. Les scores API-Football, eux, ne sont relus
    qu'une fois toutes les dix minutes au plus, quel que soit le nombre de pages
    ouvertes qui interrogent cette route.
    """
    jour = aujourdhui()
    matchs: list[dict] = []
    for code in codes:
        try:
            lignes, _, _ = affiches(code, jour=jour)
        except Exception as erreur:                   # une ligue muette, pas la page
            _journal.info("Statuts %s indisponibles : %s", code, erreur)
            continue
        matchs += [{"ligue": code} | {cle: m.get(cle) for cle in CHAMPS_EN_DIRECT}
                   for m in lignes]
    return {"jour": jour, "matchs": matchs,
            "genere": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


def ligues_du_jour(jour: str) -> list[str]:
    """Les championnats qui ont au moins un match ce jour-la.

    Le cache des cotes, complete de l'historique pour les matchs deja
    commences. Ne lit que le disque : aucun modele n'est entraine ici. C'est ce qui
    rend la vue du jour tenable. Un entrainement coute quelques secondes, et un
    jour de semaine la moitie des ligues ne joue pas — les charger toutes pour
    n'en afficher que huit matchs serait payer dix-sept fois un calcul inutile.
    """
    codes = []
    for code in config.COMPETITIONS_AVEC_COTES:
        cotes, _ = cotes_module.lire_cache(code)
        cotes = _cotes_a_venir(cotes)
        if not cotes.empty and (cotes["date"].map(_jour_local) == jour).any():
            codes.append(code)
        elif any(_jour_local(d) == jour
                 for d in cotes_module.lire_evenements_en_cache(code)):
            # Calendrier gratuit deja sur disque : une ligue sans cache de
            # cotes (Europa, Conference) est quand meme reconnue ce jour-la.
            codes.append(code)
        elif not _avec_matchs_commences(code, cotes.iloc[0:0], jour).empty:
            codes.append(code)                        # tous deja commences
    for code in config.CHAMPIONNATS_SANS_COTES:
        calendrier = _calendrier_sans_cotes(code, jour)
        if not calendrier.empty and (calendrier["date"].map(_jour_local) == jour).any():
            codes.append(code)
    return codes


def etat_aujourdhui(bankroll: float, jour: str | None = None,
                    forcer: bool = False) -> dict:
    """Les matchs d'une seule journee, toutes ligues confondues.

    Meme forme que `etat_du_jour` pour que la page les affiche par le meme
    chemin, a trois differences pres, toutes assumees :
      - `performance` et `poids_modele` sont nuls, parce qu'ils sont mesures
        par championnat : les cumuler melangerait des echantillons distincts ;
      - `age_cotes_minutes` porte le cache le PLUS VIEUX des ligues lues, seul
        chiffre honnete quand on en agrege plusieurs ;
      - une ligue qui echoue n'interrompt pas les autres : elle est reportee
        dans `ligues`, et la page affiche les ligues qui ont repondu.

    `forcer` interroge l'API pour les ligues qui jouent aujourd'hui, a deux
    credits chacune. Lesquelles jouent est decide sur le cache actuel : une
    ligue dont le cache est trop vieux pour connaitre le match de ce soir ne
    sera pas rafraichie. Il faut alors passer par « Toutes les ligues ».
    """
    jour = jour or aujourdhui()
    codes = ligues_du_jour(jour)
    matchs: list[dict] = []
    lignes: list[dict] = []
    ecartes: list[dict] = []
    ligues: list[dict] = []
    ages: list[float] = []

    for code in codes:
        nom = config.nom_championnat(code)
        try:
            opportunites_ligue, age = opportunites(
                code, bankroll, forcer=forcer, jour=jour)
            matchs_ligue, ecartes_ligue, _ = affiches(code, jour=jour)
        except Exception as erreur:                   # une ligue muette, pas la page
            _journal.warning("Vue du jour, %s ecarte : %s", code, erreur)
            ligues.append({"code": code, "nom": nom, "matchs": 0,
                           "erreur": _message_erreur(erreur)})
            continue
        for match in matchs_ligue:
            match["ligue"] = code
        for opportunite in opportunites_ligue:
            opportunite["ligue"] = code
        matchs += matchs_ligue
        lignes += opportunites_ligue
        ecartes += ecartes_ligue
        ligues.append({"code": code, "nom": nom,
                       "matchs": len(matchs_ligue), "erreur": None})
        if age is not None:
            ages.append(age)

    matchs.sort(key=lambda m: m["date"])
    lignes.sort(key=lambda o: o["esperance"], reverse=True)
    return {
        "performance": None,
        "poids_modele": None,
        "poids_modele_defaut": config.POIDS_MELANGE_DEFAUT,
        "poids_mesure": False,
        "championnat": "@jour",
        "nom": f"tous les matchs du {jour}",
        "jour": jour,
        "multi": True,
        "ligues": ligues,
        "matchs": matchs,
        "ecartes": ecartes,
        "opportunites": lignes,
        "age_cotes_minutes": round(max(ages) / 60, 1) if ages else None,
        "quota": cotes_module.message_quota().splitlines()[0],
        "bankroll": bankroll,
        "exposition": journal_module.exposition(),
        "plafond_mise": round(bankroll * config.PLAFOND_MISE_BANKROLL, 2),
        "part_plafond": config.PLAFOND_MISE_BANKROLL,
        "seuil_alerte_jour": config.SEUIL_ALERTE_EXPOSITION_JOUR,
        "saisons": SAISONS,
        "genere": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "avertissement": config.AVERTISSEMENT_LEGAL,
        "capacites": list(CAPACITES),
    }


class ModeleParLigue:
    """Aiguille chaque match vers le modele de son championnat.

    Le module de combine ne demande qu'une chose a un modele :
    `matrice_scores(dom, ext)`. Un combine peut donc melanger les
    championnats sans que le calcul ait a le savoir.
    """

    def __init__(self, jambes: list[dict], defaut: str) -> None:
        self._modeles: dict[str, Any] = {}
        self._ligue: dict[tuple[str, str], str] = {}
        for jambe in jambes:
            ligue = (jambe.get("ligue") or defaut).upper()
            if ligue not in config.COMPETITIONS:
                raise ValueError(f"Championnat inconnu dans le combine : {ligue}")
            self._ligue[(jambe["dom"], jambe["ext"])] = ligue
            if ligue not in self._modeles:
                self._modeles[ligue] = CACHE.modele(ligue)[1]

    def matrice_scores(self, dom: str, ext: str, taille: int | None = None):
        return self._modeles[self._ligue[(dom, ext)]].matrice_scores(dom, ext)


def evaluer_combine(championnat: str, bankroll: float, jambes: list[dict]) -> dict:
    """Un combine, avec la probabilite jointe exacte et ses motifs de rejet.

    Les jambes peuvent venir de championnats differents : chacune porte le sien.
    """
    modele = ModeleParLigue(jambes, championnat)
    resultat = combine_module.evaluer(
        modele,
        [combine_module.Jambe(j["dom"], j["ext"], j["marche"], j["selection"],
                              float(j["cote"]),
                              None if j.get("p_finale") is None else float(j["p_finale"]))
         for j in jambes],
        bankroll)
    return {
        "jambes": [{"dom": j.dom, "ext": j.ext, "marche": j.marche,
                    "selection": j.selection, "cote": j.cote, "p_modele": p}
                   for j, p in zip(resultat.jambes, resultat.p_par_jambe)],
        "cote_totale": resultat.cote_totale,
        "p_modele": resultat.p_modele,
        "p_modele_seul": resultat.p_modele_seul,
        "recale": all(j.p_finale is not None for j in resultat.jambes),
        "p_independance": resultat.p_independance,
        "cote_equitable": resultat.cote_equitable,
        "esperance": resultat.esperance,
        "mise": resultat.mise,
        "gain_si_tout_passe": round(resultat.mise * resultat.cote_totale, 2),
        "retenu": resultat.retenu,
        "correlations": resultat.correlations,
        "motifs": resultat.motifs,
    }


def etat_des_apis() -> list[dict]:
    """Les trois services, avec leur consommation reelle.

    Deux services ont un compteur : The Odds API pour les cotes, API-Football
    pour les absences et les scores. football-data.org n'en a pas : il ne sert
    qu'aux resultats de Ligue des champions du modele des coupes d'Europe, un
    appel par saison toutes les six heures au plus, loin de son quota.
    """
    compteur = cotes_module.lire_compteur()
    consomme = cotes_module.credits_consommes(compteur)
    compteur_absences = absences_module.lire_compteur()
    lignes = []
    for service in config.services():
        ligne = {**service, "consomme": None, "restant": None,
                 "appels": None, "dernier_appel": None, "annonce_par_l_api": None}
        if service["nom"] == "api_football":
            # Quota quotidien, pas mensuel : un appel couvre toutes les ligues
            # d'une date, donc deux suffisent a couvrir la fenetre autorisee.
            ligne.update(
                consomme=compteur_absences["appels"],
                restant=max(0, int(service["limite"]) - compteur_absences["appels"]),
                appels=compteur_absences["appels"],
                dernier_appel=compteur_absences.get("dernier_appel"),
                annonce_par_l_api=compteur_absences.get("restant_api"),
                cout_par_appel=1,
                fenetre=absences_module.jours_couverts())
        elif service["nom"] == "football_data_org":
            ligne.update(usage="resultats de Ligue des champions (modele des coupes "
                               "d'Europe), un appel par saison toutes les 6 h au plus")
        elif service["nom"] == "odds":
            ligne.update(
                consomme=consomme,
                restant=max(0, int(service["limite"]) - consomme),
                appels=compteur["appels"],
                dernier_appel=compteur.get("dernier_appel"),
                annonce_par_l_api=compteur.get("restant_api"),
                cout_par_appel=cotes_module.COUT_CREDITS_PAR_APPEL,
                seuil_alerte=config.SEUIL_AVERTISSEMENT_QUOTA)
        lignes.append(ligne)
    return lignes


def _libelle_jour(jour: str | None) -> str:
    """« le 2026-08-21 » ou « dans les cotes en cache », selon qu'un jour est vise."""
    return f"le {jour}" if jour else "dans les cotes en cache"


def _cote_maximale(candidats: list) -> float:
    """Cote obtenue en empilant les candidats, les plus probables d'abord.

    Sans limite de jambes, c'est la cote de tous les favoris eligibles du jour :
    le plus haut que le combine du jour puisse atteindre.
    """
    plafond = 1.0
    for selection in sorted(candidats, key=lambda s: s.p_marche, reverse=True)[
        :MAX_JAMBES_COMBINE_JOUR
    ]:
        plafond *= selection.cote
    return round(plafond, 2)


# Deux credits par rencontre. Sans borne, une journee de quatre-vingts matchs
# consomme cent soixante credits — un tiers du budget mensuel en un clic. On ne
# demande les marches etendus que pour les rencontres qui ont une chance d'etre
# retenues : celles dont le favori 1N2 depasse deja le seuil.
PLAFOND_RENCONTRES_ETENDUES = 20


def _cotes_etendues(par_championnat: dict, jour: str | None,
                    seuil: float = 0.62) -> dict:
    """Ajoute 1X/12/X2 et GG/NG, mais seulement la ou ca peut servir.

    Une ligue qui echoue garde ses cotes de base plutot que de disparaitre.
    """
    # Les rencontres deja interessantes sur le 1N2, les plus probables d'abord.
    interessantes: dict[str, list] = {}
    for code, cotes in par_championnat.items():
        for candidat in quotidien_module.candidats(
                cotes, code, seuil=seuil, prime_minimale=0.0):
            if jour is None or _jour_local(candidat.date) == jour:
                interessantes.setdefault(code, []).append(candidat)
    classees = sorted(
        ((code, c) for code, liste in interessantes.items() for c in liste),
        key=lambda paire: paire[1].p_marche, reverse=True)[:PLAFOND_RENCONTRES_ETENDUES]
    retenues: dict[str, set] = {}
    for code, candidat in classees:
        retenues.setdefault(code, set()).add((candidat.dom, candidat.ext))
    _journal.info("Marches etendus : %d rencontre(s) retenue(s), soit %d credits.",
                  len(classees), len(classees) * cotes_module.COUT_CREDITS_MARCHES_ETENDUS)

    enrichi = {}
    for code, cotes in par_championnat.items():
        paires = retenues.get(code)
        if not paires:
            enrichi[code] = cotes
            continue
        matchs = cotes_module.matchs_a_venir(cotes)
        if not matchs.empty:
            matchs = matchs[[(str(l.dom), str(l.ext)) in paires
                             for l in matchs.itertuples()]]
        identifiants = _identifiants_evenements(code, matchs)
        if not identifiants:
            enrichi[code] = cotes
            continue
        try:
            supplement = cotes_module.recuperer_etendus(code, identifiants)
        except Exception as erreur:
            _journal.warning("Marches etendus %s : %s", code, erreur)
            enrichi[code] = cotes
            continue
        enrichi[code] = (pd.concat([cotes, supplement], ignore_index=True)
                         if not supplement.empty else cotes)
    return enrichi


def _identifiants_evenements(championnat: str, matchs) -> list[str]:
    """Identifiants The Odds API des rencontres retenues, lus du cache brut."""
    if matchs.empty:
        return []
    try:
        brut, _ = cotes_module.recuperer_brut(championnat)
    except Exception:
        return []
    voulus = {(str(ligne.dom), str(ligne.ext)) for ligne in matchs.itertuples()}
    identifiants = []
    for evenement in brut:
        dom = noms_module.canoniser(evenement.get("home_team", ""), "odds_api",
                                    identite_si_inconnu=True)
        ext = noms_module.canoniser(evenement.get("away_team", ""), "odds_api",
                                    identite_si_inconnu=True)
        if (dom, ext) in voulus and evenement.get("id"):
            identifiants.append(evenement["id"])
    return identifiants


def combine_du_jour(cible: float = 9.0, mise: float = 10.0,
                    jour: str | None = None, etendus: bool = False) -> dict:
    """Le combine de favoris qui atteint l'objectif de gain demande.

    Lit uniquement les cotes en cache : cliquer ce bouton ne doit jamais
    consommer de credit API. Les probabilites sont celles du marche, jamais
    celles du modele — le poids mesure de ce dernier est nul.

    `jour` (AAAA-MM-JJ local) restreint le combine a une seule journee. Sans
    lui, le combine empile ce qu'il trouve dans tout le cache — jusqu'a trois
    journees differentes en pratique, ce qui contredit son nom. La reponse
    porte donc toujours `jours_couverts` : la page doit pouvoir dire sur quoi
    l'utilisateur parie reellement.
    """
    par_championnat = {}
    for code in config.COMPETITIONS_AVEC_COTES:
        cotes, age = cotes_module.lire_cache(code)
        if not cotes.empty and age is not None:
            par_championnat[code] = cotes
    if not par_championnat:
        return {"erreur": "Aucune cote en cache. Utilise « Rafraichir les cotes »."}

    # Seuil abaisse et prime de prix non exigee : ici on cherche a EMPILER des
    # favoris, pas a trouver la meilleure affaire du jour. Ce sont deux
    # questions differentes et elles n'ont pas les memes filtres.
    if etendus:
        par_championnat = _cotes_etendues(par_championnat, jour)
        # Melange de marches : une seule jambe par rencontre, sinon « victoire
        # du domicile » et « domicile ou nul » se multiplieraient alors qu'ils
        # sont correles a 100 %.
        candidats = quotidien_module.du_jour_multi(par_championnat, seuil=0.62)
    else:
        candidats = quotidien_module.du_jour(
            par_championnat, seuil=0.62, prime_minimale=0.0)
    if jour is not None:
        # Le decoupage des journees est celui de `_jour_local`, donc le meme que
        # celui du navigateur : un match a 22h30 UTC ne doit pas tomber dans deux
        # jours differents selon le cote.
        candidats = [c for c in candidats if _jour_local(c.date) == jour]
    if not candidats:
        return {"erreur": f"Aucun favori eligible {_libelle_jour(jour)}. "
                          "Choisis un autre jour, ou rafraichis les cotes.",
                "jour": jour}

    # `au_mieux` : une journee ordinaire n'offre que deux ou trois favoris. Rendre
    # le meilleur empilement possible informe ; refuser de repondre ressemblerait
    # a une panne.
    combine = quotidien_module.combine_vers(
        candidats, cible=cible, mise=mise,
        maximum_jambes=MAX_JAMBES_COMBINE_JOUR, au_mieux=True)
    if combine is None:
        # L'objectif est annonce en euros d'abord : c'est ce que l'utilisateur a
        # saisi. La cote n'en est que le quotient, et « x9 » pour un x9.3 demande
        # ferait douter du chiffre affiche.
        return {"erreur": f"Impossible d'atteindre {cible * mise:.0f} EUR de gain "
                          f"(x{round(cible, 2):g}), meme en empilant les {len(candidats)} "
                          "favori(s) en cache. Rafraichis les cotes, "
                          "ou demande un gain plus modeste.",
                "jour": jour}

    annee = combine.sur_une_annee()
    jours_couverts = sorted({_jour_local(j.date) for j in combine.jambes})
    return {
        "jour": jour,
        "jours_couverts": jours_couverts,
        "objectif_atteint": combine.objectif_atteint,
        "cote_maximale": _cote_maximale(candidats),
        "maximum_jambes": MAX_JAMBES_COMBINE_JOUR,
        "candidats_du_jour": len(candidats),
        "marches_melanges": etendus,
        "cible": cible, "mise": combine.mise, "gain": combine.gain,
        "cote_totale": combine.cote_totale,
        "probabilite": combine.probabilite,
        "une_fois_sur": combine.une_fois_sur,
        "cote_equitable": combine.cote_equitable,
        "marge_payee": combine.marge_payee,
        "esperance": combine.esperance,
        "annee": annee,
        "roi_pari_simple": quotidien_module.ROI_BANDE_LONGUE,
        "roi_pari_simple_ic": list(quotidien_module.ROI_BANDE_LONGUE_IC),
        "roi_pari_simple_n": quotidien_module.ROI_BANDE_LONGUE_N,
        "candidats_disponibles": len(candidats),
        "jambes": [{
            "championnat": j.championnat, "dom": j.dom, "ext": j.ext,
            "selection": j.selection, "equipe": j.equipe,
            "p_marche": j.p_marche, "cote": j.cote, "bookmaker": j.bookmaker,
            "date": str(j.date),
        } for j in combine.jambes],
    }


# Les trois combines « prets » de l'onglet Equipes fortes. Trois objectifs
# fixes plutot qu'un curseur : c'est l'onglet de celui qui veut une reponse
# toute faite, le reglage fin reste dans « Combine du jour ».
PROFILS_COMBINE = (("Prudent", 2.0), ("Equilibre", 4.0), ("Audacieux", 8.0))


def _coup_d_envoi(date_match: Any) -> pd.Timestamp:
    """Instant UTC du match ; une date sans fuseau est lue comme UTC (The Odds API)."""
    instant = pd.Timestamp(date_match)
    return instant.tz_localize("UTC") if instant.tzinfo is None else instant


def _jambe(s: quotidien_module.Selection) -> dict:
    return {"championnat": s.championnat, "dom": s.dom, "ext": s.ext,
            "selection": s.selection, "equipe": s.equipe,
            "p_marche": s.p_marche, "cote": s.cote, "bookmaker": s.bookmaker,
            "nb_bookmakers": s.nb_bookmakers,
            # ISO avec un « T » : `new Date()` refuse l'espace sous Firefox.
            "date": pd.Timestamp(s.date).isoformat()}


# Les deux listes complementaires de l'onglet « Equipes fortes ». Un seuil de
# 50 % ne dit qu'une chose : le marche voit cette issue plus probable que son
# contraire. Ce n'est PAS la regle mesuree (70 %), et la page le dit.
SEUIL_LISTES_ELARGIES = 0.50


def _a_venir_du_jour(selections: list, jour: str | None) -> list:
    """Les selections dont le coup d'envoi n'est pas passe, du jour demande."""
    maintenant = pd.Timestamp.now(tz="UTC")
    retenues = [s for s in selections if _coup_d_envoi(s.date) > maintenant]
    if jour is not None:
        retenues = [s for s in retenues if _jour_local(s.date) == jour]
    return sorted(retenues, key=lambda s: s.p_marche, reverse=True)


def _avec_defaite(s: quotidien_module.Selection) -> dict:
    return _jambe(s) | {"perd_une_fois_sur": 1.0 / max(1e-9, 1.0 - s.p_marche)}


def listes_elargies(par_championnat: dict, jour: str | None) -> dict:
    """Vainqueurs a plus de 50 %, et le cote le plus probable du plus/moins 2.5.

    Probabilites du marche de-vigge, comme le reste de l'onglet : le modele ne
    fait pas mieux que lui (voir `paris/quotidien.py`).
    """
    vainqueurs, buts = [], []
    for code, cotes in par_championnat.items():
        vainqueurs += [s for s in quotidien_module.candidats(
                           cotes, code, seuil=SEUIL_LISTES_ELARGIES, prime_minimale=0.0)
                       if s.selection in ("1", "2")]          # le nul n'est pas une equipe
        buts += [s for s in quotidien_module.candidats_multi(
                     cotes, code, seuil=SEUIL_LISTES_ELARGIES)
                 if s.selection.startswith("plus_moins_2.5:")]
    return {"seuil_elargi": SEUIL_LISTES_ELARGIES,
            "vainqueurs": [_avec_defaite(s) for s in _a_venir_du_jour(vainqueurs, jour)],
            "buts": [_avec_defaite(s) for s in _a_venir_du_jour(buts, jour)]}


def equipes_fortes(jour: str | None = None, mise: float = 10.0) -> dict:
    """Les grands favoris du marche et trois combines prets, pour une journee.

    « Fort » veut dire que le marche donne au favori au moins
    `quotidien.SEUIL_PROBABILITE` — jamais qu'il gagnera. Chaque ligne porte
    donc sa frequence de defaite, et chaque combine sa probabilite jointe.
    Cache uniquement : ouvrir cet onglet ne consomme aucun credit API.
    """
    par_championnat = {}
    for code in config.COMPETITIONS_AVEC_COTES:
        cotes, age = cotes_module.lire_cache(code)
        if not cotes.empty and age is not None:
            par_championnat[code] = cotes
    if not par_championnat:
        return {"erreur": "Aucune cote en cache. Utilise « Rafraichir les cotes »."}

    # Prime non exigee : on classe des favoris par probabilite, pas par prix.
    # Le cache garde des matchs deja joues : un combine « pret » ne peut pas
    # contenir une rencontre dont le coup d'envoi est passe.
    fortes = _a_venir_du_jour(
        quotidien_module.du_jour(par_championnat, prime_minimale=0.0), jour)
    elargies = listes_elargies(par_championnat, jour)
    if not fortes and not elargies["vainqueurs"] and not elargies["buts"]:
        return {"erreur": f"Aucune equipe a plus de "
                          f"{SEUIL_LISTES_ELARGIES:.0%} selon le marche "
                          f"{_libelle_jour(jour)}. Choisis un autre jour, "
                          "ou rafraichis les cotes.",
                "jour": jour}

    combines, deja_vus = [], set()
    for nom, cible in PROFILS_COMBINE:
        combine = quotidien_module.combine_vers(
            fortes, cible=cible, mise=mise, maximum_jambes=MAX_JAMBES_COMBINE_JOUR)
        if combine is None:
            continue
        # Une journee pauvre donne parfois le meme empilement pour deux
        # objectifs : le montrer deux fois sous deux noms serait trompeur.
        cle = tuple((str(j.date), j.dom, j.ext) for j in combine.jambes)
        if cle in deja_vus:
            continue
        deja_vus.add(cle)
        combines.append({
            "nom": nom, "cible": cible, "mise": combine.mise, "gain": combine.gain,
            "cote_totale": combine.cote_totale,
            "probabilite": combine.probabilite,
            "une_fois_sur": combine.une_fois_sur,
            "marge_payee": combine.marge_payee,
            "esperance": combine.esperance,
            "jambes": [_jambe(j) for j in combine.jambes],
        })

    return {
        "jour": jour,
        "jours_couverts": sorted({_jour_local(j["date"]) for j in (
            [_jambe(s) for s in fortes] + elargies["vainqueurs"] + elargies["buts"])}),
        "seuil": quotidien_module.SEUIL_PROBABILITE,
        "roi_mesure": quotidien_module.ROI_MESURE,
        "roi_ic": list(quotidien_module.ROI_IC),
        "roi_nb_paris": quotidien_module.ROI_NB_PARIS,
        "mise": mise,
        "fortes": [_avec_defaite(s) for s in fortes],
        "combines": combines,
        **elargies,
    }


# --------------------------------------------------------------------------
# Pronos surs : le prono le plus sur de chaque match, et son taux mesure
# --------------------------------------------------------------------------

_MESURES_EN_COURS: set[str] = set()
_VERROU_MESURES = threading.Lock()


def _lancer_mesures(codes: list[str]) -> list[str]:
    """Calcule en arriere-plan les taux de reussite manquants. Renvoie ceux en cours.

    Une mesure coute de 5 a 25 secondes par championnat : la faire pendant la
    requete bloquerait la page une minute. La page redemande un peu plus tard.
    """
    with _VERROU_MESURES:
        a_faire = [c for c in dict.fromkeys(codes)
                   if c in config.CHAMPIONNATS and c not in _MESURES_EN_COURS
                   and reussite_module.en_cache(c) is None]
        _MESURES_EN_COURS.update(a_faire)
        en_cours = sorted(_MESURES_EN_COURS)
    if a_faire:
        def travail() -> None:
            for code in a_faire:
                try:
                    reussite_module.mesurer(code, SAISONS)
                except Exception as erreur:          # une ligue muette, pas les autres
                    _journal.warning("Taux de reussite %s non mesure : %s", code, erreur)
                finally:
                    with _VERROU_MESURES:
                        _MESURES_EN_COURS.discard(code)
        threading.Thread(target=travail, daemon=True, name="mesure-reussite").start()
    return en_cours


def _choix_sans_cotes(code: str, match: dict) -> dict | None:
    """Le choix le plus sur d'un match sans bookmaker, aux cotes equitables du modele.

    Meme regle que pour les ligues cotees (cote d'au moins 1.20, probabilite
    d'au moins 60 %), mais le prix est 1/p : ce n'est pas un prix jouable, et
    la page le dit.
    """
    _, modele = CACHE.modele(code)
    p = modele.predire(match["dom"], match["ext"])
    issues, totaux = p.get("1N2") or {}, p.get("totaux") or {}
    equitables = {cle: 1.0 / v for cle, v in (
        ("1", issues.get("1")), ("N", issues.get("N")), ("2", issues.get("2")),
        ("plus", totaux.get("plus_2.5")), ("moins", totaux.get("moins_2.5")))
        if v and v > 0}
    return confiance_module.choix_le_plus_sur(p, match["dom"], match["ext"], {}, equitables)


def pronos_surs(jour: str | None = None, attendre: bool = False) -> dict:
    """Pour chaque match d'une journee : le prono le plus sur et son taux de reussite.

    Deux taux, a ne pas confondre :
      - `probabilite` : ce que le modele (melange au marche s'il y a des cotes)
        annonce pour CE match ;
      - `mesure` : la part des pronos du meme type, annonces dans la meme
        tranche de dix points, qui sont reellement passes sur les saisons
        passees (walk-forward, sans voir le futur).
    `attendre` calcule les mesures manquantes avant de repondre (publication) ;
    sinon elles partent en arriere-plan et `mesures_en_cours` les nomme.
    Cache uniquement : aucun credit The Odds API.
    """
    jour = jour or aujourdhui()
    codes = ligues_du_jour(jour)
    if attendre:
        for code in codes:
            if code in config.CHAMPIONNATS and reussite_module.en_cache(code) is None:
                try:
                    reussite_module.mesurer(code, SAISONS)
                except Exception as erreur:
                    _journal.warning("Taux de reussite %s non mesure : %s", code, erreur)
        en_cours: list[str] = []
    else:
        en_cours = _lancer_mesures(codes)
    tables = {c: reussite_module.en_cache(c) for c in config.CHAMPIONNATS}
    toutes = [t for t in tables.values() if t]

    lignes: list[dict] = []
    sans_choix = 0
    ligues: list[dict] = []
    for code in codes:
        try:
            matchs, _, _ = affiches(code, jour=jour)
        except Exception as erreur:                   # une ligue muette, pas la page
            ligues.append({"code": code, "nom": config.nom_championnat(code),
                           "erreur": _message_erreur(erreur)})
            continue
        ligues.append({"code": code, "nom": config.nom_championnat(code), "erreur": None})
        sans_cotes = config.sans_cotes(code)
        for match in matchs:
            try:
                sur = _choix_sans_cotes(code, match) if sans_cotes else match.get("choix_sur")
            except Exception as erreur:
                _journal.info("Choix sur %s indisponible : %s", code, erreur)
                sur = None
            choix = (sur or {}).get("choix")
            if not choix:
                sans_choix += 1
                continue
            resultat = match.get("resultat")
            verdict = (verdict_module.verifier(choix["marche"], choix["selection"], resultat)
                       if resultat else None)
            lignes.append({
                "ligue": code, "nom_ligue": config.nom_championnat(code),
                "date": match["date"], "dom": match["dom"], "ext": match["ext"],
                "statut": match.get("statut"), "resultat": resultat,
                "direct": match.get("direct"),
                "libelle": choix["libelle"], "marche": choix["marche"],
                "selection": choix["selection"],
                "probabilite": choix["p_finale"], "p_modele": choix["p_modele"],
                "p_marche": choix.get("p_marche"),
                "cote": choix["cote"], "cote_reelle": not sans_cotes,
                "echoue_1_fois_sur": choix["echoue_1_fois_sur"],
                "sans_reserve": bool(sur.get("sur")),
                "reserves": sur.get("reserves", []),
                # La tranche est celle du taux AFFICHE : comparer un prono annonce
                # a 80 % aux pronos de la tranche 70-80 % serait incomprehensible.
                "mesure": reussite_module.taux(choix["marche"], choix["p_finale"],
                                               tables.get(code), toutes),
                "verdict": verdict,
            })

    lignes.sort(key=lambda l: l["probabilite"], reverse=True)
    verdicts = [l["verdict"] for l in lignes if l["verdict"]]
    return {
        "jour": jour,
        "pronos": lignes,
        "sans_choix": sans_choix,
        "ligues": ligues,
        "bilan": verdict_module.bilan(verdicts),
        "mesures_en_cours": en_cours,
        "seuil": confiance_module.SEUIL_CHOIX_SUR,
        "cote_minimale": confiance_module.COTE_MINIMALE_SURE,
        "min_pronos_mesure": reussite_module.MIN_PRONOS_MESURE,
        "genere": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def championnats() -> list[dict]:
    """Les championnats, avec l'age de leurs cotes en cache.

    L'age dit ce qui est consultable sans depenser un credit. La page s'en sert
    pour n'interroger que les ligues qui ont deja des cotes : entrainer un
    modele pour une ligue dont on n'a aucune cote ne servirait a rien.

    Sont proposees les ligues cotees par The Odds API, et celles dont ESPN donne
    le calendrier (`sans_cotes`, pronos du modele seul). Les autres (National
    League, divisions ecossaises inferieures) n'existent ici que comme historique
    du modele inter-divisions : on n'aurait aucun programme a montrer.
    """
    lignes = []
    for code in config.COMPETITIONS_AFFICHEES:
        info = config.COMPETITIONS[code]
        sans_cotes = config.sans_cotes(code)
        age = None if sans_cotes else cotes_module.age_du_cache(code)
        lignes.append({"code": code, "nom": info["nom"], "pays": info["pays"],
                       "sans_cotes": sans_cotes,
                       "age_cotes_minutes": round(age / 60, 1) if age is not None else None})
    return lignes


# --------------------------------------------------------------------------
# Serveur
# --------------------------------------------------------------------------

def _jour_demande(parametres: dict[str, list[str]]) -> tuple[str | None, str | None]:
    """Le parametre `jour` valide, ou le message a renvoyer tel quel.

    Un seul controle pour toutes les routes qui acceptent une journee : deux
    validations paralleles finiraient par diverger, et l'une accepterait ce que
    l'autre refuse.
    """
    jour = (parametres.get("jour", [""])[0] or "").strip() or None
    if jour and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", jour):
        return None, f"Jour illisible : {jour} (attendu AAAA-MM-JJ)."
    return jour, None


def _message_erreur(erreur: Exception) -> str:
    """Message actionnable plutot qu'une trace, comme dans l'interface tkinter."""
    if isinstance(erreur, config.CleApiManquante):
        return str(erreur)
    if isinstance(erreur, (ValueError, KeyError, RuntimeError)):
        return str(erreur).strip("'")
    if isinstance(erreur, OSError):
        return ("Probleme reseau ou disque : " + str(erreur)
                + " — les donnees deja telechargees restent utilisables.")
    return f"{type(erreur).__name__} : {erreur}"


def _sans_nan(valeur: Any) -> Any:
    """NaN et infinis en None : json.dumps les ecrit tels quels, JSON.parse les refuse.

    Un championnat sans colonne de corners (ou une case vide) donne un NaN
    pandas, qui faisait tomber toute la reponse cote navigateur.
    """
    if isinstance(valeur, float):
        return valeur if math.isfinite(valeur) else None
    if isinstance(valeur, dict):
        return {cle: _sans_nan(v) for cle, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_sans_nan(v) for v in valeur]
    return valeur


class Gestionnaire(BaseHTTPRequestHandler):
    """Quatre routes : la page, la liste des championnats, l'etat, le rafraichissement."""

    server_version = "foot_edge"

    def do_GET(self) -> None:
        route = urlparse(self.path)
        if route.path == "/":
            self._page()
        elif route.path in STATIQUES:
            self._statique(*STATIQUES[route.path])
        elif route.path == "/api/championnats":
            self._json(200, {"championnats": championnats()})
        elif route.path == "/api/apis":
            self._json(200, {"services": etat_des_apis()})
        elif route.path == "/api/combine-du-jour":
            parametres = parse_qs(route.query)
            try:
                cible = float(parametres.get("cible", ["9"])[0])
                mise = float(parametres.get("mise", ["10"])[0])
            except ValueError:
                self._json(400, {"erreur": "Objectif ou mise illisible."})
                return
            if not (1.5 <= cible <= 1000) or mise <= 0:
                self._json(400, {"erreur": "Objectif hors bornes (1.5 a 1000)."})
                return
            jour, erreur = _jour_demande(parametres)
            if erreur:
                self._json(400, {"erreur": erreur})
                return
            etendus = parametres.get("etendus", ["0"])[0] in ("1", "true", "oui")
            try:
                self._json(200, combine_du_jour(cible, mise, jour, etendus))
            except Exception as souci:            # jamais une connexion coupee
                _journal.warning("%s : %s", type(souci).__name__, souci)
                self._json(500, {"erreur": _message_erreur(souci)})
        elif route.path == "/api/equipes-fortes":
            parametres = parse_qs(route.query)
            try:
                mise = float(parametres.get("mise", ["10"])[0])
            except ValueError:
                mise = 0.0
            if mise <= 0:
                self._json(400, {"erreur": "Mise illisible."})
                return
            jour, erreur = _jour_demande(parametres)
            if erreur:
                self._json(400, {"erreur": erreur})
                return
            try:
                self._json(200, equipes_fortes(jour, mise))
            except Exception as souci:            # jamais une connexion coupee
                _journal.warning("%s : %s", type(souci).__name__, souci)
                self._json(500, {"erreur": _message_erreur(souci)})
        elif route.path == "/api/pronos-surs":
            jour, erreur = _jour_demande(parse_qs(route.query))
            if erreur:
                self._json(400, {"erreur": erreur})
                return
            try:
                self._json(200, pronos_surs(jour))
            except Exception as souci:            # jamais une connexion coupee
                _journal.warning("%s : %s", type(souci).__name__, souci)
                self._json(500, {"erreur": _message_erreur(souci)})
        elif route.path == "/api/jour":
            self._etat(parse_qs(route.query), forcer=False)
        elif route.path == "/api/statuts":
            codes = [c.strip().upper() for c in
                     (parse_qs(route.query).get("ligues", [""])[0] or "").split(",")
                     if c.strip()]
            inconnus = [c for c in codes if c not in config.COMPETITIONS]
            if not codes or inconnus or len(codes) > len(config.COMPETITIONS):
                self._json(400, {"erreur": "Ligues illisibles : "
                                 + (", ".join(inconnus) or "aucune donnee")})
            else:
                try:
                    self._json(200, statuts_du_jour(list(dict.fromkeys(codes))))
                except Exception as souci:        # jamais une connexion coupee
                    _journal.warning("%s : %s", type(souci).__name__, souci)
                    self._json(500, {"erreur": _message_erreur(souci)})
        elif route.path == "/api/aujourdhui":
            self._aujourdhui(parse_qs(route.query))
        else:
            self._json(404, {"erreur": "Adresse inconnue."})

    def do_POST(self) -> None:
        route = urlparse(self.path)
        if route.path == "/api/rafraichir":
            self._etat(parse_qs(route.query), forcer=True)
        elif route.path == "/api/aujourdhui":
            self._aujourdhui(parse_qs(route.query), forcer=True)
        elif route.path == "/api/combine":
            self._combine(parse_qs(route.query))
        else:
            self._json(404, {"erreur": "Adresse inconnue."})

    # -- routes ------------------------------------------------------------

    def _page(self) -> None:
        try:
            contenu = PAGE.read_bytes()
        except OSError:
            self._json(500, {"erreur": f"Page introuvable : {PAGE}"})
            return
        self._envoyer(200, "text/html; charset=utf-8", contenu)

    def _statique(self, nom: str, type_mime: str) -> None:
        try:
            contenu = (DOSSIER_WEB / nom).read_bytes()
        except OSError:
            self._json(404, {"erreur": f"Fichier introuvable : {nom}"})
            return
        self._envoyer(200, type_mime, contenu)

    def _etat(self, parametres: dict[str, list[str]], forcer: bool) -> None:
        championnat = (parametres.get("championnat", ["F1"])[0] or "F1").upper()
        if championnat not in config.COMPETITIONS:
            self._json(400, {"erreur": f"Championnat inconnu : {championnat}"})
            return
        try:
            bankroll = float(parametres.get("bankroll", ["1000"])[0])
        except ValueError:
            self._json(400, {"erreur": "Bankroll illisible : attendu un nombre."})
            return
        if bankroll <= 0:
            self._json(400, {"erreur": "La bankroll doit etre strictement positive."})
            return
        try:
            self._json(200, etat_du_jour(championnat, bankroll, forcer))
        except Exception as erreur:                       # remonte lisiblement
            _journal.warning("%s : %s", type(erreur).__name__, erreur)
            self._json(500, {"erreur": _message_erreur(erreur)})

    def _aujourdhui(self, parametres: dict[str, list[str]],
                    forcer: bool = False) -> None:
        """La journee en cours, toutes ligues. En GET, ne depense aucun credit."""
        try:
            bankroll = float(parametres.get("bankroll", ["1000"])[0])
        except ValueError:
            self._json(400, {"erreur": "Bankroll illisible : attendu un nombre."})
            return
        if bankroll <= 0:
            self._json(400, {"erreur": "La bankroll doit etre strictement positive."})
            return
        jour, erreur = _jour_demande(parametres)
        if erreur:
            self._json(400, {"erreur": erreur})
            return
        try:
            self._json(200, etat_aujourdhui(bankroll, jour, forcer))
        except Exception as erreur:
            _journal.warning("%s : %s", type(erreur).__name__, erreur)
            self._json(500, {"erreur": _message_erreur(erreur)})

    def _combine(self, parametres: dict[str, list[str]]) -> None:
        championnat = (parametres.get("championnat", ["F1"])[0] or "F1").upper()
        try:
            bankroll = float(parametres.get("bankroll", ["1000"])[0])
            taille = int(self.headers.get("Content-Length") or 0)
            jambes = json.loads(self.rfile.read(taille) or b"[]")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"erreur": "Requete illisible."})
            return
        if championnat not in config.COMPETITIONS or bankroll <= 0:
            self._json(400, {"erreur": "Championnat ou bankroll invalide."})
            return
        try:
            self._json(200, evaluer_combine(championnat, bankroll, jambes))
        except Exception as erreur:
            _journal.warning("%s : %s", type(erreur).__name__, erreur)
            self._json(400, {"erreur": _message_erreur(erreur)})

    # -- envoi -------------------------------------------------------------

    def _json(self, code: int, charge: dict) -> None:
        corps = json.dumps(_sans_nan(charge), ensure_ascii=False, default=str,
                           allow_nan=False).encode("utf-8")
        self._envoyer(code, "application/json; charset=utf-8", corps)

    def _envoyer(self, code: int, type_mime: str, corps: bytes) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", type_mime)
            self.send_header("Content-Length", str(len(corps)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corps)
        except (BrokenPipeError, ConnectionResetError):    # client parti en cours de route
            _journal.debug("Client deconnecte avant la fin de la reponse.")
            self.close_connection = True

    def log_message(self, format: str, *args) -> None:    # noqa: A002
        _journal.info("%s %s", self.address_string(), format % args)


def adresse_locale() -> str | None:
    """L'adresse du PC sur le reseau local, celle que le telephone doit viser."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))       # aucun paquet n'est envoye
            return s.getsockname()[0]
        except OSError:
            return None


def servir(port: int = PORT, ouvrir: bool = False, reseau: bool = False) -> None:
    hote = "0.0.0.0" if reseau else HOTE
    serveur = ThreadingHTTPServer((hote, port), Gestionnaire)
    adresse = f"http://{HOTE}:{port}"
    print(f"foot_edge — consultation sur {adresse}")
    if reseau:
        ip = adresse_locale()
        print(f"  Sur le telephone (meme Wi-Fi) : http://{ip or '<ip-du-pc>'}:{port}")
        print(f"  Application Android : http://{ip or '<ip-du-pc>'}:{port}/RAB3OUNSTAT.apk")
        print("  Attention : toute machine du reseau local peut ouvrir la page")
        print("  et lancer un rafraichissement des cotes (credits API).")
        print("  Ctrl+C pour arreter.")
    else:
        print("  Ctrl+C pour arreter. Rien n'est expose au reseau.")
    if ouvrir:
        threading.Timer(0.5, lambda: webbrowser.open(adresse)).start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
    finally:
        serveur.server_close()


def main(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--port", "-p", type=int, default=PORT)
    analyseur.add_argument("--ouvrir", "-o", action="store_true",
                           help="ouvrir le navigateur au demarrage")
    analyseur.add_argument("--reseau", "-r", action="store_true",
                           help="ecouter sur le reseau local (telephone)")
    analyseur.add_argument("--verbeux", "-v", action="store_true")
    args = analyseur.parse_args(arguments)
    logging.basicConfig(level=logging.INFO if args.verbeux else logging.WARNING,
                        format="%(message)s")
    servir(args.port, args.ouvrir, args.reseau)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
