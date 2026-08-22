"""Scores finaux du jour meme, via API-Football.

Pourquoi ce module existe alors que `data/resultats.py` ramene deja des scores :
football-data.co.uk publie **par lots**, pas en continu. Mesure faite le
2026-08-22 a 00h44, quatre heures apres les coups de sifflet final :

    E2, SP1   dernier resultat publie 20/08   (2 jours de retard)
    E1, SP2   17/08                           (5 jours)
    F2        14/08                           (8 jours)
    SC0       09/08                           (13 jours)
    E0, F1, I1, I2, D1, G1  saison 2026-27 pas encore publiee

Or le flux de cotes ne retient que les matchs a venir : au rafraichissement
suivant, un match joue disparait de l'affichage. Les deux fenetres ne se
recouvrent presque jamais, et la confrontation prono/resultat n'aurait donc
jamais lieu. API-Football, elle, donne le score des la fin du match.

Cout, mesure plutot que suppose :

  - les scores d'une date entiere tiennent en **un seul appel** (385 rencontres
    le 21/08), avec un cache disque d'une heure ;
  - les corners et les cartons demandent un appel **par rencontre**, mais nos
    championnats n'en comptent qu'une douzaine par jour — soit douze appels sur
    les cent quotidiens. Le cache est definitif : les statistiques d'un match
    termine ne changent plus.

Le compteur de quota est celui d'`absences` : meme cle, meme budget, donc un
seul compteur. Quand il est epuise, les comptes restent simplement inconnus.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from data import absences as absences_module
from data import noms as noms_module

_journal = logging.getLogger("foot_edge.scores")

URL_BASE = "https://v3.football.api-sports.io"
DELAI_RESEAU = 20

# Une heure : un score final ne bouge plus, mais les matchs de la soiree
# tombent au fil de l'eau et une date consultee tot doit pouvoir se completer.
TTL_SECONDES = 3600

# Rencontres terminees. Les matchs en cours (1H, 2H, HT) sont volontairement
# exclus : un prono confronte a un score de mi-temps serait juge sur rien.
STATUTS_TERMINES = frozenset({"FT", "AET", "PEN"})


def _chemin_cache(jour: str) -> Path:
    return config.DOSSIER_CACHE_ABSENCES / f"scores_{jour}.json"


def _cache_frais(chemin: Path) -> bool:
    return chemin.exists() and (time.time() - chemin.stat().st_mtime) < TTL_SECONDES


def recuperer_brut(jour: str, forcer: bool = False) -> tuple[list, bool]:
    """JSON brut des rencontres d'une date. Renvoie (donnees, provient_du_cache).

    Ne leve jamais pour une raison reseau : sans score, la page affiche « pas
    encore publie », ce qui reste vrai.
    """
    import requests

    jour = str(jour)[:10]
    config.creer_dossiers()
    chemin = _chemin_cache(jour)
    if not forcer and _cache_frais(chemin):
        return json.loads(chemin.read_text(encoding="utf-8")), True

    etat = absences_module.lire_compteur()
    if etat["appels"] >= config.QUOTA_QUOTIDIEN_API_FOOTBALL:
        if chemin.exists():
            _journal.warning("Quota quotidien atteint : lecture du cache des scores.")
            return json.loads(chemin.read_text(encoding="utf-8")), True
        raise absences_module.QuotaEpuise(
            f"Quota quotidien atteint ({etat['appels']} appels) et aucun cache "
            "de scores pour cette date. Le compteur repart demain.")

    reponse = requests.get(
        f"{URL_BASE}/fixtures",
        headers={"x-apisports-key": config.cle_api("api_football")},
        params={"date": jour}, timeout=DELAI_RESEAU)
    if reponse.status_code == 429:
        raise absences_module.QuotaEpuise(
            "Quota API-Football epuise (429). Les scores deja en cache restent "
            "lisibles.")
    reponse.raise_for_status()
    charge = reponse.json()
    erreurs = charge.get("errors")
    if erreurs:
        raise RuntimeError(f"API-Football : {erreurs}")

    donnees = charge.get("response", [])
    chemin.write_text(json.dumps(donnees), encoding="utf-8")
    entete = reponse.headers.get("x-ratelimit-requests-remaining")
    restant = int(entete) if entete and entete.strip().isdigit() else None
    etat = absences_module.enregistrer_appel(restant)
    _journal.info("%s — %d rencontre(s) lues pour le %s.",
                  absences_module.message_quota(etat), len(donnees), jour)
    return donnees, False


def _score(rencontre: dict) -> dict[str, Any] | None:
    buts = rencontre.get("goals") or {}
    dom, ext = buts.get("home"), buts.get("away")
    if dom is None or ext is None:
        return None
    return {"buts_dom": float(dom), "buts_ext": float(ext)}


def du_jour(jour: str, equipes: list[str],
            forcer: bool = False) -> dict[tuple[str, str], dict]:
    """Scores finaux d'une date, indexes par (domicile, exterieur) resolus.

    `equipes` est la liste de reference — celle du modele qui a servi a estimer
    le match, pour que la cle soit exactement celle que la page manipule. Une
    rencontre dont un seul camp se resout est ignoree : apparier a moitie
    reviendrait a deviner.
    """
    try:
        rencontres, _ = recuperer_brut(jour, forcer=forcer)
    except Exception as erreur:                     # jamais au prix de la page
        _journal.info("Scores du %s indisponibles : %s", jour, erreur)
        return {}

    index: dict[tuple[str, str], dict] = {}
    for rencontre in rencontres:
        statut = (rencontre.get("fixture", {}).get("status", {}) or {}).get("short")
        if statut not in STATUTS_TERMINES:
            continue
        score = _score(rencontre)
        if score is None:
            continue
        camps = rencontre.get("teams") or {}
        brut_dom = (camps.get("home") or {}).get("name", "")
        brut_ext = (camps.get("away") or {}).get("name", "")
        dom = noms_module.resoudre_vers(
            noms_module.canoniser(brut_dom, "api_football", identite_si_inconnu=True),
            equipes)
        ext = noms_module.resoudre_vers(
            noms_module.canoniser(brut_ext, "api_football", identite_si_inconnu=True),
            equipes)
        if dom and ext and dom != ext:
            index[(dom, ext)] = {
                **score, "source": "api_football", "statut": statut, "jour": jour,
                "fixture": (rencontre.get("fixture") or {}).get("id")}
    return index


# Ce que l'API nomme les colonnes qui nous interessent.
_COMPTES = {"Corner Kicks": "corners", "Yellow Cards": "jaunes"}


def _chemin_statistiques(fixture: int) -> Path:
    return config.DOSSIER_CACHE_ABSENCES / f"stats_{fixture}.json"


def statistiques(fixture: int, forcer: bool = False) -> list:
    """Statistiques d'une rencontre terminee. Cache definitif.

    Un match joue ne change plus : une fois lues, ses statistiques n'ont aucune
    raison d'etre redemandees, et le cache n'a donc pas de duree de validite.
    """
    import requests

    config.creer_dossiers()
    chemin = _chemin_statistiques(fixture)
    if not forcer and chemin.exists():
        return json.loads(chemin.read_text(encoding="utf-8"))

    etat = absences_module.lire_compteur()
    if etat["appels"] >= config.QUOTA_QUOTIDIEN_API_FOOTBALL:
        raise absences_module.QuotaEpuise(
            f"Quota quotidien atteint ({etat['appels']} appels) : les corners et "
            "cartons de cette rencontre resteront inconnus jusqu'a demain.")

    reponse = requests.get(
        f"{URL_BASE}/fixtures/statistics",
        headers={"x-apisports-key": config.cle_api("api_football")},
        params={"fixture": fixture}, timeout=DELAI_RESEAU)
    if reponse.status_code == 429:
        raise absences_module.QuotaEpuise("Quota API-Football epuise (429).")
    reponse.raise_for_status()
    charge = reponse.json()
    if charge.get("errors"):
        raise RuntimeError(f"API-Football : {charge['errors']}")

    donnees = charge.get("response", [])
    chemin.write_text(json.dumps(donnees), encoding="utf-8")
    entete = reponse.headers.get("x-ratelimit-requests-remaining")
    absences_module.enregistrer_appel(
        int(entete) if entete and entete.strip().isdigit() else None)
    return donnees


def comptes(entree: dict, dom: str, ext: str) -> dict:
    """Corners et cartons jaunes d'une rencontre, ajoutes a son score.

    Renvoie l'entree inchangee si l'identifiant manque, si le quota est epuise
    ou si l'API ne publie pas ces lignes : un compte absent doit rester absent,
    jamais devenir un zero — un zero serait un chiffre, et il serait faux.
    """
    fixture = entree.get("fixture")
    if not fixture:
        return entree
    try:
        equipes = statistiques(int(fixture))
    except Exception as erreur:
        _journal.info("Statistiques de la rencontre %s indisponibles : %s",
                      fixture, erreur)
        return entree

    ajouts: dict[str, float] = {}
    for rang, camp in enumerate(("dom", "ext")):
        if rang >= len(equipes):
            break
        lignes = {s.get("type"): s.get("value")
                  for s in (equipes[rang].get("statistics") or [])}
        for etiquette, prefixe in _COMPTES.items():
            valeur = lignes.get(etiquette)
            if valeur is not None:
                ajouts[f"{prefixe}_{camp}"] = float(valeur)
    return {**entree, **ajouts}


def jours_en_cache() -> list[str]:
    """Les dates dont les scores sont deja sur le disque."""
    dossier = config.DOSSIER_CACHE_ABSENCES
    if not dossier.exists():
        return []
    return sorted(chemin.stem.removeprefix("scores_")
                  for chemin in dossier.glob("scores_*.json"))


def horodatage() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
