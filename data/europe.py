"""Resultats de Ligue des champions, tires de football-data.org.

Pourquoi ce module existe : un modele europeen doit comparer un club anglais a
un club portugais, alors que leurs championnats ne se croisent jamais. Les
seuls matchs qui relient les championnats entre eux sont les matchs de coupe
d'Europe. football-data.co.uk n'en publie aucun ; football-data.org publie la
Ligue des champions sur son plan gratuit, saison en cours comprise. La Ligue
Europa et la Ligue Conference, elles, sont reservees aux plans payants : on
s'en passe, et les clubs qui n'ont joue que dans ces deux coupes ne sont
relies a l'echelle commune que par leur championnat.

Les noms sont ramenes a ceux de football-data.co.uk (ceux des modeles) quand
la correspondance est sure, et laisses tels quels sinon : un club tcheque ou
serbe, dont le championnat n'est pas charge, reste une equipe a part entiere,
identifiee par son nom football-data.org.

Le score retenu est celui du temps reglementaire, comme partout ailleurs dans
le projet : une prolongation n'est pas un match de 120 minutes a modeliser.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

import config
from data import noms as noms_module
from data import resultats as resultats_module

_journal = logging.getLogger("foot_edge.europe")

URL_BASE = "https://api.football-data.org/v4"
DELAI_RESEAU = 20
COMPETITION = "CL"
CODE_CHAMPIONNAT = "UCL"
DOSSIER_CACHE = config.DOSSIER_CACHE_RESULTATS / "europe"

# Une saison terminee ne change plus ; la saison en cours est relue au plus
# toutes les six heures, comme les CSV football-data.co.uk.
TTL_SAISON_COURANTE = config.TTL_SAISON_COURANTE_SECONDES


def annee_de_debut(aujourdhui: date | None = None) -> int:
    """L'annee de debut de la saison europeenne en cours (2026 pour 2026/27)."""
    jour = aujourdhui or date.today()
    return jour.year if jour.month >= 7 else jour.year - 1


def saisons(nombre: int, aujourdhui: date | None = None) -> list[int]:
    """Les `nombre` dernieres saisons, de la plus ancienne a la plus recente."""
    derniere = annee_de_debut(aujourdhui)
    return list(range(derniere - nombre + 1, derniere + 1))


def _chemin(saison: int) -> Path:
    return DOSSIER_CACHE / f"{COMPETITION}_{saison}.json"


def _cache_valide(chemin: Path, saison: int, aujourdhui: date | None) -> bool:
    if not chemin.exists():
        return False
    if saison < annee_de_debut(aujourdhui):
        return True
    return time.time() - chemin.stat().st_mtime < TTL_SAISON_COURANTE


def _telecharger(saison: int) -> dict:
    import requests

    cle = config.cle_api("football_data_org")
    reponse = requests.get(
        f"{URL_BASE}/competitions/{COMPETITION}/matches",
        params={"season": saison}, headers={"X-Auth-Token": cle},
        timeout=DELAI_RESEAU)
    reponse.raise_for_status()
    return reponse.json()


def brut(saison: int, forcer: bool = False,
         aujourdhui: date | None = None) -> dict | None:
    """Le JSON football-data.org d'une saison, depuis le cache si possible.

    Ne leve jamais d'exception reseau : sans connexion ni cle, renvoie le
    cache, meme perime, ou None.
    """
    chemin = _chemin(saison)
    if not forcer and _cache_valide(chemin, saison, aujourdhui):
        return json.loads(chemin.read_text(encoding="utf-8"))
    try:
        donnees = _telecharger(saison)
    except Exception as erreur:                       # reseau, cle, quota
        _journal.warning("Ligue des champions %s non telechargee (%s)%s.", saison, erreur,
                         " : cache perime utilise" if chemin.exists() else "")
        return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else None
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    return donnees


def _score_90_minutes(score: dict) -> tuple[int | None, int | None]:
    """Le score a la fin du temps reglementaire.

    football-data.org range dans `fullTime` les buts de la prolongation (et
    meme la seance de tirs au but) ; `regularTime` n'existe que pour ces
    matchs-la.
    """
    if score.get("duration", "REGULAR") != "REGULAR" and score.get("regularTime"):
        temps = score["regularTime"]
    else:
        temps = score.get("fullTime") or {}
    return temps.get("home"), temps.get("away")


def _nom(equipe: dict, connues: list[str]) -> str | None:
    """Le nom du modele quand la correspondance est sure, sinon celui de la source."""
    nom = equipe.get("name")
    if not nom:
        return None
    for candidat in (nom, equipe.get("shortName")):
        if candidat:
            trouve = noms_module.resoudre_vers(candidat, connues)
            if trouve:
                return trouve
    return str(nom).strip()


def normaliser(donnees: dict, saison: int, equipes_connues: Iterable[str]) -> pd.DataFrame:
    """Matchs termines d'une saison, aux colonnes de data/resultats.py."""
    connues = sorted(set(equipes_connues))
    lignes = []
    for match in donnees.get("matches", []):
        if match.get("status") != "FINISHED":
            continue
        buts_dom, buts_ext = _score_90_minutes(match.get("score") or {})
        dom = _nom(match.get("homeTeam") or {}, connues)
        ext = _nom(match.get("awayTeam") or {}, connues)
        if None in (buts_dom, buts_ext, dom, ext):
            continue
        mi_temps = (match.get("score") or {}).get("halfTime") or {}
        lignes.append({
            "date": pd.Timestamp(match["utcDate"]).tz_convert(None).normalize(),
            "saison": f"{saison % 100:02d}{(saison + 1) % 100:02d}",
            "championnat": CODE_CHAMPIONNAT,
            "dom": dom, "ext": ext,
            "buts_dom": float(buts_dom), "buts_ext": float(buts_ext),
            "resultat": "H" if buts_dom > buts_ext else "A" if buts_dom < buts_ext else "D",
            "buts_dom_mt": mi_temps.get("home"), "buts_ext_mt": mi_temps.get("away"),
        })
    sortie = pd.DataFrame(lignes).reindex(columns=list(resultats_module.COLONNES_NORMALISEES))
    if sortie.empty:
        return sortie
    sortie["total_buts"] = sortie["buts_dom"] + sortie["buts_ext"]
    return sortie.sort_values("date").reset_index(drop=True)


def charger(nb_saisons: int, equipes_connues: Iterable[str],
            forcer: bool = False, aujourdhui: date | None = None) -> pd.DataFrame:
    """Matchs de Ligue des champions des `nb_saisons` dernieres saisons."""
    connues = list(equipes_connues)
    morceaux = []
    for saison in saisons(nb_saisons, aujourdhui):
        donnees = brut(saison, forcer, aujourdhui)
        if donnees:
            propre = normaliser(donnees, saison, connues)
            if not propre.empty:
                morceaux.append(propre)
    if not morceaux:
        return pd.DataFrame(columns=list(resultats_module.COLONNES_NORMALISEES))
    return pd.concat(morceaux, ignore_index=True).sort_values("date").reset_index(drop=True)
