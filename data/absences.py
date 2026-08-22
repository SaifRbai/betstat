"""Blessures, suspensions et compositions via API-Football (v3).

Ce module comble le trou que le reste du projet nommait sans le combler :
paris/valeur.py explique une divergence modele/marche par « une information qui
manque au modele (blessure, composition, match sans enjeu) », sans jamais
pouvoir dire laquelle. Ici on va la chercher.

Ce que le plan gratuit autorise reellement, mesure et non suppose :

  - **100 requetes par jour.** Un appel `/injuries?date=` renvoie TOUTES les
    ligues de cette date d'un coup : deux appels couvrent donc aujourd'hui et
    demain, pour toutes les competitions suivies.
  - **Une fenetre de deux jours seulement** : aujourd'hui et demain. Au-dela,
    l'API repond « Free plans do not have access to this date ». Un match de
    samedi consulte le mercredi n'a donc PAS d'information disponible, ce qui
    est different de « aucun joueur absent ». Cette distinction est portee
    partout par `DISPONIBILITE_*` : la confondre serait faire passer une
    ignorance pour une bonne nouvelle.
  - **Les compositions n'existent qu'a l'approche du coup d'envoi** (environ
    une heure avant). Avant, l'API renvoie une liste vide, legitimement.

Les absences ne modifient aucune probabilite. Ajuster un lambda parce que trois
joueurs manquent demanderait une estimation de la contribution de chaque joueur,
que ce projet n'a pas. Elles sont donc rendues comme du contexte, et servent a
degrader la confiance d'un prono, pas a fabriquer un chiffre.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from data import noms as noms_module

_journal = logging.getLogger("foot_edge.absences")

URL_BASE = "https://v3.football.api-sports.io"
DELAI_RESEAU = 20
TTL_SECONDES = 3 * 3600          # les forfaits bougent peu dans la journee
FENETRE_JOURS = 1                # aujourd'hui + demain, limite du plan gratuit

DISPONIBILITE_OK = "disponible"
DISPONIBILITE_HORS_FENETRE = "hors_fenetre"
DISPONIBILITE_INDISPONIBLE = "indisponible"

# Motifs renvoyes par l'API qui ne sont pas des blessures : les distinguer
# change la lecture, une suspension etant certaine la ou une blessure ne l'est pas.
MOTIFS_SUSPENSION = ("suspended", "red card", "yellow cards")


class QuotaEpuise(RuntimeError):
    """Levee quand le quota quotidien local est atteint."""


class HorsFenetre(RuntimeError):
    """Levee quand la date demandee depasse ce que le plan gratuit couvre."""


# --------------------------------------------------------------------------
# Compteur quotidien
# --------------------------------------------------------------------------

def _chemin_compteur() -> Path:
    return config.DOSSIER_DONNEES / "quota_api_football.json"


def _jour_courant() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def lire_compteur() -> dict[str, Any]:
    """Etat du quota quotidien. Repart de zero chaque jour."""
    defaut = {"jour": _jour_courant(), "appels": 0, "restant_api": None,
              "dernier_appel": None}
    chemin = _chemin_compteur()
    if not chemin.exists():
        return defaut
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaut
    if donnees.get("jour") != _jour_courant():
        return defaut
    return {**defaut, **donnees}


def _incrementer_compteur(restant_api: int | None) -> dict[str, Any]:
    etat = lire_compteur()
    etat["appels"] += 1
    etat["restant_api"] = restant_api
    etat["dernier_appel"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config.creer_dossiers()
    _chemin_compteur().write_text(json.dumps(etat, indent=2), encoding="utf-8")
    return etat


def enregistrer_appel(restant_api: int | None) -> dict[str, Any]:
    """Compte un appel API-Football fait par un autre module.

    `data/scores.py` interroge la meme cle avec le meme budget de cent appels
    par jour : deux compteurs separes laisseraient chacun croire qu'il lui en
    reste, et le quota sauterait sans prevenir.
    """
    return _incrementer_compteur(restant_api)


def message_quota(etat: dict[str, Any] | None = None) -> str:
    etat = etat or lire_compteur()
    texte = (f"Quota API-Football : {etat['appels']} appel(s) aujourd'hui sur "
             f"{config.QUOTA_QUOTIDIEN_API_FOOTBALL}")
    if etat.get("restant_api") is not None:
        texte += f" — l'API annonce {etat['restant_api']} restant(s)"
    return texte


# --------------------------------------------------------------------------
# Fenetre autorisee
# --------------------------------------------------------------------------

def jours_couverts(aujourd_hui: date | None = None) -> list[str]:
    """Les dates que le plan gratuit accepte : aujourd'hui et demain."""
    base = aujourd_hui or datetime.now(timezone.utc).date()
    return [str(base + timedelta(days=n)) for n in range(FENETRE_JOURS + 1)]


def dans_la_fenetre(jour: str, aujourd_hui: date | None = None) -> bool:
    return str(jour)[:10] in jours_couverts(aujourd_hui)


# --------------------------------------------------------------------------
# Cache disque et appel
# --------------------------------------------------------------------------

def _chemin_cache(jour: str) -> Path:
    return config.DOSSIER_CACHE_ABSENCES / f"{jour}.json"


def _cache_frais(chemin: Path) -> bool:
    return chemin.exists() and (time.time() - chemin.stat().st_mtime) < TTL_SECONDES


def _appeler(chemin_api: str, parametres: dict) -> tuple[list, int | None]:
    import requests

    reponse = requests.get(f"{URL_BASE}/{chemin_api}",
                           headers={"x-apisports-key": config.cle_api("api_football")},
                           params=parametres, timeout=DELAI_RESEAU)
    if reponse.status_code == 429:
        raise QuotaEpuise(
            "Quota API-Football epuise (429). Le plan gratuit est de "
            f"{config.QUOTA_QUOTIDIEN_API_FOOTBALL} requetes par jour, remis a "
            "zero chaque jour. Les absences deja en cache restent lisibles.")
    reponse.raise_for_status()
    charge = reponse.json()

    # L'API repond 200 meme quand elle refuse : l'erreur est dans le corps.
    erreurs = charge.get("errors")
    if isinstance(erreurs, dict) and erreurs:
        if "plan" in erreurs:
            raise HorsFenetre(
                f"API-Football refuse cette date : {erreurs['plan']}\n"
                "  Le plan gratuit ne couvre qu'aujourd'hui et demain. Les matchs "
                "plus lointains n'ont pas d'information d'absence disponible — ce "
                "qui ne veut pas dire que personne ne manque.")
        raise RuntimeError(f"API-Football : {erreurs}")
    if isinstance(erreurs, list) and erreurs:
        raise RuntimeError(f"API-Football : {erreurs}")

    entete = reponse.headers.get("x-ratelimit-requests-remaining")
    restant = int(entete) if entete and entete.strip().isdigit() else None
    return charge.get("response", []), restant


def recuperer_brut(jour: str, forcer: bool = False) -> tuple[list, bool]:
    """JSON brut des absences d'une date. Renvoie (donnees, provient_du_cache)."""
    jour = str(jour)[:10]
    if not dans_la_fenetre(jour):
        raise HorsFenetre(
            f"Le {jour} sort de la fenetre du plan gratuit "
            f"({' et '.join(jours_couverts())}). Aucune information d'absence "
            "n'est disponible pour ce match — ce n'est pas la meme chose que "
            "l'absence de blessure.")

    config.creer_dossiers()
    chemin = _chemin_cache(jour)
    if not forcer and _cache_frais(chemin):
        return json.loads(chemin.read_text(encoding="utf-8")), True

    etat = lire_compteur()
    if etat["appels"] >= config.QUOTA_QUOTIDIEN_API_FOOTBALL:
        if chemin.exists():
            _journal.warning("Quota quotidien atteint : lecture du cache des absences.")
            return json.loads(chemin.read_text(encoding="utf-8")), True
        raise QuotaEpuise(
            f"Quota quotidien atteint ({etat['appels']} appels) et aucun cache "
            "pour cette date. Le compteur repart demain.")

    donnees, restant = _appeler("injuries", {"date": jour})
    chemin.write_text(json.dumps(donnees), encoding="utf-8")
    etat = _incrementer_compteur(restant)
    _journal.info("%s — %d absence(s) recuperee(s) pour le %s.",
                  message_quota(etat), len(donnees), jour)
    return donnees, False


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def est_suspension(motif: str) -> bool:
    return any(mot in str(motif).lower() for mot in MOTIFS_SUSPENSION)


def normaliser(donnees: list[dict]) -> list[dict]:
    """JSON brut -> liste plate, sans doublons.

    L'API renvoie chaque joueur plusieurs fois quand plusieurs sources le
    signalent ; on deduplique sur (equipe, joueur, motif).
    """
    vues: set[tuple] = set()
    lignes: list[dict] = []
    for entree in donnees:
        equipe = (entree.get("team") or {}).get("name")
        joueur = (entree.get("player") or {}).get("name")
        if not equipe or not joueur:
            continue
        motif = (entree.get("player") or {}).get("reason") or "raison non precisee"
        cle = (equipe, joueur, motif)
        if cle in vues:
            continue
        vues.add(cle)
        lignes.append({
            "equipe_source": equipe,
            "joueur": joueur,
            "motif": motif,
            "suspension": est_suspension(motif),
            "type": (entree.get("player") or {}).get("type"),
            "ligue": (entree.get("league") or {}).get("name"),
            "fixture": (entree.get("fixture") or {}).get("id"),
        })
    return lignes


def du_match(lignes: list[dict], dom: str, ext: str,
             equipes_modele: list[str] | None = None) -> dict[str, list[dict]]:
    """Absences des deux equipes d'un match, canonisees vers nos noms.

    Les noms d'API-Football ne sont pas les notres. On les resout avec le meme
    mecanisme que le flux de cotes (`noms.resoudre_vers`), qui gere deja les
    variantes du type « Rayo Vallecano » / « Vallecano ».
    """
    candidats = list(equipes_modele) if equipes_modele else [dom, ext]
    resultat: dict[str, list[dict]] = {dom: [], ext: []}
    for ligne in lignes:
        resolu = noms_module.resoudre_vers(ligne["equipe_source"], candidats)
        if resolu in resultat:
            resultat[resolu].append(
                {c: ligne[c] for c in ("joueur", "motif", "suspension", "fixture")})
    return resultat


# --------------------------------------------------------------------------
# Compositions
# --------------------------------------------------------------------------

def compositions(fixture: int, forcer: bool = False) -> list[dict]:
    """Compositions probables d'un match, vides tant qu'elles ne sont pas publiees.

    Elles paraissent environ une heure avant le coup d'envoi. Une liste vide
    n'est donc pas une anomalie : c'est l'etat normal la veille.
    """
    config.creer_dossiers()
    chemin = config.DOSSIER_CACHE_ABSENCES / f"compo_{fixture}.json"
    if not forcer and _cache_frais(chemin):
        return json.loads(chemin.read_text(encoding="utf-8"))

    etat = lire_compteur()
    if etat["appels"] >= config.QUOTA_QUOTIDIEN_API_FOOTBALL:
        return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else []

    donnees, restant = _appeler("fixtures/lineups", {"fixture": int(fixture)})
    lignes = [{
        "equipe_source": (e.get("team") or {}).get("name"),
        "schema": e.get("formation"),
        "titulaires": [(j.get("player") or {}).get("name")
                       for j in (e.get("startXI") or [])],
    } for e in donnees]
    chemin.write_text(json.dumps(lignes), encoding="utf-8")
    _incrementer_compteur(restant)
    return lignes


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------

def recuperer(jour: str, forcer: bool = False) -> list[dict]:
    """Absences normalisees d'une date."""
    donnees, _ = recuperer_brut(jour, forcer)
    return normaliser(donnees)


def etat_du_jour(jour: str) -> tuple[list[dict], str, str]:
    """Absences d'une date, avec un statut de disponibilite et son explication.

    Ne leve jamais : une page qui affiche des matchs ne doit pas tomber parce
    qu'une API annexe est muette. Le statut dit ce qui s'est passe.
    """
    jour = str(jour)[:10]
    if not dans_la_fenetre(jour):
        return [], DISPONIBILITE_HORS_FENETRE, (
            f"Le plan gratuit API-Football ne couvre que "
            f"{' et '.join(jours_couverts())} : aucune information d'absence "
            "pour ce match, ce qui ne veut pas dire que personne ne manque.")
    try:
        return recuperer(jour), DISPONIBILITE_OK, ""
    except (QuotaEpuise, HorsFenetre) as erreur:
        return [], DISPONIBILITE_INDISPONIBLE, str(erreur).splitlines()[0]
    except Exception as erreur:                     # reseau, JSON, API muette
        _journal.info("Absences indisponibles pour %s : %s", jour, erreur)
        return [], DISPONIBILITE_INDISPONIBLE, (
            f"API-Football injoignable ({type(erreur).__name__}) : le reste de "
            "la page reste valable.")
