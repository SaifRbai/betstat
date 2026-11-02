"""Resultats et calendrier des championnats absents de football-data.co.uk, via ESPN.

ESPN publie sans cle ni quota le tableau des scores de nombreux championnats
que ni football-data.co.uk ni le plan gratuit d'API-Football (bloque sur 2022-
2024) ne couvrent : Colombie, Arabie saoudite, Chili, Equateur, Perou...

Un seul appel par annee civile (`scoreboard?dates=AAAA`) renvoie tous les
matchs de l'annee, joues ou a venir. Il sert deux fois :
  - les matchs termines deviennent un CSV au format football-data, qui suit
    ensuite exactement le meme chemin que les autres historiques ;
  - les matchs a venir donnent le calendrier des ligues que The Odds API ne
    cote pas. Sans lui, ces ligues n'auraient aucun match a afficher : c'est le
    flux de cotes qui fournit le programme de toutes les autres.

L'API n'est pas documentee publiquement. Si elle change de forme, les
fonctions ci-dessous levent une erreur que l'appelant traite comme une
coupure reseau : on retombe sur le cache, jamais sur une trace d'exception.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

import config

_journal = logging.getLogger(__name__)

URL_TABLEAU = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
DELAI_RESEAU = 25
# Le calendrier d'une ligue sans cotes bouge peu : une heure suffit, et la
# page peut etre rechargee sans relancer d'appel.
TTL_CALENDRIER_SECONDES = 3600

COLONNES_CSV = ["Div", "Date", "Time", "HomeTeam", "AwayTeam",
                "FTHG", "FTAG", "FTR"]
COLONNES_CALENDRIER = ["date", "dom", "ext", "nb_bookmakers",
                       "statut", "buts_dom", "buts_ext"]


def slug(ligue: str) -> str | None:
    """Identifiant ESPN du championnat ("col.1"), ou None s'il n'en a pas."""
    return config.CHAMPIONNATS.get(ligue.upper(), {}).get("espn")


# --------------------------------------------------------------------------
# Appel et cache brut
# --------------------------------------------------------------------------

def _chemin_annee(ligue: str, annee: int) -> Path:
    return config.DOSSIER_CACHE_RESULTATS / f"{ligue.upper()}_espn_{annee}.json"


def _appeler(identifiant: str, annee: int) -> list[dict]:
    reponse = requests.get(URL_TABLEAU.format(slug=identifiant),
                           params={"dates": str(annee), "limit": 1000},
                           timeout=DELAI_RESEAU)
    reponse.raise_for_status()
    evenements = reponse.json().get("events")
    if not isinstance(evenements, list):
        raise ValueError("reponse ESPN sans liste d'evenements")
    return evenements


def evenements(ligue: str, annee: int, ttl: float | None = None) -> list[dict]:
    """Tous les matchs ESPN d'une annee civile, depuis le cache si possible.

    Une annee passee ne change plus : son cache ne perime jamais. L'annee en
    cours (et la suivante, deja programmee en decembre) vit `ttl` secondes.
    En cas d'echec reseau, le dernier cache est rendu tel quel ; sans cache,
    l'erreur remonte a l'appelant.
    """
    identifiant = slug(ligue)
    if identifiant is None:
        raise ValueError(f"Pas d'identifiant ESPN pour {ligue}.")
    config.creer_dossiers()
    chemin = _chemin_annee(ligue, annee)
    if ttl is None:
        ttl = config.TTL_SAISON_COURANTE_SECONDES
    if chemin.exists():
        age = time.time() - chemin.stat().st_mtime
        if annee < date.today().year or age < ttl:
            return json.loads(chemin.read_text(encoding="utf-8"))
    try:
        donnees = _appeler(identifiant, annee)
    except Exception as erreur:
        if chemin.exists():
            _journal.warning("ESPN injoignable pour %s %s (%s) : lecture du cache.",
                             ligue, annee, type(erreur).__name__)
            return json.loads(chemin.read_text(encoding="utf-8"))
        raise
    chemin.write_text(json.dumps(donnees), encoding="utf-8")
    _journal.info("ESPN %s %s : %d matchs", ligue, annee, len(donnees))
    return donnees


# --------------------------------------------------------------------------
# Lecture d'un evenement
# --------------------------------------------------------------------------

def _match(evenement: dict) -> dict | None:
    """Les champs utiles d'un evenement ESPN, ou None s'il est illisible."""
    try:
        competition = evenement["competitions"][0]
        equipes = {e["homeAway"]: e for e in competition["competitors"]}
        dom, ext = equipes["home"], equipes["away"]
        statut = evenement["status"]["type"]
        coup_envoi = datetime.fromisoformat(evenement["date"].replace("Z", "+00:00"))
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    def buts(equipe: dict) -> int | None:
        try:
            return int(equipe.get("score"))
        except (TypeError, ValueError):
            return None

    if any(n.upper().startswith("TBD") for n in (_brut(dom), _brut(ext))):
        return None                       # affiche pas encore connue (barrages)
    termine = bool(statut.get("completed")) and statut.get("state") == "post"
    return {
        "coup_envoi": coup_envoi.astimezone(timezone.utc),
        "dom": _nom(dom), "ext": _nom(ext),
        "etat": statut.get("state"),          # pre, in, post
        "nom_statut": statut.get("name", ""),
        "termine": termine,
        "buts_dom": buts(dom), "buts_ext": buts(ext),
    }


def _brut(equipe: dict) -> str:
    return str(equipe.get("team", {}).get("displayName", "")).strip()


def _nom(equipe: dict) -> str:
    """Nom ESPN -> orthographe football-data quand elle existe, sinon canonique.

    Un club relegue de Serie A garde ainsi le meme nom dans les deux divisions,
    ce qui permet au modele inter-divisions de s'en servir comme pont.
    """
    from data import noms as noms_module

    brut = str(equipe.get("team", {}).get("displayName", "")).strip()
    canonique = noms_module.canoniser(brut, "espn")
    if canonique is None:
        return brut
    alias = noms_module.alias_connus(canonique, "football_data")
    return alias[0] if alias else canonique


# --------------------------------------------------------------------------
# Historique : CSV au format football-data
# --------------------------------------------------------------------------

def _bornes_saison(ligue: str, saison: str) -> tuple[date, date]:
    """[debut, fin) d'une saison : annee civile "2026", ou "2526" de juillet a juillet."""
    if len(saison) == 4 and saison.startswith("20") and config.CHAMPIONNATS[
            ligue.upper()].get("annee_civile"):
        annee = int(saison)
        return date(annee, 1, 1), date(annee + 1, 1, 1)
    debut = 2000 + int(saison[:2])
    return date(debut, 7, 1), date(debut + 1, 7, 1)


def csv_saison(ligue: str, saison: str) -> bytes:
    """Matchs termines d'une saison, au format CSV football-data.

    Une saison a cheval (Arabie saoudite, "2526") couvre deux annees civiles :
    deux appels, chacun mis en cache separement.
    """
    debut, fin = _bornes_saison(ligue, saison)
    lignes = []
    for annee in sorted({debut.year, (fin - timedelta(days=1)).year}):
        if annee > date.today().year:
            continue
        for evenement in evenements(ligue, annee):
            match = _match(evenement)
            if match is None or not match["termine"]:
                continue
            if match["buts_dom"] is None or match["buts_ext"] is None:
                continue
            if not (debut <= match["coup_envoi"].date() < fin):
                continue
            bd, be = match["buts_dom"], match["buts_ext"]
            lignes.append({
                "Div": ligue.upper(),
                "Date": match["coup_envoi"].strftime("%d/%m/%Y"),
                "Time": match["coup_envoi"].strftime("%H:%M"),
                "HomeTeam": match["dom"], "AwayTeam": match["ext"],
                "FTHG": bd, "FTAG": be,
                "FTR": "H" if bd > be else "A" if bd < be else "D",
            })
    if not lignes:
        # Un en-tete seul : la saison n'a pas commence. L'appelant le traite
        # comme une saison pas encore publiee, sans crier a la panne.
        return b""
    return pd.DataFrame(lignes, columns=COLONNES_CSV).to_csv(index=False).encode("utf-8")


# --------------------------------------------------------------------------
# Calendrier des ligues sans cotes
# --------------------------------------------------------------------------

def calendrier(ligue: str, depuis: datetime | None = None) -> pd.DataFrame:
    """Matchs a partir de `depuis` (UTC), au format de `cotes.matchs_a_venir`.

    `nb_bookmakers` vaut 0 : aucun bookmaker ne cote ces matchs, et la page
    doit le savoir. Les colonnes de score servent aux matchs deja joues de la
    journee, dont ESPN donne le resultat le soir meme. Ne leve jamais : sans
    reseau ni cache, renvoie un tableau vide.
    """
    depuis = depuis or datetime.now(timezone.utc)
    annee = depuis.year
    lignes = []
    for an in (annee, annee + 1) if depuis.month == 12 else (annee,):
        try:
            donnees = evenements(ligue, an, ttl=TTL_CALENDRIER_SECONDES)
        except Exception as erreur:
            _journal.warning("Calendrier ESPN %s %s indisponible : %s", ligue, an, erreur)
            continue
        for evenement in donnees:
            match = _match(evenement)
            if match is None or match["coup_envoi"] < depuis:
                continue
            if match["nom_statut"] in ("STATUS_POSTPONED", "STATUS_CANCELED",
                                       "STATUS_ABANDONED"):
                continue
            lignes.append({
                "date": pd.Timestamp(match["coup_envoi"]),   # meme forme que les cotes
                "dom": match["dom"], "ext": match["ext"], "nb_bookmakers": 0,
                "statut": match["etat"],
                "buts_dom": match["buts_dom"] if match["etat"] != "pre" else None,
                "buts_ext": match["buts_ext"] if match["etat"] != "pre" else None,
            })
    if not lignes:
        return pd.DataFrame(columns=COLONNES_CALENDRIER)
    return (pd.DataFrame(lignes, columns=COLONNES_CALENDRIER)
            .drop_duplicates(subset=["date", "dom", "ext"])
            .sort_values("date").reset_index(drop=True))


def age_du_calendrier(ligue: str) -> float | None:
    """Age en secondes du cache de l'annee en cours, ou None s'il n'existe pas."""
    chemin = _chemin_annee(ligue, date.today().year)
    if not chemin.exists():
        return None
    return time.time() - chemin.stat().st_mtime
