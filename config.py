"""Configuration centrale de foot_edge.

Expose les chemins de travail, les constantes de championnats et la lecture
des cles API. Aucune cle n'est ecrite en dur ici : tout passe par le fichier
.env a la racine du projet ou par les variables d'environnement.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

try:  # python-dotenv est optionnel au chargement, obligatoire a l'usage
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - depend de l'installation
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


# --------------------------------------------------------------------------
# Chemins
# --------------------------------------------------------------------------

RACINE_PROJET: Final[Path] = Path(__file__).resolve().parent
CHEMIN_ENV: Final[Path] = RACINE_PROJET / ".env"

# Permet de deplacer entierement les donnees (utile pour les tests).
_racine_donnees = os.environ.get("FOOT_EDGE_HOME")
DOSSIER_DONNEES: Final[Path] = (
    Path(_racine_donnees).expanduser() if _racine_donnees else Path.home() / ".foot_edge"
)
DOSSIER_CACHE: Final[Path] = DOSSIER_DONNEES / "cache"
DOSSIER_CACHE_RESULTATS: Final[Path] = DOSSIER_CACHE / "resultats"
DOSSIER_CACHE_XG: Final[Path] = DOSSIER_CACHE / "xg"
DOSSIER_CACHE_COTES: Final[Path] = DOSSIER_CACHE / "cotes"
DOSSIER_CACHE_ABSENCES: Final[Path] = DOSSIER_CACHE / "absences"
DOSSIER_CALIBRATION: Final[Path] = DOSSIER_DONNEES / "calibration"
DOSSIER_RAPPORTS: Final[Path] = DOSSIER_DONNEES / "rapports"
CHEMIN_DB: Final[Path] = DOSSIER_DONNEES / "foot_edge.db"

_DOSSIERS: Final[tuple[Path, ...]] = (
    DOSSIER_DONNEES,
    DOSSIER_CACHE,
    DOSSIER_CACHE_RESULTATS,
    DOSSIER_CACHE_XG,
    DOSSIER_CACHE_COTES,
    DOSSIER_CACHE_ABSENCES,
    DOSSIER_CALIBRATION,
    DOSSIER_RAPPORTS,
)


def creer_dossiers() -> None:
    """Cree l'arborescence de travail si elle n'existe pas encore."""
    for dossier in _DOSSIERS:
        dossier.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Championnats
# --------------------------------------------------------------------------

# code football-data.co.uk -> (nom lisible, pays, cle The Odds API, cle FBref/Understat)
CHAMPIONNATS: Final[dict[str, dict[str, str | bool | None]]] = {
    "E0": {"nom": "Premier League", "pays": "Angleterre",
           "odds": "soccer_epl", "fbref": "ENG-Premier League", "understat": "ENG-Premier League"},
    "E1": {"nom": "Championship", "pays": "Angleterre",
           "odds": "soccer_efl_champ", "fbref": None, "understat": None},
    "E2": {"nom": "League One", "pays": "Angleterre",
           "odds": "soccer_england_league1", "fbref": None, "understat": None},
    "E3": {"nom": "League Two", "pays": "Angleterre",
           "odds": "soccer_england_league2", "fbref": None, "understat": None},
    "EC": {"nom": "National League", "pays": "Angleterre",
           "odds": None, "fbref": None, "understat": None},
    "SC0": {"nom": "Scottish Premiership", "pays": "Ecosse",
            "odds": "soccer_spl", "fbref": None, "understat": None},
    "SC1": {"nom": "Scottish Championship", "pays": "Ecosse",
            "odds": None, "fbref": None, "understat": None},
    "SC2": {"nom": "Scottish League One", "pays": "Ecosse",
            "odds": None, "fbref": None, "understat": None},
    "SC3": {"nom": "Scottish League Two", "pays": "Ecosse",
            "odds": None, "fbref": None, "understat": None},
    "SP1": {"nom": "La Liga", "pays": "Espagne",
            "odds": "soccer_spain_la_liga", "fbref": "ESP-La Liga", "understat": "ESP-La Liga"},
    "SP2": {"nom": "La Liga 2", "pays": "Espagne",
            "odds": "soccer_spain_segunda_division", "fbref": None, "understat": None},
    "I1": {"nom": "Serie A", "pays": "Italie",
           "odds": "soccer_italy_serie_a", "fbref": "ITA-Serie A", "understat": "ITA-Serie A"},
    "I2": {"nom": "Serie B", "pays": "Italie",
           "odds": "soccer_italy_serie_b", "fbref": None, "understat": None},
    "D1": {"nom": "Bundesliga", "pays": "Allemagne",
           "odds": "soccer_germany_bundesliga", "fbref": "GER-Bundesliga", "understat": "GER-Bundesliga"},
    "D2": {"nom": "Bundesliga 2", "pays": "Allemagne",
           "odds": "soccer_germany_bundesliga2", "fbref": None, "understat": None},
    "F1": {"nom": "Ligue 1", "pays": "France",
           "odds": "soccer_france_ligue_one", "fbref": "FRA-Ligue 1", "understat": "FRA-Ligue 1"},
    "F2": {"nom": "Ligue 2", "pays": "France",
           "odds": "soccer_france_ligue_two", "fbref": None, "understat": None},
    "N1": {"nom": "Eredivisie", "pays": "Pays-Bas",
           "odds": "soccer_netherlands_eredivisie", "fbref": None, "understat": None},
    "B1": {"nom": "Jupiler Pro League", "pays": "Belgique",
           "odds": "soccer_belgium_first_div", "fbref": None, "understat": None},
    "P1": {"nom": "Primeira Liga", "pays": "Portugal",
           "odds": "soccer_portugal_primeira_liga", "fbref": None, "understat": None},
    "T1": {"nom": "Super Lig", "pays": "Turquie",
           "odds": "soccer_turkey_super_league", "fbref": None, "understat": None},
    "G1": {"nom": "Super League", "pays": "Grece",
           "odds": "soccer_greece_super_league", "fbref": None, "understat": None},
    # Saison sur l'annee civile, publiee par football-data.co.uk dans un fichier
    # unique (new/BRA.csv) qui couvre toutes les saisons : voir data/resultats.py.
    "BRA": {"nom": "Brasileirao Serie A", "pays": "Bresil",
            "odds": "soccer_brazil_campeonato", "fbref": None, "understat": None,
            "annee_civile": True},
    # Absente de football-data.co.uk : resultats tires d'API-Football (ligue 72),
    # dont le plan gratuit ne couvre que les saisons 2022 a 2024.
    "BRB": {"nom": "Brasileirao Serie B", "pays": "Bresil",
            "odds": "soccer_brazil_serie_b", "fbref": None, "understat": None,
            "annee_civile": True, "api_football": 72},
}

# Championnats dont la saison suit l'annee civile ("2026" et non "2526").
CHAMPIONNATS_ANNEE_CIVILE: Final[frozenset[str]] = frozenset(
    code for code, info in CHAMPIONNATS.items() if info.get("annee_civile")
)

CODES_CHAMPIONNATS: Final[tuple[str, ...]] = tuple(CHAMPIONNATS)

# Championnats que The Odds API cote : les seuls qu'on puisse proposer a
# l'affichage. Les autres n'existent ici que comme historique, pour donner au
# modele inter-divisions de quoi situer une equipe qui monte ou qui descend.
CHAMPIONNATS_AVEC_COTES: Final[tuple[str, ...]] = tuple(
    code for code, info in CHAMPIONNATS.items() if info["odds"]
)

# Pays -> ses divisions, du plus haut niveau au plus bas. C'est la promotion et
# la relegation qui relient ces divisions entre elles : une equipe qui a joue
# dans deux d'entre elles au cours des saisons chargees porte une seule force,
# ajustee contre les deux, et c'est elle qui met les divisions a la meme
# echelle. Sans ces equipes-pont, un modele commun n'aurait aucun sens.
DIVISIONS_PAR_PAYS: Final[dict[str, tuple[str, ...]]] = {
    pays: tuple(code for code, info in CHAMPIONNATS.items() if info["pays"] == pays)
    for pays in dict.fromkeys(info["pays"] for info in CHAMPIONNATS.values())
}


def divisions_du_pays(code: str) -> tuple[str, ...]:
    """Les divisions du meme pays que `code`, celle-ci comprise."""
    info = CHAMPIONNATS.get(code.upper())
    return DIVISIONS_PAR_PAYS.get(info["pays"], (code.upper(),)) if info else (code.upper(),)

# Championnats pour lesquels des xG publics existent (5 grands championnats).
CHAMPIONNATS_AVEC_XG: Final[tuple[str, ...]] = tuple(
    code for code, info in CHAMPIONNATS.items() if info["fbref"]
)


def nom_championnat(code: str) -> str:
    """Nom lisible d'un championnat, ou le code lui-meme s'il est inconnu."""
    info = CHAMPIONNATS.get(code.upper())
    return str(info["nom"]) if info else code


# --------------------------------------------------------------------------
# Constantes metier (voir section 2 de TACHES.txt)
# --------------------------------------------------------------------------

FRACTION_KELLY: Final[float] = 0.25          # Kelly/4, jamais complet
PLAFOND_MISE_BANKROLL: Final[float] = 0.02   # 2 % de bankroll, plafond dur
SEUIL_ESPERANCE_DEFAUT: Final[float] = 0.04  # 4 % d'esperance minimale

# Poids accorde au modele face au marche dans le melange geometrique, quand
# aucune mesure n'existe encore pour le championnat. Ce n'est qu'un point de
# depart : `cli.py calibrer` mesure le bon poids par backtest et l'enregistre,
# et c'est le poids mesure qui prime ensuite.
POIDS_MELANGE_DEFAUT: Final[float] = 0.3

# Le plafond ci-dessus est PAR PARI. En cumulant plusieurs championnats sur une
# meme journee, on peut engager une part importante de la bankroll sans qu'aucun
# pari ne depasse ses 2 %. Ce seuil ne bride rien : il declenche un avertissement
# visible, parce que la decision d'etaler ou non appartient au parieur, mais
# qu'il doit voir le total avant de la prendre.
SEUIL_ALERTE_EXPOSITION_JOUR: Final[float] = 0.10   # 10 % de bankroll sur une journee
SEUIL_DIVERGENCE_ALERTE: Final[float] = 0.15  # 15 points de % modele vs marche
MIN_BOOKMAKERS_CONSENSUS: Final[int] = 3

# En dessous de ce nombre de matchs dans l'historique charge, les forces d'une
# equipe ne veulent pas dire grand-chose : un promu avec deux matchs peut se
# voir attribuer une defense trois fois pire que la moyenne, et le modele
# annoncera 97 % la ou le marche dit 76 %. Le garde-fou de divergence rejette
# bien ces paris, mais les probabilites, elles, restent affichees : il faut
# donc dire a cote qu'elles reposent sur presque rien.
MIN_MATCHS_ESTIMATION_FIABLE: Final[int] = 10
QUOTA_MENSUEL_ODDS_API: Final[int] = 500
SEUIL_AVERTISSEMENT_QUOTA: Final[int] = 400

# API-Football, plan gratuit. Un appel /injuries?date= rapporte toutes les
# ligues de la date : deux appels suffisent a couvrir aujourd'hui et demain.
QUOTA_QUOTIDIEN_API_FOOTBALL: Final[int] = 100

# Au-dela de ce nombre d'absents dans une equipe, le modele est entraine sur
# un effectif qui ne jouera pas. Ce n'est pas un ajustement de probabilite --
# il n'y en a aucun -- mais une reserve affichee a cote du prono.
SEUIL_ABSENCES_NOTABLES: Final[int] = 3
TTL_COTES_SECONDES: Final[int] = 15 * 60
TTL_SAISON_COURANTE_SECONDES: Final[int] = 6 * 3600

AVERTISSEMENT_LEGAL: Final[str] = (
    "Outil d'analyse statistique. Les probabilites affichees sont des estimations "
    "assorties d'une marge d'erreur reelle, pas des pronostics. Les paris sportifs "
    "comportent un risque de perte financiere et d'addiction."
)


# --------------------------------------------------------------------------
# Cles API
# --------------------------------------------------------------------------

class CleApiManquante(RuntimeError):
    """Levee quand une cle API necessaire n'est pas configuree."""


# nom logique -> (variable d'environnement, service, URL d'inscription)
_SERVICES: Final[dict[str, tuple[str, str, str]]] = {
    "odds": ("ODDS_API_KEY", "The Odds API (cotes multi-bookmakers)",
             "https://the-odds-api.com/#get-access"),
    "football_data_org": ("FOOTBALL_DATA_ORG_KEY", "football-data.org (calendrier, resultats)",
                          "https://www.football-data.org/client/register"),
    "api_football": ("API_FOOTBALL_KEY", "API-Football (blessures, compositions)",
                     "https://www.api-football.com/pricing"),
}

# Ce que chaque service offre gratuitement, et si cet outil l'appelle vraiment.
# football-data.org reste marque comme inutilise, et ce n'est pas un oubli : sur
# le plan gratuit, ses arbitres ne sont jamais renseignes (mesure : 0 match sur
# 373), ses cotes sont derriere un paquet payant, et son classement n'apporte
# rien que les CSV football-data.co.uk ne donnent deja en plus riche. Afficher
# un compteur pour ce service laisserait croire a une fonctionnalite qui
# n'existe pas.
_QUOTAS: Final[dict[str, tuple[int, str, bool]]] = {
    "odds": (QUOTA_MENSUEL_ODDS_API, "mois", True),
    "football_data_org": (10, "minute", False),
    "api_football": (QUOTA_QUOTIDIEN_API_FOOTBALL, "jour", True),
}


def services() -> list[dict[str, object]]:
    """Description des trois services : nom, quota gratuit, usage reel."""
    lignes = []
    for nom, (variable, libelle, inscription) in _SERVICES.items():
        limite, periode, utilise = _QUOTAS[nom]
        lignes.append({
            "nom": nom, "libelle": libelle, "variable": variable,
            "inscription": inscription, "limite": limite, "periode": periode,
            "utilise_par_l_outil": utilise,
            "cle_configuree": cle_api(nom, obligatoire=False) is not None,
        })
    return lignes


_env_charge = False


def _charger_env() -> None:
    """Charge le .env une seule fois par processus."""
    global _env_charge
    if not _env_charge:
        load_dotenv(CHEMIN_ENV, override=False)
        _env_charge = True


def cle_api(nom: str, obligatoire: bool = True) -> str | None:
    """Renvoie la cle API demandee.

    `nom` vaut "odds", "football_data_org" ou "api_football".
    Si la cle manque et que `obligatoire` est vrai, leve une CleApiManquante
    dont le message dit quel fichier editer et ou obtenir la cle.
    """
    if nom not in _SERVICES:
        raise KeyError(
            f"Service API inconnu : {nom!r}. Services connus : {', '.join(_SERVICES)}"
        )
    _charger_env()
    variable, service, url = _SERVICES[nom]
    valeur = (os.environ.get(variable) or "").strip()
    if valeur and not valeur.lower().startswith("votre_"):
        return valeur
    if not obligatoire:
        return None
    raise CleApiManquante(
        f"Cle API manquante pour {service}.\n"
        f"  1. Ouvre (ou cree) le fichier : {CHEMIN_ENV}\n"
        f"     Tu peux partir du modele : {RACINE_PROJET / '.env.example'}\n"
        f"  2. Ajoute la ligne : {variable}=ta_cle_ici\n"
        f"  3. Obtiens une cle gratuite ici : {url}\n"
        f"  (ou definis la variable d'environnement {variable})"
    )


def cles_disponibles() -> dict[str, bool]:
    """Etat de configuration de chaque service, pour l'ecran de diagnostic."""
    return {nom: cle_api(nom, obligatoire=False) is not None for nom in _SERVICES}


creer_dossiers()
