"""Cotes multi-bookmakers via The Odds API (the-odds-api.com).

Le plan gratuit donne 500 requetes par mois. Une requete rapporte TOUS les
matchs a venir d'un championnat, pas un seul : on ne boucle donc jamais sur les
matchs. Le cache de 15 minutes, le compteur mensuel local et la lecture de
l'en-tete x-requests-remaining sont la pour qu'on ne se retrouve jamais a sec
un samedi apres-midi.

Toutes les cotes recuperees sont historisees en SQLite : ces donnees ne sont
pas recuperables retroactivement et sans elles le CLV (T13) est impossible.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import config
from data import noms as noms_module

_journal = logging.getLogger("foot_edge.cotes")

URL_BASE = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
REGIONS = "eu"
MARCHES_API = "h2h,totals"
DELAI_RESEAU = 20
# The Odds API facture 1 credit par marche et par region demandes.
COUT_CREDITS_PAR_APPEL = len(MARCHES_API.split(",")) * len(REGIONS.split(","))

MARCHE_1N2 = "1N2"
MARCHE_TOTAUX = "plus_moins_2.5"
MARCHE_DOUBLE_CHANCE = "double_chance"
MARCHE_BTTS = "btts"

# --------------------------------------------------------------------------
# Marches etendus : 1X / 12 / X2 et GG / NG
# --------------------------------------------------------------------------
# The Odds API ne les sert PAS sur l'endpoint groupe (422 « Markets not
# supported by this endpoint ») : il faut interroger chaque rencontre une par
# une, a un credit par marche. Deux marches sur une journee de douze matchs
# coutent donc vingt-quatre credits sur les cinq cents mensuels — c'est pourquoi
# rien n'est recupere automatiquement, seulement sur demande explicite.
#
# Ce qui N'EXISTE PAS chez ce fournisseur, verifie par sondage (les marches
# refuses ne coutent aucun credit) : `corners`, `totals_corners`,
# `team_totals_corners`, `cards`, `totals_cards`. Aucune cote de corner ni de
# carton n'est disponible, a aucun prix. Les pronos correspondants restent donc
# invalidables par le marche, et le projet le dit deja sur chaque ligne.
URL_EVENEMENT = ("https://api.the-odds-api.com/v4/sports/{sport}/events/"
                 "{evenement}/odds")
MARCHES_ETENDUS = ("double_chance", "btts")
COUT_CREDITS_MARCHES_ETENDUS = len(MARCHES_ETENDUS) * len(REGIONS.split(","))

# Nom rendu par l'API -> selection interne. « Metz or Draw » devient « 1N ».
_DOUBLE_CHANCE = {("dom", "draw"): "1N", ("draw", "ext"): "N2", ("dom", "ext"): "12"}

COLONNES_COTES = ("date", "dom", "ext", "championnat", "bookmaker",
                  "marche", "selection", "cote", "horodatage", "lien")

# Colonnes sans lesquelles un tableau de cotes n'est pas exploitable. `lien` et
# `horodatage` n'en font pas partie : un cache ecrit par une version anterieure
# ne les a pas, et doit rester lisible.
COLONNES_INDISPENSABLES = ("date", "dom", "ext", "bookmaker", "marche",
                           "selection", "cote")


class QuotaEpuise(RuntimeError):
    """Levee quand le quota mensuel local est atteint."""


# --------------------------------------------------------------------------
# Compteur d'appels mensuel
# --------------------------------------------------------------------------

def _chemin_compteur() -> Path:
    return config.DOSSIER_DONNEES / "quota_odds_api.json"


def _mois_courant() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def lire_compteur() -> dict[str, Any]:
    """Etat du quota : appels, credits consommes, et restant annonce par l'API.

    Attention : The Odds API facture un credit PAR MARCHE et PAR REGION, pas par
    appel HTTP. Un appel h2h+totals sur la region eu coute donc plusieurs credits.
    C'est le nombre de credits qui compte pour le quota de 500, pas le nombre
    d'appels : on suit les deux, et on avertit sur les credits.
    """
    chemin = _chemin_compteur()
    defaut = {"mois": _mois_courant(), "appels": 0, "credits": 0,
              "restant_api": None, "dernier_appel": None}
    if not chemin.exists():
        return defaut
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaut
    if donnees.get("mois") != _mois_courant():
        return defaut  # nouveau mois, le quota gratuit repart de zero
    return {**defaut, **donnees}


def _ecrire_compteur(etat: dict[str, Any]) -> None:
    config.creer_dossiers()
    _chemin_compteur().write_text(json.dumps(etat, indent=2), encoding="utf-8")


def _incrementer_compteur(restant_api: int | None,
                          utilises_api: int | None = None,
                          cout_estime: int = COUT_CREDITS_PAR_APPEL) -> dict[str, Any]:
    """Met a jour le compteur apres un appel.

    Les en-tetes de l'API font foi quand ils sont presents ; sinon on retombe
    sur une estimation locale (un credit par marche demande).
    """
    etat = lire_compteur()
    etat["appels"] += 1
    if utilises_api is not None:
        etat["credits"] = utilises_api
    elif restant_api is not None and etat.get("restant_api") is not None:
        etat["credits"] += max(1, etat["restant_api"] - restant_api)
    else:
        etat["credits"] += cout_estime
    etat["restant_api"] = restant_api
    etat["dernier_appel"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _ecrire_compteur(etat)
    return etat


def credits_consommes(etat: dict[str, Any] | None = None) -> int:
    """Credits factures ce mois-ci, au sens du quota de 500."""
    etat = etat or lire_compteur()
    return int(etat.get("credits", etat.get("appels", 0)))


def message_quota(etat: dict[str, Any] | None = None) -> str:
    """Phrase affichable sur l'etat du quota."""
    etat = etat or lire_compteur()
    restant = etat.get("restant_api")
    credits = credits_consommes(etat)
    texte = (f"Quota The Odds API : {credits} credit(s) consomme(s) ce mois-ci "
             f"sur {config.QUOTA_MENSUEL_ODDS_API} "
             f"({etat['appels']} appel(s), {COUT_CREDITS_PAR_APPEL} credits chacun)")
    if restant is not None:
        texte += f" — l'API annonce {restant} credit(s) restant(s)"
    if credits >= config.SEUIL_AVERTISSEMENT_QUOTA or (restant is not None and restant < 100):
        texte += ("\n  ATTENTION : quota bientot epuise. Espace tes rafraichissements ; "
                  "le cache de 15 minutes evite deja les appels inutiles. "
                  "Le quota gratuit repart a zero le 1er du mois.")
    return texte


# --------------------------------------------------------------------------
# Cache disque 15 minutes
# --------------------------------------------------------------------------

def _chemin_cache(championnat: str) -> Path:
    return config.DOSSIER_CACHE_COTES / f"{championnat.upper()}.json"


def _cache_frais(chemin: Path) -> bool:
    return chemin.exists() and (time.time() - chemin.stat().st_mtime) < config.TTL_COTES_SECONDES


# --------------------------------------------------------------------------
# Appel API
# --------------------------------------------------------------------------

def _entier_entete(reponse, nom: str) -> int | None:
    valeur = reponse.headers.get(nom)
    return int(valeur) if valeur and valeur.strip().lstrip("-").isdigit() else None


def _appeler_api(sport: str, cle: str) -> tuple[list[dict], int | None, int | None]:
    reponse = requests.get(
        URL_BASE.format(sport=sport),
        # `includeLinks` ne change pas le cout (mesure : 2 credits avec ou
        # sans). Il ajoute l'adresse de la page du match chez chaque bookmaker.
        # Aucun lien de COUPON pre-rempli n'existe : aucun des 23 bookmakers
        # n'en renvoie, a aucun niveau de marche ou de selection.
        params={"apiKey": cle, "regions": REGIONS, "markets": MARCHES_API,
                "oddsFormat": "decimal", "dateFormat": "iso",
                "includeLinks": "true"},
        timeout=DELAI_RESEAU,
    )
    restant_int = _entier_entete(reponse, "x-requests-remaining")
    utilises_int = _entier_entete(reponse, "x-requests-used")
    # x-requests-last donne le cout exact du dernier appel ; on le journalise
    # pour que le cout reel soit visible et pas seulement estime.
    cout = _entier_entete(reponse, "x-requests-last")
    if cout is not None and cout != COUT_CREDITS_PAR_APPEL:
        _journal.info("Cet appel a coute %d credit(s) (estimation locale : %d).",
                      cout, COUT_CREDITS_PAR_APPEL)
    if reponse.status_code == 401:
        raise PermissionError(
            "The Odds API refuse la cle (401).\n"
            f"  Verifie ODDS_API_KEY dans {config.CHEMIN_ENV}\n"
            "  Une cle gratuite s'obtient sur https://the-odds-api.com/#get-access"
        )
    if reponse.status_code == 429:
        raise QuotaEpuise(
            "Quota The Odds API epuise (429). Le plan gratuit est de "
            f"{config.QUOTA_MENSUEL_ODDS_API} requetes par mois, remis a zero "
            "le 1er du mois. En attendant, l'application continue de fonctionner "
            "sur les dernieres cotes en cache."
        )
    reponse.raise_for_status()
    return reponse.json(), restant_int, utilises_int


def recuperer_brut(championnat: str, forcer: bool = False) -> tuple[list[dict], bool]:
    """JSON brut de l'API, depuis le cache si celui-ci a moins de 15 minutes.

    Renvoie (donnees, provient_du_cache).
    """
    championnat = championnat.upper()
    info = config.COMPETITIONS.get(championnat)
    if not info:
        raise ValueError(f"Championnat inconnu : {championnat!r}")
    sport = info.get("odds")
    if not sport:
        raise ValueError(f"Pas de cle The Odds API connue pour {championnat}.")

    config.creer_dossiers()
    chemin = _chemin_cache(championnat)
    if not forcer and _cache_frais(chemin):
        _journal.info("Cotes %s reprises du cache (moins de 15 min, 0 requete consommee).",
                      championnat)
        return json.loads(chemin.read_text(encoding="utf-8")), True

    etat = lire_compteur()
    if credits_consommes(etat) + COUT_CREDITS_PAR_APPEL > config.QUOTA_MENSUEL_ODDS_API:
        if chemin.exists():
            _journal.warning("Quota mensuel atteint : lecture des dernieres cotes en cache.")
            return json.loads(chemin.read_text(encoding="utf-8")), True
        raise QuotaEpuise(
            f"Quota mensuel local atteint ({credits_consommes(etat)} credits sur "
            f"{config.QUOTA_MENSUEL_ODDS_API}) et aucun cache disponible pour ce "
            "championnat. Attends le 1er du mois ou passe a un plan payant."
        )

    cle = config.cle_api("odds")
    try:
        donnees, restant, utilises = _appeler_api(str(sport), cle)
    except (requests.RequestException, QuotaEpuise) as erreur:
        if chemin.exists():
            _journal.warning("Appel impossible (%s) : lecture du cache des cotes.", erreur)
            return json.loads(chemin.read_text(encoding="utf-8")), True
        raise

    chemin.write_text(json.dumps(donnees), encoding="utf-8")
    etat = _incrementer_compteur(restant, utilises)
    _journal.info(message_quota(etat))
    return donnees, False


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def _selection_h2h(nom_equipe: str, dom: str, ext: str) -> str | None:
    if nom_equipe == dom:
        return "1"
    if nom_equipe == ext:
        return "2"
    if str(nom_equipe).lower() == "draw":
        return "N"
    return None


def _lignes_evenement(evenement: dict, championnat: str, horodatage: str) -> list[dict]:
    dom_brut = evenement.get("home_team", "")
    ext_brut = evenement.get("away_team", "")
    dom = noms_module.canoniser(dom_brut, "odds_api", identite_si_inconnu=True)
    ext = noms_module.canoniser(ext_brut, "odds_api", identite_si_inconnu=True)
    date_match = pd.to_datetime(evenement.get("commence_time"), utc=True, errors="coerce")

    lignes: list[dict] = []
    for bookmaker in evenement.get("bookmakers", []):
        titre = bookmaker.get("title") or bookmaker.get("key")
        lien = bookmaker.get("link") or ""
        for marche in bookmaker.get("markets", []):
            for issue in marche.get("outcomes", []):
                cle = marche.get("key")
                if cle == "h2h":
                    selection = _selection_h2h(issue.get("name", ""), dom_brut, ext_brut)
                    nom_marche = MARCHE_1N2
                elif cle == "totals" and float(issue.get("point", 0)) == 2.5:
                    selection = "plus" if issue.get("name") == "Over" else "moins"
                    nom_marche = MARCHE_TOTAUX
                else:
                    continue
                if selection is None:
                    continue
                lignes.append({
                    "date": date_match, "dom": dom, "ext": ext,
                    "championnat": championnat, "bookmaker": titre,
                    "marche": nom_marche, "selection": selection,
                    "cote": float(issue.get("price", 0) or 0),
                    "horodatage": horodatage, "lien": lien,
                })
    return lignes


def _selection_double_chance(nom: str, dom: str, ext: str) -> str | None:
    """« Metz or Draw » -> « 1N ». Sans deviner : les deux camps doivent etre
    reconnus, sinon on ecarte la ligne plutot que d'inventer une selection."""
    parts = [p.strip() for p in nom.split(" or ")]
    if len(parts) != 2:
        return None
    roles = []
    for part in parts:
        if part.lower() == "draw":
            roles.append("draw")
        elif part == dom:
            roles.append("dom")
        elif part == ext:
            roles.append("ext")
        else:
            return None
    return _DOUBLE_CHANCE.get(tuple(sorted(roles, key=("dom", "draw", "ext").index)))


def _lignes_etendues(evenement: dict, championnat: str, horodatage: str) -> list[dict]:
    """Lignes 1X/12/X2 et GG/NG d'une rencontre, au meme format que les autres."""
    dom_brut = evenement.get("home_team", "")
    ext_brut = evenement.get("away_team", "")
    dom = noms_module.canoniser(dom_brut, "odds_api", identite_si_inconnu=True)
    ext = noms_module.canoniser(ext_brut, "odds_api", identite_si_inconnu=True)
    date_match = pd.to_datetime(evenement.get("commence_time"), utc=True, errors="coerce")

    lignes: list[dict] = []
    for bookmaker in evenement.get("bookmakers", []):
        titre = bookmaker.get("title") or bookmaker.get("key")
        lien = bookmaker.get("link") or ""
        for marche in bookmaker.get("markets", []):
            cle = marche.get("key")
            for issue in marche.get("outcomes", []):
                nom = issue.get("name", "")
                if cle == "double_chance":
                    selection = _selection_double_chance(nom, dom_brut, ext_brut)
                    nom_marche = MARCHE_DOUBLE_CHANCE
                elif cle == "btts":
                    selection = {"Yes": "oui", "No": "non"}.get(nom)
                    nom_marche = MARCHE_BTTS
                else:
                    continue
                if selection is None:
                    continue
                lignes.append({
                    "date": date_match, "dom": dom, "ext": ext,
                    "championnat": championnat, "bookmaker": titre,
                    "marche": nom_marche, "selection": selection,
                    "cote": float(issue.get("price", 0) or 0),
                    "horodatage": horodatage, "lien": lien,
                })
    return lignes


def _chemin_cache_etendu(championnat: str, evenement: str) -> Path:
    return config.DOSSIER_CACHE_COTES / f"{championnat.upper()}_ev_{evenement}.json"


def recuperer_etendus(championnat: str, evenements: list[str],
                      forcer: bool = False) -> pd.DataFrame:
    """1X/12/X2 et GG/NG pour une liste de rencontres, une requete chacune.

    Chaque rencontre coute deux credits. Rien n'est demande automatiquement :
    l'appelant choisit les rencontres, et le compteur mensuel est le meme que
    pour le reste — un seul budget, une seule verite sur ce qui reste.
    """
    championnat = championnat.upper()
    info = config.COMPETITIONS.get(championnat) or {}
    sport = info.get("odds")
    if not sport:
        return normaliser([], championnat)

    config.creer_dossiers()
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lignes: list[dict] = []
    for evenement in evenements:
        chemin = _chemin_cache_etendu(championnat, evenement)
        if not forcer and _cache_frais(chemin):
            charge = json.loads(chemin.read_text(encoding="utf-8"))
        else:
            try:
                charge = _appeler_evenement(sport, evenement)
            except Exception as erreur:
                _journal.info("Marches etendus indisponibles pour %s : %s",
                              evenement, erreur)
                if not chemin.exists():
                    continue
                charge = json.loads(chemin.read_text(encoding="utf-8"))
            else:
                chemin.write_text(json.dumps(charge), encoding="utf-8")
        lignes.extend(_lignes_etendues(charge, championnat, horodatage))

    if not lignes:
        return normaliser([], championnat)
    tableau = pd.DataFrame(lignes)
    return tableau[tableau["cote"] > 1.0].reset_index(drop=True)


def _appeler_evenement(sport: str, evenement: str) -> dict:
    reponse = requests.get(
        URL_EVENEMENT.format(sport=sport, evenement=evenement),
        params={"apiKey": config.cle_api("odds"), "regions": REGIONS,
                "markets": ",".join(MARCHES_ETENDUS), "oddsFormat": "decimal",
                "dateFormat": "iso", "includeLinks": "true"},
        timeout=DELAI_RESEAU)
    if reponse.status_code == 429:
        raise QuotaEpuise("Quota The Odds API epuise (429).")
    reponse.raise_for_status()
    cout = _entier_entete(reponse, "x-requests-last")
    restant = _entier_entete(reponse, "x-requests-remaining")
    utilises = _entier_entete(reponse, "x-requests-used")
    if cout:
        _incrementer_compteur(restant, utilises, cout)
    return reponse.json()


def normaliser(donnees: list[dict], championnat: str) -> pd.DataFrame:
    """JSON de l'API -> DataFrame plat, noms d'equipes canonises."""
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lignes: list[dict] = []
    for evenement in donnees:
        lignes.extend(_lignes_evenement(evenement, championnat.upper(), horodatage))
    if not lignes:
        return pd.DataFrame(columns=list(COLONNES_COTES))
    tableau = pd.DataFrame(lignes)
    return tableau[tableau["cote"] > 1.0].reset_index(drop=True)


# --------------------------------------------------------------------------
# Historisation SQLite
# --------------------------------------------------------------------------

SCHEMA_HISTORIQUE = """
CREATE TABLE IF NOT EXISTS cotes_historique (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage    TEXT NOT NULL,
    championnat   TEXT NOT NULL,
    date_match    TEXT NOT NULL,
    dom           TEXT NOT NULL,
    ext           TEXT NOT NULL,
    bookmaker     TEXT NOT NULL,
    marche        TEXT NOT NULL,
    selection     TEXT NOT NULL,
    cote          REAL NOT NULL,
    UNIQUE (horodatage, championnat, date_match, dom, ext, bookmaker, marche, selection)
);
CREATE INDEX IF NOT EXISTS idx_cotes_match
    ON cotes_historique (dom, ext, date_match, marche);
"""


def connexion(chemin: Path | None = None) -> sqlite3.Connection:
    config.creer_dossiers()
    lien = sqlite3.connect(chemin or config.CHEMIN_DB)
    lien.executescript(SCHEMA_HISTORIQUE)
    return lien


def historiser(cotes: pd.DataFrame, chemin_db: Path | None = None) -> int:
    """Enregistre un instantane de cotes. Renvoie le nombre de lignes ajoutees."""
    if cotes.empty:
        return 0
    lien = connexion(chemin_db)
    try:
        lignes = [
            (ligne.horodatage, ligne.championnat, str(ligne.date), ligne.dom, ligne.ext,
             ligne.bookmaker, ligne.marche, ligne.selection, float(ligne.cote))
            for ligne in cotes.itertuples()
        ]
        curseur = lien.executemany(
            "INSERT OR IGNORE INTO cotes_historique "
            "(horodatage, championnat, date_match, dom, ext, bookmaker, marche, selection, cote) "
            "VALUES (?,?,?,?,?,?,?,?,?)", lignes)
        lien.commit()
        return curseur.rowcount
    finally:
        lien.close()


def historique_match(dom: str, ext: str, date_match: str,
                     marche: str = MARCHE_1N2, chemin_db: Path | None = None) -> pd.DataFrame:
    """Toutes les cotes historisees d'un match, du plus ancien au plus recent."""
    lien = connexion(chemin_db)
    try:
        return pd.read_sql_query(
            "SELECT * FROM cotes_historique WHERE dom=? AND ext=? AND marche=? "
            "AND date_match LIKE ? ORDER BY horodatage",
            lien, params=(dom, ext, marche, f"{str(date_match)[:10]}%"))
    finally:
        lien.close()


def derniers_instantanes(championnat: str, debut: str, fin: str,
                         chemin_db: Path | None = None) -> pd.DataFrame:
    """Dernier instantane historise de chaque match commencant dans [debut, fin).

    Le flux de l'API ne rend que les matchs a venir : des le coup d'envoi, un
    match disparait du cache au rafraichissement suivant. L'historique, lui,
    le garde, avec les dernieres cotes vues avant le coup d'envoi. `debut` et
    `fin` sont des instants UTC ISO.
    """
    debut, fin = (pd.Timestamp(b) if pd.Timestamp(b).tzinfo
                  else pd.Timestamp(b).tz_localize("UTC") for b in (debut, fin))
    # date_match est ecrit par str(Timestamp UTC) : la comparaison de chaines
    # suit donc l'ordre chronologique, a une journee de marge pres.
    bas = str(debut - pd.Timedelta(days=1))
    haut = str(fin + pd.Timedelta(days=1))
    lien = connexion(chemin_db)
    try:
        lignes = pd.read_sql_query(
            "SELECT h.* FROM cotes_historique h JOIN ("
            "  SELECT date_match, dom, ext, MAX(horodatage) AS dernier"
            "  FROM cotes_historique"
            "  WHERE championnat=? AND date_match>=? AND date_match<?"
            "  GROUP BY date_match, dom, ext) d"
            " ON h.date_match=d.date_match AND h.dom=d.dom AND h.ext=d.ext"
            "  AND h.horodatage=d.dernier WHERE h.championnat=?",
            lien, params=(championnat.upper(), bas, haut, championnat.upper()))
    finally:
        lien.close()
    if lignes.empty:
        return pd.DataFrame(columns=list(COLONNES_COTES))
    lignes["date"] = pd.to_datetime(lignes.pop("date_match"), utc=True, errors="coerce")
    dans_le_creneau = (lignes["date"] >= debut) & (lignes["date"] < fin)
    lignes = lignes.loc[dans_le_creneau].copy()
    lignes["lien"] = ""
    return lignes.reindex(columns=list(COLONNES_COTES)).reset_index(drop=True)


def dernieres_cotes_avant(dom: str, ext: str, date_match: str, instant: str,
                          marche: str = MARCHE_1N2,
                          chemin_db: Path | None = None) -> pd.DataFrame:
    """Instantane le plus recent anterieur a `instant` (sert au CLV)."""
    historique = historique_match(dom, ext, date_match, marche, chemin_db)
    if historique.empty:
        return historique
    avant = historique[historique["horodatage"] <= instant]
    source = avant if not avant.empty else historique
    dernier = source["horodatage"].max()
    return source[source["horodatage"] == dernier].reset_index(drop=True)


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------

def recuperer(championnat: str, forcer: bool = False,
              historiser_db: bool = True) -> pd.DataFrame:
    """Cotes 1N2 et over/under 2.5 des matchs a venir d'un championnat."""
    donnees, du_cache = recuperer_brut(championnat, forcer)
    cotes = normaliser(donnees, championnat)
    if historiser_db and not du_cache and not cotes.empty:
        ajoutees = historiser(cotes)
        _journal.info("%d cotes historisees (indispensable pour le CLV).", ajoutees)
    return cotes


def age_du_cache(championnat: str) -> float | None:
    """Age en secondes des cotes en cache, None si ce championnat n'en a pas.

    Permet de savoir ce qu'on peut afficher sans depenser un credit, avant
    meme d'entrainer le moindre modele.
    """
    chemin = _chemin_cache(championnat)
    return time.time() - chemin.stat().st_mtime if chemin.exists() else None


def lire_cache(championnat: str) -> tuple[pd.DataFrame, float | None]:
    """Cotes du cache disque, sans jamais appeler l'API.

    Renvoie (cotes, age du cache en secondes) ; l'age vaut None si rien n'est
    en cache. C'est la lecture qu'il faut pour une page qu'on rafraichit :
    passer par recuperer() consommerait deux credits des que le cache depasse
    quinze minutes, a l'insu de celui qui recharge.
    """
    chemin = _chemin_cache(championnat)
    if not chemin.exists():
        return normaliser([], championnat.upper()), None
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    age = time.time() - chemin.stat().st_mtime
    return normaliser(donnees, championnat.upper()), age


def matchs_a_venir(cotes: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par match, avec le nombre de bookmakers qui le cotent."""
    if cotes.empty:
        return pd.DataFrame(columns=["date", "dom", "ext", "nb_bookmakers"])
    return (cotes.groupby(["date", "dom", "ext"], as_index=False)
            .agg(nb_bookmakers=("bookmaker", "nunique"))
            .sort_values("date").reset_index(drop=True))


def marge_bookmaker(cotes_1n2: dict[str, float]) -> float | None:
    """Marge du bookmaker sur le 1N2 : somme des probabilites implicites moins 1.

    Un livre a 1.05 garde 5 % en moyenne. C'est le seul chiffre qui permette de
    comparer honnetement deux bookmakers : la meilleure cote sur une selection
    peut venir d'un livre cher ailleurs. Renvoie None si les trois cotes ne sont
    pas toutes presentes.
    """
    valeurs = [cotes_1n2.get(s) for s in ("1", "N", "2")]
    if any(not v or v <= 1.0 for v in valeurs):
        return None
    return sum(1.0 / float(v) for v in valeurs) - 1.0


def par_bookmaker(cotes: pd.DataFrame, date: Any, dom: str, ext: str) -> list[dict]:
    """Le detail d'un match : une ligne par bookmaker, toutes ses cotes.

    L'agregat `matchs_a_venir` ne donne qu'un compte ("14 bookmakers") ; ici on
    rend les cotes elles-memes, parce que c'est chez un bookmaker precis qu'on
    parie, pas chez la moyenne. Les livres sont tries par marge croissante :
    les moins chers d'abord, ceux dont les cotes sont les plus informatives.
    """
    # Detail d'affichage : sur un tableau incomplet on ne montre rien plutot que
    # de faire echouer toute la page pour une colonne absente.
    if cotes.empty or not set(COLONNES_INDISPENSABLES).issubset(cotes.columns):
        return []
    selection = cotes[(cotes["date"] == date) & (cotes["dom"] == dom) & (cotes["ext"] == ext)]
    if selection.empty:
        return []

    lignes: list[dict] = []
    for nom, groupe in selection.groupby("bookmaker"):
        cotes_1n2: dict[str, float] = {}
        totaux: dict[str, float] = {}
        for ligne in groupe.itertuples():
            cible = cotes_1n2 if ligne.marche == MARCHE_1N2 else totaux
            cible[ligne.selection] = float(ligne.cote)
        # Le lien mene a la PAGE DU MATCH chez ce bookmaker, pas a un coupon
        # pre-rempli : aucun des vingt-trois bookmakers du flux n'en fournit.
        liens = [str(l) for l in groupe.get("lien", pd.Series(dtype=str)) if l]
        lignes.append({
            "bookmaker": str(nom),
            "1": cotes_1n2.get("1"), "N": cotes_1n2.get("N"), "2": cotes_1n2.get("2"),
            "plus": totaux.get("plus"), "moins": totaux.get("moins"),
            "marge": marge_bookmaker(cotes_1n2),
            "lien": liens[0] if liens else None,
        })
    # marge inconnue en dernier : on ne fait pas passer une absence pour un zero
    lignes.sort(key=lambda l: (l["marge"] is None, l["marge"], l["bookmaker"]))
    return lignes


def meilleures_cotes(detail: list[dict]) -> dict[str, float]:
    """La meilleure cote offerte pour chaque selection, tous bookmakers confondus."""
    meilleures: dict[str, float] = {}
    for ligne in detail:
        for cle in ("1", "N", "2", "plus", "moins"):
            valeur = ligne.get(cle)
            if valeur and (cle not in meilleures or valeur > meilleures[cle]):
                meilleures[cle] = float(valeur)
    return meilleures
