"""xG (expected goals) reels par match, via la bibliotheque soccerdata.

Pourquoi c'est le module le plus utile du projet : les buts sont extremement
bruites (une frappe deviee change un resultat). Les xG mesurent la qualite des
occasions creees. Un modele entraine sur les xG converge avec deux a trois fois
moins de matchs et predit mieux.

Limites reelles, assumees ici :
  - les xG publics n'existent que pour les 5 grands championnats
  - et seulement a partir de 2017 environ
Pour tout le reste, ce module renvoie proprement un DataFrame vide et le dit
dans les logs, il ne plante pas et n'invente rien.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

import config
from data import noms as noms_module
from data import resultats as resultats_module

_journal = logging.getLogger("foot_edge.xg")

COLONNES_XG: tuple[str, ...] = ("date", "dom", "ext", "xg_dom", "xg_ext", "source")

# Premiere saison ou des xG publics existent (code football-data).
PREMIERE_SAISON_XG = "1718"

TAUX_APPARIEMENT_MINIMAL = 0.90


def _dataframe_vide() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.Series(dtype="datetime64[ns]"),
        "dom": pd.Series(dtype="string"),
        "ext": pd.Series(dtype="string"),
        "xg_dom": pd.Series(dtype="float64"),
        "xg_ext": pd.Series(dtype="float64"),
        "source": pd.Series(dtype="string"),
    })


def championnat_couvert(championnat: str) -> bool:
    """Vrai si des xG publics existent pour ce championnat."""
    return championnat.upper() in config.CHAMPIONNATS_AVEC_XG


def _saison_trop_ancienne(saison: str) -> bool:
    return saison < PREMIERE_SAISON_XG


# --------------------------------------------------------------------------
# Cache disque
# --------------------------------------------------------------------------

def _chemin_cache(source: str, championnat: str, saison: str) -> Path:
    return config.DOSSIER_CACHE_XG / f"{source}_{championnat.upper()}_{saison}.csv"


def _cache_utilisable(chemin: Path, saison: str) -> bool:
    """Une saison passee n'est JAMAIS retelechargee : ses xG ne bougent plus."""
    if not chemin.exists():
        return False
    if saison != resultats_module.saison_courante():
        return True
    return (time.time() - chemin.stat().st_mtime) < config.TTL_SAISON_COURANTE_SECONDES


def _lire_cache(chemin: Path) -> pd.DataFrame:
    df = pd.read_csv(chemin, parse_dates=["date"])
    for colonne in ("dom", "ext", "source"):
        df[colonne] = df[colonne].astype("string")
    return df


# --------------------------------------------------------------------------
# Lecture des sources
# --------------------------------------------------------------------------

def _importer_soccerdata():
    try:
        import soccerdata  # noqa: PLC0415
    except ImportError as erreur:
        raise RuntimeError(
            "La bibliotheque soccerdata n'est pas installee.\n"
            "  Installe-la avec : pip install soccerdata\n"
            "  Sans elle, le projet fonctionne mais sans xG (mode 'buts' uniquement)."
        ) from erreur
    return soccerdata


def _brut_understat(cle_ligue: str, saison: str) -> pd.DataFrame:
    soccerdata = _importer_soccerdata()
    calendrier = soccerdata.Understat(leagues=cle_ligue, seasons=saison).read_schedule()
    calendrier = calendrier.reset_index()
    if "home_xg" not in calendrier.columns:
        return _dataframe_vide()
    joues = calendrier[calendrier.get("is_result", True).astype(bool)]
    sortie = pd.DataFrame({
        "date": pd.to_datetime(joues["date"]).dt.normalize(),
        "dom": joues["home_team"].astype("string"),
        "ext": joues["away_team"].astype("string"),
        "xg_dom": pd.to_numeric(joues["home_xg"], errors="coerce"),
        "xg_ext": pd.to_numeric(joues["away_xg"], errors="coerce"),
        "source": "understat",
    })
    return sortie.dropna(subset=["xg_dom", "xg_ext"]).reset_index(drop=True)


def _brut_fbref(cle_ligue: str, saison: str) -> pd.DataFrame:
    """FBref n'expose pas toujours les xG par match selon le championnat.

    On lit le calendrier et on ne garde les xG que s'ils sont reellement la.
    Aucune valeur n'est reconstituee ou devinee.
    """
    soccerdata = _importer_soccerdata()
    calendrier = soccerdata.FBref(leagues=cle_ligue, seasons=saison).read_schedule()
    calendrier = calendrier.reset_index()
    colonnes = {c.lower(): c for c in calendrier.columns}
    col_dom = colonnes.get("home_xg") or colonnes.get("xg_home")
    col_ext = colonnes.get("away_xg") or colonnes.get("xg_away")
    if not col_dom or not col_ext:
        _journal.info("FBref n'expose pas de xG par match pour %s %s.", cle_ligue, saison)
        return _dataframe_vide()
    sortie = pd.DataFrame({
        "date": pd.to_datetime(calendrier["date"]).dt.normalize(),
        "dom": calendrier["home_team"].astype("string"),
        "ext": calendrier["away_team"].astype("string"),
        "xg_dom": pd.to_numeric(calendrier[col_dom], errors="coerce"),
        "xg_ext": pd.to_numeric(calendrier[col_ext], errors="coerce"),
        "source": "fbref",
    })
    return sortie.dropna(subset=["xg_dom", "xg_ext"]).reset_index(drop=True)


_LECTEURS = {"understat": _brut_understat, "fbref": _brut_fbref}


def _charger_saison(championnat: str, saison: str, source: str) -> pd.DataFrame:
    """Une saison de xG, depuis le cache si possible."""
    chemin = _chemin_cache(source, championnat, saison)
    if _cache_utilisable(chemin, saison):
        return _lire_cache(chemin)

    info = config.CHAMPIONNATS[championnat.upper()]
    cle_ligue = info.get(source)
    if not cle_ligue:
        return _dataframe_vide()

    try:
        brut = _LECTEURS[source](str(cle_ligue), saison)
    except Exception as erreur:
        if chemin.exists():
            _journal.warning(
                "%s injoignable pour %s %s (%s) : lecture du cache.",
                source, championnat, saison, type(erreur).__name__,
            )
            return _lire_cache(chemin)
        _journal.warning(
            "xG indisponibles pour %s saison %s via %s : %s. "
            "Le modele fonctionnera sur les buts seuls.",
            championnat, saison, source, erreur,
        )
        return _dataframe_vide()

    if brut.empty:
        return brut

    brut["dom"] = brut["dom"].map(
        lambda nom: noms_module.canoniser(nom, source, identite_si_inconnu=True)
    ).astype("string")
    brut["ext"] = brut["ext"].map(
        lambda nom: noms_module.canoniser(nom, source, identite_si_inconnu=True)
    ).astype("string")

    config.creer_dossiers()
    brut.to_csv(chemin, index=False)
    return brut


def charger_xg(
    championnat: str,
    nb_saisons: int = 3,
    source: str = "understat",
) -> pd.DataFrame:
    """xG par match, noms deja canonises, pour les `nb_saisons` dernieres saisons."""
    championnat = championnat.upper()
    if championnat not in config.CHAMPIONNATS:
        raise ValueError(f"Championnat inconnu : {championnat!r}")
    if not championnat_couvert(championnat):
        _journal.info(
            "Pas de xG publics pour %s (%s) : seuls les 5 grands championnats "
            "en ont. Le modele utilisera les buts.",
            config.nom_championnat(championnat), championnat,
        )
        return _dataframe_vide()

    morceaux = []
    for saison in resultats_module.dernieres_saisons(nb_saisons):
        if _saison_trop_ancienne(saison):
            _journal.info("Saison %s anterieure aux xG publics : ignoree.", saison)
            continue
        saison_df = _charger_saison(championnat, saison, source)
        if not saison_df.empty:
            morceaux.append(saison_df)

    if not morceaux:
        return _dataframe_vide()
    total = pd.concat(morceaux, ignore_index=True)
    total = total.drop_duplicates(subset=["date", "dom", "ext"], keep="last")
    return total.sort_values("date").reset_index(drop=True)


def _calendrier_brut(source: str, cle_ligue: str, saison: str) -> pd.DataFrame:
    """Calendrier d'une source, xG ou non : sert au seul appariement des noms."""
    soccerdata = _importer_soccerdata()
    lecteur = soccerdata.Understat if source == "understat" else soccerdata.FBref
    return lecteur(leagues=cle_ligue, seasons=saison).read_schedule().reset_index()


def equipes_source(championnat: str, source: str = "understat", nb_saisons: int = 2) -> list[str]:
    """Equipes vues par une source (sert a outils/verifier_noms.py).

    Renvoie les noms BRUTS de la source, non canonises : c'est justement ce
    qu'on veut inspecter pour completer la table de correspondance. On lit le
    calendrier et non les xG : une source peut tres bien connaitre les equipes
    d'un championnat sans en publier les xG par match, et on veut quand meme
    pouvoir apparier ses noms.
    """
    championnat = championnat.upper()
    info = config.CHAMPIONNATS.get(championnat, {})
    cle_ligue = info.get(source)
    if not cle_ligue:
        return []
    noms_vus: set[str] = set()
    for saison in resultats_module.dernieres_saisons(nb_saisons):
        if _saison_trop_ancienne(saison):
            continue
        try:
            calendrier = _calendrier_brut(source, str(cle_ligue), saison)
        except Exception as erreur:
            _journal.warning(
                "Calendrier %s indisponible pour %s %s (%s) : saison ignoree.",
                source, championnat, saison, type(erreur).__name__)
            continue
        for colonne in ("home_team", "away_team"):
            if colonne in calendrier.columns:
                noms_vus.update(calendrier[colonne].dropna().astype(str))
    return sorted(noms_vus)


# --------------------------------------------------------------------------
# Fusion avec les resultats
# --------------------------------------------------------------------------

def fusionner(
    matchs: pd.DataFrame,
    xg: pd.DataFrame,
    tolerance_jours: int = 1,
) -> tuple[pd.DataFrame, dict]:
    """Ajoute xg_dom / xg_ext aux resultats de T02.

    L'appariement se fait sur (equipe dom, equipe ext) canonisees et une date a
    +/- `tolerance_jours` : les sources ne s'accordent pas toujours sur le jour
    d'un match a cause des fuseaux horaires.
    Renvoie (DataFrame enrichi, rapport d'appariement).
    """
    enrichi = matchs.copy()
    enrichi["xg_dom"] = pd.Series(float("nan"), index=enrichi.index, dtype="float64")
    enrichi["xg_ext"] = pd.Series(float("nan"), index=enrichi.index, dtype="float64")

    rapport = {"total": len(enrichi), "apparies": 0, "taux": 0.0, "non_apparies": []}
    if matchs.empty or xg.empty:
        rapport["taux"] = 0.0 if not matchs.empty else 1.0
        if not matchs.empty:
            rapport["non_apparies"] = _lignes_non_appariees(enrichi)
        return enrichi, rapport

    gauche = (enrichi.drop(columns=["xg_dom", "xg_ext"])
              .reset_index().rename(columns={"index": "_ligne"}))
    gauche["_dom_c"] = gauche["dom"].map(
        lambda n: noms_module.canoniser(n, "football_data", identite_si_inconnu=True))
    gauche["_ext_c"] = gauche["ext"].map(
        lambda n: noms_module.canoniser(n, "football_data", identite_si_inconnu=True))

    droite = xg.rename(columns={"date": "_date_xg", "dom": "_dom_c", "ext": "_ext_c"})
    fusion = gauche.merge(droite, on=["_dom_c", "_ext_c"], how="inner")
    if not fusion.empty:
        ecart = (fusion["date"] - fusion["_date_xg"]).abs()
        fusion = fusion[ecart <= pd.Timedelta(days=tolerance_jours)].copy()
        fusion["_ecart"] = (fusion["date"] - fusion["_date_xg"]).abs()
        fusion = fusion.sort_values("_ecart").drop_duplicates(subset=["_ligne"], keep="first")

    if not fusion.empty:
        enrichi.loc[fusion["_ligne"].to_numpy(), "xg_dom"] = fusion["xg_dom"].to_numpy()
        enrichi.loc[fusion["_ligne"].to_numpy(), "xg_ext"] = fusion["xg_ext"].to_numpy()

    apparies = int(enrichi["xg_dom"].notna().sum())
    rapport["apparies"] = apparies
    rapport["taux"] = apparies / len(enrichi) if len(enrichi) else 1.0
    rapport["non_apparies"] = _lignes_non_appariees(enrichi)
    return enrichi, rapport


def _lignes_non_appariees(df: pd.DataFrame, maximum: int = 50) -> list[str]:
    manquants = df[df["xg_dom"].isna()]
    lignes = [
        f"{ligne.date:%d/%m/%Y} {ligne.dom} - {ligne.ext}"
        for ligne in manquants.head(maximum).itertuples()
    ]
    if len(manquants) > maximum:
        lignes.append(f"... et {len(manquants) - maximum} autres")
    return lignes


def afficher_rapport(rapport: dict, championnat: str = "") -> str:
    """Message lisible sur le taux d'appariement, journalise au niveau adequat."""
    taux = rapport["taux"]
    entete = f"xG {championnat} : {rapport['apparies']}/{rapport['total']} matchs apparies ({taux:.1%})"
    if rapport["total"] == 0:
        message = "Aucun match a apparier."
        _journal.info(message)
        return message
    if taux >= TAUX_APPARIEMENT_MINIMAL:
        _journal.info(entete)
        return entete
    detail = "\n  ".join(rapport["non_apparies"][:20])
    message = (
        f"{entete}\n"
        f"  Taux inferieur a {TAUX_APPARIEMENT_MINIMAL:.0%} : verifie la table de noms "
        f"(python outils/verifier_noms.py --championnat {championnat or 'F1'}).\n"
        f"  Matchs sans xG :\n  {detail}"
    )
    _journal.warning(message)
    return message


def charger_avec_xg(
    championnat: str,
    nb_saisons: int = 3,
    source: str = "understat",
) -> tuple[pd.DataFrame, dict]:
    """Raccourci : resultats T02 + xG apparies + rapport d'appariement."""
    matchs = resultats_module.charger(championnat, nb_saisons)
    xg = charger_xg(championnat, nb_saisons, source)
    enrichi, rapport = fusionner(matchs, xg)
    afficher_rapport(rapport, championnat)
    return enrichi, rapport
