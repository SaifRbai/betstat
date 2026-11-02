"""Telechargement, cache et nettoyage des resultats football-data.co.uk.

Source unique : https://www.football-data.co.uk/mmz4281/{saison}/{ligue}.csv
et, pour les championnats joues sur l'annee civile (Bresil, Argentine, MLS...) et pour les championnats "extra", le fichier unique
https://www.football-data.co.uk/new/{ligue}.csv qui contient toutes les saisons.
Seule exception : la Serie B bresilienne, absente de football-data.co.uk, dont
les resultats viennent d'API-Football et sont convertis au meme format CSV.
C'est un CSV gratuit, sans cle, qui contient scores, statistiques de match et
cotes historiques de plusieurs bookmakers. Aucun scraping n'est effectue.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

import config
from data import espn as espn_module

_journal = logging.getLogger("foot_edge.resultats")

URL_MODELE = "https://www.football-data.co.uk/mmz4281/{saison}/{ligue}.csv"
URL_ANNEE_CIVILE = "https://www.football-data.co.uk/new/{ligue}.csv"
# Code "saison" du fichier unique d'un championnat sur l'annee civile : il
# contient toutes les saisons, y compris celle en cours, et perime donc comme elle.
FICHIER_UNIQUE = "tout"
DELAI_RESEAU = 20  # secondes

# Colonnes brutes -> colonnes normalisees en francais.
_RENOMMAGE: dict[str, str] = {
    "HomeTeam": "dom",
    "AwayTeam": "ext",
    "FTHG": "buts_dom",
    "FTAG": "buts_ext",
    "FTR": "resultat",
    "HTHG": "buts_dom_mt",
    "HTAG": "buts_ext_mt",
    "HS": "tirs_dom",
    "AS": "tirs_ext",
    "HST": "tirs_cadres_dom",
    "AST": "tirs_cadres_ext",
    "HF": "fautes_dom",
    "AF": "fautes_ext",
    "HC": "corners_dom",
    "AC": "corners_ext",
    "HY": "jaunes_dom",
    "AY": "jaunes_ext",
    "HR": "rouges_dom",
    "AR": "rouges_ext",
}

# Les fichiers "new/" (championnats sur l'annee civile) nomment autrement les
# memes colonnes ; on les ramene au format mmz4281 avant tout le reste.
_COLONNES_FICHIER_UNIQUE: dict[str, str] = {
    "Home": "HomeTeam",
    "Away": "AwayTeam",
    "HG": "FTHG",
    "AG": "FTAG",
    "Res": "FTR",
}

# Cotes : on prend la moyenne du marche en priorite (Avg), sinon Bet365, sinon
# Pinnacle, sinon la meilleure cote disponible. Les colonnes "_max" gardent la
# meilleure cote proposee, indispensable pour un backtest realiste. Les cotes de
# cloture (colonnes en "C") ne servent qu'en dernier recours : ce sont les seules
# que publient les fichiers "new/" (Bresil).
_COTES_MOYENNES: dict[str, tuple[str, ...]] = {
    "cote_1": ("AvgH", "B365H", "PSH", "BWH", "MaxH", "AvgCH", "B365CH", "PSCH", "MaxCH"),
    "cote_N": ("AvgD", "B365D", "PSD", "BWD", "MaxD", "AvgCD", "B365CD", "PSCD", "MaxCD"),
    "cote_2": ("AvgA", "B365A", "PSA", "BWA", "MaxA", "AvgCA", "B365CA", "PSCA", "MaxCA"),
    "cote_plus25": ("Avg>2.5", "B365>2.5", "P>2.5", "Max>2.5"),
    "cote_moins25": ("Avg<2.5", "B365<2.5", "P<2.5", "Max<2.5"),
}
_COTES_MAX: dict[str, tuple[str, ...]] = {
    "cote_1_max": ("MaxH", "B365H", "AvgH", "MaxCH", "B365CH", "AvgCH"),
    "cote_N_max": ("MaxD", "B365D", "AvgD", "MaxCD", "B365CD", "AvgCD"),
    "cote_2_max": ("MaxA", "B365A", "AvgA", "MaxCA", "B365CA", "AvgCA"),
    "cote_plus25_max": ("Max>2.5", "B365>2.5", "Avg>2.5"),
    "cote_moins25_max": ("Max<2.5", "B365<2.5", "Avg<2.5"),
}

COLONNES_NORMALISEES: tuple[str, ...] = (
    "date", "saison", "championnat", "dom", "ext",
    "buts_dom", "buts_ext", "total_buts", "resultat",
    "buts_dom_mt", "buts_ext_mt",
    "tirs_dom", "tirs_ext", "tirs_cadres_dom", "tirs_cadres_ext",
    "fautes_dom", "fautes_ext", "corners_dom", "corners_ext",
    "jaunes_dom", "jaunes_ext", "rouges_dom", "rouges_ext",
    *_COTES_MOYENNES, *_COTES_MAX,
)


# --------------------------------------------------------------------------
# Saisons
# --------------------------------------------------------------------------

def saison_courante(aujourdhui: date | None = None) -> str:
    """Code de la saison en cours au format football-data ("2526")."""
    jour = aujourdhui or date.today()
    debut = jour.year if jour.month >= 7 else jour.year - 1
    return f"{debut % 100:02d}{(debut + 1) % 100:02d}"


def dernieres_saisons(nombre: int, aujourdhui: date | None = None) -> list[str]:
    """Les `nombre` derniers codes saison, de la plus ancienne a la plus recente."""
    jour = aujourdhui or date.today()
    debut = jour.year if jour.month >= 7 else jour.year - 1
    codes = [
        f"{(debut - i) % 100:02d}{(debut - i + 1) % 100:02d}"
        for i in range(max(1, nombre))
    ]
    return list(reversed(codes))


def annee_civile(ligue: str) -> bool:
    """Ce championnat joue-t-il sa saison sur l'annee civile (Bresil) ?"""
    return ligue.upper() in config.CHAMPIONNATS_ANNEE_CIVILE


def fichier_unique(ligue: str) -> bool:
    """football-data publie-t-il ce championnat dans un seul fichier new/ ?"""
    return ligue.upper() in config.CHAMPIONNATS_FICHIER_UNIQUE


def libelles_saison(code: str) -> tuple[str, str]:
    """Code saison "2627" -> ("2026/2027", "2026"), tels que dans la colonne Season.

    Le second couvre un championnat passe de l'annee civile a une saison a
    cheval (Japon en 2026) : sa saison "2025" devient le code "2526".
    """
    debut = 2000 + int(code[:2])
    return f"{debut}/{debut + 1}", str(debut)


def dernieres_annees(nombre: int, aujourdhui: date | None = None) -> list[str]:
    """Les `nombre` dernieres saisons civiles ("2026"), la plus ancienne d'abord.

    L'annee en cours compte meme avant son coup d'envoi, comme la saison
    europeenne en aout : elle est alors simplement vide.
    """
    annee = (aujourdhui or date.today()).year
    return [str(annee - i) for i in reversed(range(max(1, nombre)))]


# --------------------------------------------------------------------------
# Cache et telechargement
# --------------------------------------------------------------------------

def ligue_api_football(ligue: str) -> int | None:
    """Identifiant API-Football si ce championnat n'existe pas chez football-data."""
    return config.CHAMPIONNATS.get(ligue.upper(), {}).get("api_football")


def chemin_cache(ligue: str, saison: str) -> Path:
    return config.DOSSIER_CACHE_RESULTATS / f"{ligue.upper()}_{saison}.csv"


def _ressemble_a_du_csv(contenu: bytes) -> bool:
    """Un CSV football-data, ou une page d'erreur deguisee ?

    Quand une saison n'est pas encore publiee, le serveur repond
    « 300 Multiple Choices » avec une page HTML. Elle contient des virgules et
    ne declenche pas raise_for_status() : sans ce controle elle finit en cache
    sous un nom .csv, et la saison en cours disparait sans un mot.
    """
    if len(contenu) < 200:
        return False
    debut = contenu[:400].lstrip().lower()
    if debut.startswith(b"<") or b"<html" in debut:
        return False
    return b"," in contenu[:400]


def _cache_valide(chemin: Path, saison: str) -> bool:
    """Une saison passee ne perime jamais ; la saison en cours vit 6 heures."""
    if not chemin.exists() or chemin.stat().st_size == 0:
        return False
    with chemin.open("rb") as fichier:      # purge un cache empoisonne
        if not _ressemble_a_du_csv(fichier.read(400)):
            return False
    if saison not in (saison_courante(), str(date.today().year), FICHIER_UNIQUE):
        return True
    age = time.time() - chemin.stat().st_mtime
    return age < config.TTL_SAISON_COURANTE_SECONDES


class SaisonNonPubliee(ValueError):
    """Le serveur repond, mais le fichier de cette saison n'existe pas encore.

    Ce n'est pas une panne : en aout, la saison qui commence n'est publiee
    qu'apres les premieres journees, et pas le meme jour pour tous les
    championnats. On le distingue d'une vraie erreur reseau pour ne pas crier
    au loup a chaque chargement.
    """


class SaisonInaccessible(SaisonNonPubliee):
    """La saison existe, mais l'abonnement API-Football ne la couvre pas.

    Le plan gratuit refuse tout ce qui sort de 2022-2024 : rien a voir avec une
    saison pas encore commencee, et ca ne se reglera pas tout seul.
    """


def _chemin_absence(ligue: str, saison: str) -> Path:
    return chemin_cache(ligue, saison).with_suffix(".absente")


def _absence_constatee_recemment(ligue: str, saison: str) -> bool:
    """Evite de redemander toutes les cinq secondes un fichier qui n'existe pas.

    Meme duree de validite que le cache de la saison en cours : si le fichier
    parait dans la journee, on le verra a la prochaine peremption.
    """
    marqueur = _chemin_absence(ligue, saison)
    if not marqueur.exists():
        return False
    return (time.time() - marqueur.stat().st_mtime) < config.TTL_SAISON_COURANTE_SECONDES


def telecharger(ligue: str, saison: str, forcer: bool = False) -> Path | None:
    """Assure la presence du CSV en cache et renvoie son chemin.

    En cas de coupure reseau, on retombe silencieusement sur le cache existant
    (avec un avertissement) plutot que de planter. Renvoie None si aucune
    donnee n'est disponible ni en ligne ni en cache.
    """
    config.creer_dossiers()
    chemin = chemin_cache(ligue, saison)
    if not forcer and _cache_valide(chemin, saison):
        return chemin
    if not forcer and _absence_constatee_recemment(ligue, saison):
        return None
    if chemin.exists() and not _ressemble_a_du_csv(chemin.read_bytes()[:400]):
        # Une page d'erreur enregistree sous un nom .csv par une version
        # anterieure : elle ne redeviendra jamais valide, autant la retirer.
        _journal.info("Cache invalide retire : %s", chemin.name)
        chemin.unlink()

    if saison == FICHIER_UNIQUE:
        url = URL_ANNEE_CIVILE.format(ligue=ligue.upper())
    else:
        url = URL_MODELE.format(saison=saison, ligue=ligue.upper())
    try:
        if espn_module.slug(ligue):
            contenu = espn_module.csv_saison(ligue, saison)
            if not contenu:
                raise SaisonNonPubliee("aucun match termine sur ESPN pour cette saison")
        elif ligue_api_football(ligue):
            contenu = _csv_api_football(ligue, saison)
        else:
            reponse = requests.get(url, timeout=DELAI_RESEAU)
            reponse.raise_for_status()
            contenu = reponse.content
        if not _ressemble_a_du_csv(contenu):
            raise SaisonNonPubliee(
                "le fichier n'est pas encore publie sur football-data.co.uk")
        chemin.write_bytes(contenu)
        _chemin_absence(ligue, saison).unlink(missing_ok=True)
        _journal.info("Telecharge %s saison %s (%d octets)", ligue, saison, len(contenu))
        return chemin
    except SaisonInaccessible as erreur:
        _chemin_absence(ligue, saison).write_text("", encoding="utf-8")
        _journal.warning("%s saison %s : %s", ligue, saison, erreur)
        return None
    except SaisonNonPubliee as erreur:
        # Cas normal en debut de saison, et non une panne : on le note une fois
        # au journal detaille, et on ne redemande pas avant six heures.
        _chemin_absence(ligue, saison).write_text("", encoding="utf-8")
        _journal.info("%s saison %s : %s. On travaille avec les saisons precedentes.",
                      ligue, saison, erreur)
        return None
    except Exception as erreur:  # reseau, DNS, 404
        # Se rabattre sur le cache, oui — mais pas sur une page d'erreur qui y
        # dormirait : on dirait « reseau indisponible » alors que la saison
        # n'est simplement pas encore publiee.
        if chemin.exists() and _ressemble_a_du_csv(chemin.read_bytes()[:400]):
            _journal.warning(
                "Reseau indisponible pour %s %s (%s). Utilisation du cache du %s.",
                ligue, saison, type(erreur).__name__,
                datetime.fromtimestamp(chemin.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
            )
            return chemin
        _journal.warning(
            "Aucune donnee pour %s saison %s (%s). "
            "Verifie ta connexion, ou que ce championnat existe bien cette saison-la.",
            ligue, saison, _cause_lisible(erreur),
        )
        return None


def _nom_api_football(nom: str) -> str:
    """Nom d'equipe API-Football -> orthographe football-data quand elle existe.

    Un club relegue de Serie A garde ainsi le meme nom dans les deux divisions,
    ce qui permet au modele inter-divisions de s'en servir comme pont.
    """
    from data import noms as noms_module

    canonique = noms_module.canoniser(nom, "api_football")
    if canonique is None:
        return str(nom).strip()
    alias = noms_module.alias_connus(canonique, "football_data")
    return alias[0] if alias else canonique


def _csv_api_football(ligue: str, saison: str) -> bytes:
    """Matchs termines d'une saison API-Football, au format CSV football-data.

    Une requete par saison, comptee sur le meme quota quotidien que les
    absences et les scores : c'est la meme cle.
    """
    from data import absences as absences_module

    if absences_module.lire_compteur()["appels"] >= config.QUOTA_QUOTIDIEN_API_FOOTBALL:
        raise absences_module.QuotaEpuise("quota quotidien API-Football atteint")
    try:
        matchs, restant = absences_module._appeler(
            "fixtures", {"league": ligue_api_football(ligue), "season": saison})
    except absences_module.HorsFenetre:
        absences_module.enregistrer_appel(None)
        raise SaisonInaccessible(
            "l'abonnement API-Football ne couvre pas cette saison (le plan "
            "gratuit s'arrete a 2024). Sans abonnement payant, ce championnat "
            "n'a pas d'historique recent.") from None
    absences_module.enregistrer_appel(restant)

    lignes = []
    for match in matchs:
        if match["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
            continue
        temps = match["score"]["fulltime"]       # score a 90 minutes
        mi_temps = match["score"]["halftime"]
        if temps["home"] is None or temps["away"] is None:
            continue
        coup_envoi = datetime.fromisoformat(match["fixture"]["date"])
        lignes.append({
            "Div": ligue.upper(),
            "Date": coup_envoi.strftime("%d/%m/%Y"),
            "Time": coup_envoi.strftime("%H:%M"),
            "HomeTeam": _nom_api_football(match["teams"]["home"]["name"]),
            "AwayTeam": _nom_api_football(match["teams"]["away"]["name"]),
            "FTHG": temps["home"],
            "FTAG": temps["away"],
            "FTR": ("H" if temps["home"] > temps["away"]
                    else "A" if temps["home"] < temps["away"] else "D"),
            "HTHG": mi_temps["home"],
            "HTAG": mi_temps["away"],
        })
    colonnes = ["Div", "Date", "Time", "HomeTeam", "AwayTeam",
                "FTHG", "FTAG", "FTR", "HTHG", "HTAG"]
    return pd.DataFrame(lignes, columns=colonnes).to_csv(index=False).encode("utf-8")


def _cause_lisible(erreur: Exception) -> str:
    """Resume une exception reseau en quelques mots plutot qu'en dix lignes."""
    if isinstance(erreur, requests.ConnectionError):
        return "pas de connexion internet"
    if isinstance(erreur, requests.Timeout):
        return "delai depasse"
    if isinstance(erreur, requests.HTTPError):
        code = erreur.response.status_code if erreur.response is not None else "?"
        return f"reponse HTTP {code}"
    if isinstance(erreur, ValueError):
        return str(erreur)
    return type(erreur).__name__


# --------------------------------------------------------------------------
# Nettoyage
# --------------------------------------------------------------------------

def analyser_dates(brutes: pd.Series) -> pd.Series:
    """Parse des dates football-data, dont le format change selon les saisons.

    On rencontre %d/%m/%y (anciennes saisons) et %d/%m/%Y (recentes), parfois
    melanges dans un meme fichier. On essaie chaque format explicitement puis
    on complete au cas par cas.
    """
    texte = brutes.astype("string").str.strip()
    resultat = pd.Series(pd.NaT, index=texte.index, dtype="datetime64[ns]")
    for format_essai in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y"):
        manquants = resultat.isna() & texte.notna()
        if not manquants.any():
            break
        essai = pd.to_datetime(texte[manquants], format=format_essai, errors="coerce")
        resultat.loc[manquants] = essai
    manquants = resultat.isna() & texte.notna()
    if manquants.any():  # dernier recours, ligne par ligne
        essai = pd.to_datetime(texte[manquants], errors="coerce", dayfirst=True)
        resultat.loc[manquants] = essai
    return resultat


def _premiere_colonne(df: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    """Premiere colonne existante et non vide parmi les candidates."""
    valeurs = pd.Series(float("nan"), index=df.index, dtype="float64")
    for nom in candidates:
        if nom in df.columns:
            colonne = pd.to_numeric(df[nom], errors="coerce")
            valeurs = valeurs.where(valeurs.notna(), colonne)
    return valeurs


def nettoyer(brut: pd.DataFrame, saison: str, ligue: str) -> pd.DataFrame:
    """Normalise un CSV football-data brut vers les colonnes du projet."""
    df = brut.copy()
    df.columns = [str(c).strip().lstrip("﻿") for c in df.columns]
    if "HomeTeam" not in df.columns:
        df = df.rename(columns=_COLONNES_FICHIER_UNIQUE)

    if "Date" not in df.columns or "HomeTeam" not in df.columns:
        return pd.DataFrame(columns=list(COLONNES_NORMALISEES))

    sortie = pd.DataFrame(index=df.index)
    sortie["date"] = analyser_dates(df["Date"])
    sortie["saison"] = saison
    sortie["championnat"] = ligue.upper()

    for source, cible in _RENOMMAGE.items():
        if source in df.columns:
            sortie[cible] = df[source]
        else:
            sortie[cible] = pd.NA

    for cible in ("dom", "ext", "resultat"):
        sortie[cible] = sortie[cible].astype("string").str.strip()

    non_numeriques = {"dom", "ext", "resultat"}
    numeriques = [c for c in _RENOMMAGE.values() if c not in non_numeriques]
    for colonne in numeriques:
        sortie[colonne] = pd.to_numeric(sortie[colonne], errors="coerce")

    for cible, candidates in {**_COTES_MOYENNES, **_COTES_MAX}.items():
        sortie[cible] = _premiere_colonne(df, candidates)

    sortie["total_buts"] = sortie["buts_dom"] + sortie["buts_ext"]

    # Une ligne sans date, sans equipes ou sans score n'est pas un match joue.
    sortie = sortie.dropna(subset=["date", "dom", "ext", "buts_dom", "buts_ext"])
    sortie = sortie[(sortie["dom"] != "") & (sortie["ext"] != "")]
    sortie = sortie.reindex(columns=list(COLONNES_NORMALISEES))
    return sortie.sort_values("date").reset_index(drop=True)


def lire_csv(chemin: Path) -> pd.DataFrame:
    """Lecture tolerante d'un CSV football-data (BOM, lignes vides, encodage)."""
    for encodage in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(chemin, encoding=encodage, on_bad_lines="skip")
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
    return pd.DataFrame()


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------

def charger(
    ligue: str,
    nb_saisons: int = 3,
    forcer: bool = False,
    aujourdhui: date | None = None,
) -> pd.DataFrame:
    """Historique normalise d'un championnat sur les `nb_saisons` dernieres saisons.

    Ne leve jamais d'exception reseau : en l'absence de connexion, renvoie ce
    que contient le cache, eventuellement un DataFrame vide.
    """
    ligue = ligue.upper()
    if ligue not in config.CHAMPIONNATS:
        connus = ", ".join(config.CODES_CHAMPIONNATS)
        raise ValueError(f"Championnat inconnu : {ligue!r}. Codes valides : {connus}")

    morceaux: list[pd.DataFrame] = []
    for saison, brut in _saisons_brutes(ligue, nb_saisons, forcer, aujourdhui):
        propre = nettoyer(brut, saison, ligue)
        if not propre.empty:
            morceaux.append(propre)

    if not morceaux:
        _journal.warning(
            "Aucun match pour %s. Cache vide et reseau indisponible : "
            "relance quand la connexion est retablie.", ligue,
        )
        return pd.DataFrame(columns=list(COLONNES_NORMALISEES))

    total = pd.concat(morceaux, ignore_index=True)
    total = total.drop_duplicates(subset=["date", "dom", "ext"], keep="last")
    return total.sort_values("date").reset_index(drop=True)


def _saisons_brutes(
    ligue: str, nb_saisons: int, forcer: bool, aujourdhui: date | None,
) -> list[tuple[str, pd.DataFrame]]:
    """(code saison, CSV brut) pour chaque saison demandee et disponible.

    Un fichier par saison chez football-data (et un par saison API-Football),
    sauf pour les championnats publies dans un fichier unique (new/) : un seul
    fichier, decoupe ici selon sa colonne Season.
    """
    if not fichier_unique(ligue):
        codes = (dernieres_annees(nb_saisons, aujourdhui) if annee_civile(ligue)
                 else dernieres_saisons(nb_saisons, aujourdhui))
        sortie = []
        for saison in codes:
            chemin = telecharger(ligue, saison, forcer=forcer)
            if chemin is not None:
                sortie.append((saison, lire_csv(chemin)))
        if ligue_api_football(ligue) and not {s for s, _ in sortie} & set(codes[-2:]):
            # Sans la saison en cours ni la precedente (plan gratuit : rien apres
            # 2024), le modele estimerait des effectifs d'il y a deux ans ou plus.
            # Mieux vaut un championnat vide qu'un prono sur une equipe disparue.
            if sortie:
                _journal.warning(
                    "%s : aucune saison recente (%s) — l'historique de %s est trop "
                    "ancien pour estimer quoi que ce soit, on ne l'utilise pas.",
                    ligue, " ou ".join(codes[-2:]), ", ".join(s for s, _ in sortie))
            return []
        return sortie

    chemin = telecharger(ligue, FICHIER_UNIQUE, forcer=forcer)
    if chemin is None:
        return []
    brut = lire_csv(chemin)
    brut.columns = [str(c).strip().lstrip("﻿") for c in brut.columns]
    if "Season" not in brut.columns:
        return []
    annees = brut["Season"].astype("string").str.strip()
    if annee_civile(ligue):
        return [(annee, brut[annees == annee])
                for annee in dernieres_annees(nb_saisons, aujourdhui)]
    return [(code, brut[annees.isin(libelles_saison(code))])
            for code in dernieres_saisons(nb_saisons, aujourdhui)]


def nb_matchs_par_equipe(df: pd.DataFrame) -> dict[str, int]:
    """Combien de matchs chaque equipe a joues dans l'historique charge.

    Sert a savoir a quel point une estimation est appuyee : un promu qui compte
    deux rencontres n'a pas des forces comparables a celles d'une equipe qui en
    a cent vingt.
    """
    if df.empty:
        return {}
    comptes = pd.concat([df["dom"], df["ext"]]).value_counts()
    return {str(equipe): int(nombre) for equipe, nombre in comptes.items()}


def equipes(df: pd.DataFrame) -> list[str]:
    """Liste triee des equipes presentes dans un historique."""
    if df.empty:
        return []
    noms = pd.concat([df["dom"], df["ext"]]).dropna().astype(str).unique()
    return sorted(noms)
