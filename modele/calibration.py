"""Calibration : hyperparametres, de-vig, melange modele/marche.

Trois parties, dans l'ordre du cahier des charges :
  T06  optimiser_hyperparametres : choisir xi et w par validation temporelle
  T08  de-vig : retirer la marge du bookmaker (multiplicatif, puissance, Shin)
  T09  melange geometrique modele + marche, avec calibration isotonique optionnelle

Rappel de cadrage : le marche est l'estimateur le plus precis disponible. Le
modele ne sert pas a le remplacer mais a reperer ou il semble decale. Toutes
les fonctions de ce module renvoient donc systematiquement les probabilites du
modele ET du marche separement, jamais seulement leur melange.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import logging
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import brentq

import config
from modele import dixon_coles as dc

_journal = logging.getLogger("foot_edge.calibration")

Methode = Literal["multiplicatif", "puissance", "shin"]

GRILLE_XI = (0.0, 0.0005, 0.001, 0.002, 0.003, 0.005)
GRILLE_W = (0.0, 0.3, 0.5, 0.7, 1.0)
GRILLE_REGULARISATION = (0.0, 1.0, 3.0, 5.0, 8.0)
GRILLE_A = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)


# ==========================================================================
# Utilitaires communs
# ==========================================================================

def resultat_1n2(matchs: pd.DataFrame) -> pd.Series:
    """Issue reelle de chaque match : "1", "N" ou "2"."""
    ecart = matchs["buts_dom"] - matchs["buts_ext"]
    return pd.Series(
        np.where(ecart > 0, "1", np.where(ecart == 0, "N", "2")),
        index=matchs.index, dtype="string",
    )


def log_loss(probas: np.ndarray, issues: Sequence[str],
             ordre: Sequence[str] = ("1", "N", "2")) -> float:
    """Log-loss multiclasse. Plus c'est bas, mieux c'est.

    On prefere la log-loss a la precision : la precision ignore la confiance,
    et deux systemes qui donnent 51 % et 99 % au meme vainqueur n'ont pourtant
    pas du tout la meme valeur.
    """
    rang = {issue: i for i, issue in enumerate(ordre)}
    indices = np.array([rang[str(i)] for i in issues])
    choisies = probas[np.arange(len(indices)), indices]
    return float(-np.mean(np.log(np.clip(choisies, 1e-12, 1.0))))


def brier(probas: np.ndarray, issues: Sequence[str],
          ordre: Sequence[str] = ("1", "N", "2")) -> float:
    """Score de Brier multiclasse (erreur quadratique sur les probabilites)."""
    rang = {issue: i for i, issue in enumerate(ordre)}
    cible = np.zeros_like(probas)
    cible[np.arange(len(issues)), [rang[str(i)] for i in issues]] = 1.0
    return float(np.mean(np.sum((probas - cible) ** 2, axis=1)))


# ==========================================================================
# T06 — validation temporelle et choix de xi et w
# ==========================================================================

def _dates_de_test(matchs: pd.DataFrame, taille_initiale: int) -> list[pd.Timestamp]:
    """Journees a predire : toutes celles qui suivent la periode d'amorcage."""
    dates = sorted(pd.to_datetime(matchs["date"]).unique())
    cumul = matchs.sort_values("date").groupby("date").size().cumsum()
    return [d for d in dates if cumul.loc[d] > taille_initiale]


def _ligne_prediction(match, probas: dict) -> dict:
    """Une ligne de resultat walk-forward : prediction + cotes historiques."""
    return {
        "date": match.date, "dom": match.dom, "ext": match.ext,
        "buts_dom": match.buts_dom, "buts_ext": match.buts_ext,
        "p_modele_1": probas["1N2"]["1"],
        "p_modele_N": probas["1N2"]["N"],
        "p_modele_2": probas["1N2"]["2"],
        "p_modele_plus25": probas["totaux"]["plus_2.5"],
        "p_modele_btts": probas["btts"]["oui"],
        **{colonne: getattr(match, colonne, np.nan) for colonne in (
            "cote_1", "cote_N", "cote_2", "cote_1_max", "cote_N_max", "cote_2_max",
            "cote_plus25", "cote_moins25")},
    }


def predictions_walk_forward(
    matchs: pd.DataFrame,
    cible: dc.Cible = "buts",
    poids_xg: float = dc.POIDS_XG_DEFAUT,
    xi: float = dc.XI_DEFAUT,
    force_domicile_separee: bool = False,
    frequence_reentrainement: int = 1,
    taille_initiale: int | None = None,
    regularisation: float | None = None,
) -> pd.DataFrame:
    """Predictions hors echantillon, en n'utilisant jamais le futur.

    On entraine sur tout ce qui precede strictement la journee testee, on
    predit cette journee, puis on avance. Le modele est reestime toutes les
    `frequence_reentrainement` journees pour rester realiste sans exploser le
    temps de calcul.
    """
    matchs = matchs.dropna(subset=["date", "dom", "ext", "buts_dom", "buts_ext"])
    matchs = matchs.sort_values("date").reset_index(drop=True)
    if taille_initiale is None:
        taille_initiale = max(120, int(0.35 * len(matchs)))

    lignes: list[dict] = []
    modele: dc.ModeleDixonColes | None = None

    for numero, jour in enumerate(_dates_de_test(matchs, taille_initiale)):
        passe = matchs[matchs["date"] < jour]
        if len(passe) < 60:
            continue
        if modele is None or numero % max(1, frequence_reentrainement) == 0:
            try:
                modele = dc.entrainer(
                    passe, cible=cible, poids_xg=poids_xg, xi=xi,
                    force_domicile_separee=force_domicile_separee,
                    date_reference=pd.Timestamp(jour),
                    regularisation=regularisation)
            except ValueError:
                continue
        for match in matchs[matchs["date"] == jour].itertuples():
            if match.dom not in modele.attaque or match.ext not in modele.attaque:
                continue  # promu sans historique : on ne devine pas
            lignes.append(_ligne_prediction(match, modele.predire(match.dom, match.ext)))

    sortie = pd.DataFrame(lignes)
    if not sortie.empty:
        sortie["issue"] = resultat_1n2(sortie)
    return sortie


def _chemin_hyperparametres(championnat: str) -> "object":
    return config.DOSSIER_CALIBRATION / f"hyperparametres_{championnat.upper()}.json"


def charger_hyperparametres(championnat: str) -> dict | None:
    chemin = _chemin_hyperparametres(championnat)
    if chemin.exists():
        try:
            return json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _journal.warning("Fichier de calibration illisible, il sera regenere : %s", chemin)
    return None


def _balayer_grille(matchs: pd.DataFrame, cible: dc.Cible, grille_xi, grille_w,
                    frequence: int) -> list[dict]:
    """Une log-loss hors echantillon par combinaison (xi, w)."""
    resultats: list[dict] = []
    for xi in grille_xi:
        for w in grille_w:
            predictions = predictions_walk_forward(
                matchs, cible=cible, poids_xg=w, xi=xi,
                frequence_reentrainement=frequence)
            if predictions.empty:
                continue
            probas = predictions[["p_modele_1", "p_modele_N", "p_modele_2"]].to_numpy()
            issues = predictions["issue"].tolist()
            resultats.append({
                "xi": xi, "w": w,
                "log_loss": log_loss(probas, issues),
                "brier": brier(probas, issues),
                "nb_predictions": len(predictions),
            })
    return resultats


def optimiser_hyperparametres(
    matchs: pd.DataFrame,
    championnat: str = "",
    cible: dc.Cible = "buts",
    grille_xi: Iterable[float] = GRILLE_XI,
    grille_w: Iterable[float] = GRILLE_W,
    frequence_reentrainement: int = 5,
    utiliser_cache: bool = True,
    afficher: bool = True,
) -> dict:
    """Choisit xi (oubli du passe) et w (poids des xG) par validation temporelle.

    Critere : log-loss sur le 1N2, hors echantillon. Le resultat est mis en
    cache par championnat pour ne pas relancer ce balayage a chaque session.
    """
    if utiliser_cache and championnat:
        cache = charger_hyperparametres(championnat)
        if (cache and cache.get("nb_matchs") == len(matchs) and cache.get("cible") == cible
                and "regularisation" in cache):
            _journal.info("Hyperparametres repris du cache pour %s.", championnat)
            return cache

    grille_w = list(grille_w) if cible == "melange" else [dc.POIDS_XG_DEFAUT]
    grille_xi = list(grille_xi)
    # Un balayage a 30 combinaisons coute 5 fois plus cher qu'un balayage a 6 :
    # on espace les reentrainements pour rester dans les quelques minutes.
    if len(grille_xi) * len(grille_w) > 12:
        frequence_reentrainement = max(frequence_reentrainement, 10)

    resultats = _balayer_grille(matchs, cible, grille_xi, grille_w, frequence_reentrainement)
    if not resultats:
        raise ValueError(
            "Impossible de calibrer : pas assez de matchs pour une validation temporelle.\n"
            "  Charge au moins deux saisons completes."
        )

    tableau = pd.DataFrame(resultats).sort_values("log_loss").reset_index(drop=True)
    if afficher:
        print(_tableau_hyperparametres(tableau, cible))

    meilleur = tableau.iloc[0]
    regularisation, log_loss_retenue, grille_reg = _choisir_regularisation(
        matchs, cible, float(meilleur["xi"]), float(meilleur["w"]),
        frequence_reentrainement, float(meilleur["log_loss"]), afficher)
    resultat = {
        "championnat": championnat.upper(), "cible": cible,
        "xi": float(meilleur["xi"]), "w": float(meilleur["w"]),
        "regularisation": regularisation,
        "log_loss": log_loss_retenue, "nb_matchs": len(matchs),
        "grille": tableau.to_dict("records"),
        "grille_regularisation": grille_reg,
    }
    if championnat:
        config.creer_dossiers()
        _chemin_hyperparametres(championnat).write_text(
            json.dumps(resultat, indent=2), encoding="utf-8")
    return resultat


def _choisir_regularisation(matchs: pd.DataFrame, cible: dc.Cible, xi: float, w: float,
                            frequence: int, log_loss_defaut: float,
                            afficher: bool) -> tuple[float, float, list[dict]]:
    """Force du retrecissement L2, choisie a (xi, w) fixes sur la meme validation.

    Le balayage (xi, w) a ete fait avec dc.REGULARISATION_DEFAUT : on reutilise
    son score pour cette valeur plutot que de la recalculer.
    """
    grille = [{"regularisation": dc.REGULARISATION_DEFAUT, "log_loss": log_loss_defaut}]
    for force in GRILLE_REGULARISATION:
        if force == dc.REGULARISATION_DEFAUT:
            continue
        predictions = predictions_walk_forward(
            matchs, cible=cible, poids_xg=w, xi=xi,
            frequence_reentrainement=frequence, regularisation=force)
        if predictions.empty:
            continue
        probas = predictions[["p_modele_1", "p_modele_N", "p_modele_2"]].to_numpy()
        grille.append({"regularisation": force,
                       "log_loss": log_loss(probas, predictions["issue"].tolist())})
    grille.sort(key=lambda ligne: ligne["log_loss"])
    if afficher:
        print("Retrecissement L2 des forces (xi et w fixes), tri par log-loss :")
        for ligne in grille:
            print(f"  {ligne['regularisation']:>5.1f}  {ligne['log_loss']:.4f}")
    return float(grille[0]["regularisation"]), float(grille[0]["log_loss"]), grille


def _tableau_hyperparametres(tableau: pd.DataFrame, cible: str) -> str:
    lignes = [f"Balayage des hyperparametres (cible = {cible}), tri par log-loss croissante :",
              f"  {'xi':>8}  {'w':>5}  {'log-loss':>9}  {'Brier':>7}  {'n':>5}"]
    for ligne in tableau.itertuples():
        lignes.append(f"  {ligne.xi:8.4f}  {ligne.w:5.2f}  {ligne.log_loss:9.4f}  "
                      f"{ligne.brier:7.4f}  {ligne.nb_predictions:5d}")
    return "\n".join(lignes)


# ==========================================================================
# T08 — de-vig : extraire la probabilite vraie du marche
# ==========================================================================

def surcote(cotes: Sequence[float]) -> float:
    """Marge du bookmaker : somme des inverses des cotes, moins 1."""
    valeurs = np.asarray(cotes, dtype=float)
    return float(np.sum(1.0 / valeurs) - 1.0)


def devig_multiplicatif(cotes: Sequence[float]) -> np.ndarray:
    """Normalisation proportionnelle. Simple, mais laisse trop de probabilite
    aux outsiders : la marge n'est pas repartie uniformement en realite."""
    inverses = 1.0 / np.asarray(cotes, dtype=float)
    return inverses / inverses.sum()


def devig_puissance(cotes: Sequence[float]) -> np.ndarray:
    """Cherche k tel que somme((1/c_i)^k) = 1. Bon compromis general."""
    inverses = 1.0 / np.asarray(cotes, dtype=float)
    if abs(inverses.sum() - 1.0) < 1e-12:
        return inverses

    def ecart(k: float) -> float:
        return float(np.sum(inverses ** k) - 1.0)

    borne_basse, borne_haute = 0.2, 8.0
    if ecart(borne_basse) * ecart(borne_haute) > 0:
        return devig_multiplicatif(cotes)
    k = brentq(ecart, borne_basse, borne_haute, xtol=1e-12)
    probas = inverses ** k
    return probas / probas.sum()


def devig_shin(cotes: Sequence[float]) -> np.ndarray:
    """Modele de Shin : la marge vient de parieurs mieux informes.

    On cherche la proportion z de parieurs informes telle que les probabilites
    corrigees somment a 1 :
        p_i = (sqrt(z^2 + 4(1-z) * pi_i^2 / S) - z) / (2(1-z))
    avec pi_i = 1/c_i et S = somme des pi_i. Resolution par bissection.
    C'est la methode la plus juste sur les favoris extremes.
    """
    inverses = 1.0 / np.asarray(cotes, dtype=float)
    somme = float(inverses.sum())
    if somme <= 1.0 + 1e-12:
        return devig_multiplicatif(cotes)

    def probas_pour(z: float) -> np.ndarray:
        racine = np.sqrt(z * z + 4 * (1 - z) * inverses ** 2 / somme)
        return (racine - z) / (2 * (1 - z))

    def ecart(z: float) -> float:
        return float(probas_pour(z).sum() - 1.0)

    borne_haute = 0.6
    if ecart(0.0) * ecart(borne_haute) > 0:
        raise ArithmeticError("bissection de Shin sans changement de signe")
    z = brentq(ecart, 0.0, borne_haute, xtol=1e-12)
    probas = probas_pour(z)
    return probas / probas.sum()


def deviguer(cotes: Sequence[float], methode: Methode = "shin") -> np.ndarray:
    """Probabilites vraies du marche, marge retiree.

    Par defaut Shin, avec repli automatique sur la methode puissance si la
    resolution numerique ne converge pas.
    """
    valeurs = np.asarray(cotes, dtype=float)
    if valeurs.size == 0 or np.any(~np.isfinite(valeurs)) or np.any(valeurs <= 1.0):
        raise ValueError(
            f"Cotes invalides : {list(cotes)}. Une cote decimale est toujours > 1.0 "
            "et ne doit etre ni vide ni manquante."
        )
    if methode == "multiplicatif":
        return devig_multiplicatif(valeurs)
    if methode == "puissance":
        return devig_puissance(valeurs)
    try:
        return devig_shin(valeurs)
    except (ArithmeticError, ValueError, RuntimeError) as erreur:
        _journal.debug("Shin n'a pas converge (%s), repli sur la methode puissance.", erreur)
        return devig_puissance(valeurs)


def consensus(
    cotes: pd.DataFrame,
    methode: Methode = "shin",
    colonnes_cle: Sequence[str] = ("date", "dom", "ext", "marche"),
) -> pd.DataFrame:
    """Mediane inter-bookmakers des probabilites de-viggees.

    C'est l'estimateur de reference du projet : la mediane des books bat en
    pratique n'importe quel modele public. On calcule le de-vig bookmaker par
    bookmaker (chacun a sa propre marge) avant de prendre la mediane.
    """
    if cotes.empty:
        return pd.DataFrame(columns=[*colonnes_cle, "selection", "p_marche",
                                     "nb_bookmakers", "meilleure_cote", "bookmaker_meilleure"])

    lignes: list[dict] = []
    cles = [c for c in colonnes_cle if c in cotes.columns]
    for cle, groupe in cotes.groupby(list(cles) + ["bookmaker"], dropna=False):
        groupe = groupe.dropna(subset=["cote"])
        if len(groupe) < 2:
            continue  # un marche a une seule selection n'a pas de marge calculable
        try:
            probas = deviguer(groupe["cote"].to_numpy(), methode)
        except ValueError:
            continue
        for selection, proba in zip(groupe["selection"], probas):
            ligne = dict(zip(list(cles) + ["bookmaker"], cle))
            ligne.update({"selection": selection, "p_devig": float(proba)})
            lignes.append(ligne)

    if not lignes:
        return pd.DataFrame(columns=[*cles, "selection", "p_marche",
                                     "nb_bookmakers", "meilleure_cote", "bookmaker_meilleure"])

    devigge = pd.DataFrame(lignes)
    agrege = (devigge.groupby([*cles, "selection"], dropna=False)
              .agg(p_marche=("p_devig", "median"), nb_bookmakers=("bookmaker", "nunique"))
              .reset_index())

    meilleures = _meilleures_cotes(cotes, cles)
    fusion = agrege.merge(meilleures, on=[*cles, "selection"], how="left")
    return _renormaliser_par_marche(fusion, cles)


def _meilleures_cotes(cotes: pd.DataFrame, cles: Sequence[str]) -> pd.DataFrame:
    indices = cotes.groupby([*cles, "selection"], dropna=False)["cote"].idxmax()
    meilleures = cotes.loc[indices, [*cles, "selection", "cote", "bookmaker"]]
    return meilleures.rename(columns={"cote": "meilleure_cote",
                                      "bookmaker": "bookmaker_meilleure"})


def _renormaliser_par_marche(tableau: pd.DataFrame, cles: Sequence[str]) -> pd.DataFrame:
    """La mediane de probabilites ne somme pas exactement a 1 : on renormalise."""
    somme = tableau.groupby(list(cles), dropna=False)["p_marche"].transform("sum")
    tableau = tableau.copy()
    tableau["p_marche"] = tableau["p_marche"] / somme.replace(0, np.nan)
    return tableau


# ==========================================================================
# T09 — melange modele + marche
# ==========================================================================

def melange_geometrique(p_modele: np.ndarray, p_marche: np.ndarray, a: float) -> np.ndarray:
    """normaliser(p_modele^a * p_marche^(1-a)).

    Le melange geometrique est plus stable que la moyenne arithmetique sur des
    probabilites : il ne cree pas de masse la ou les deux sources sont d'accord
    pour dire "improbable".
    """
    modele = np.clip(np.asarray(p_modele, dtype=float), 1e-12, 1.0)
    marche = np.clip(np.asarray(p_marche, dtype=float), 1e-12, 1.0)
    melange = modele ** a * marche ** (1.0 - a)
    somme = melange.sum(axis=-1, keepdims=True)
    return melange / somme


@dataclass
class Melange:
    """Resultat d'un melange : les trois probabilites, jamais une seule."""

    p_modele: np.ndarray
    p_marche: np.ndarray
    p_finale: np.ndarray
    a: float
    ecart_max: float

    def en_dict(self, ordre: Sequence[str] = ("1", "N", "2")) -> dict[str, dict[str, float]]:
        return {
            "modele": dict(zip(ordre, self.p_modele.tolist())),
            "marche": dict(zip(ordre, self.p_marche.tolist())),
            "finale": dict(zip(ordre, self.p_finale.tolist())),
        }


def combiner(p_modele: Sequence[float], p_marche: Sequence[float], a: float = 0.3) -> Melange:
    """Melange modele/marche pour un match, avec les trois probabilites en sortie.

    L'ecart entre modele et marche est justement l'information recherchee :
    il est donc calcule et conserve, pas efface par le melange.
    """
    modele = np.asarray(p_modele, dtype=float)
    marche = np.asarray(p_marche, dtype=float)
    finale = melange_geometrique(modele, marche, a)
    return Melange(modele, marche, finale, a, float(np.max(np.abs(modele - marche))))


def calibrer_poids_melange(
    predictions: pd.DataFrame,
    grille_a: Iterable[float] = GRILLE_A,
    afficher: bool = True,
) -> dict:
    """Choisit "a" par backtest : on ne le fixe pas a la main.

    `predictions` doit contenir p_modele_* (hors echantillon) et les cotes de
    marche cote_1/cote_N/cote_2. Renvoie le meilleur a, les log-loss de chaque
    source, et dit clairement si le melange ne bat pas le marche seul.
    """
    utilisables = predictions.dropna(subset=["cote_1", "cote_N", "cote_2"]).copy()
    if utilisables.empty:
        raise ValueError(
            "Aucune cote historique disponible dans ces predictions : "
            "impossible de calibrer le poids du melange."
        )

    p_modele = utilisables[["p_modele_1", "p_modele_N", "p_modele_2"]].to_numpy()
    p_marche = np.array([
        deviguer([ligne.cote_1, ligne.cote_N, ligne.cote_2])
        for ligne in utilisables.itertuples()
    ])
    issues = utilisables["issue"].tolist()

    perte_modele = log_loss(p_modele, issues)
    perte_marche = log_loss(p_marche, issues)

    scores = []
    for a in grille_a:
        melange = melange_geometrique(p_modele, p_marche, a)
        scores.append({"a": float(a), "log_loss": log_loss(melange, issues)})
    tableau = pd.DataFrame(scores).sort_values("log_loss").reset_index(drop=True)
    meilleur_a = float(tableau.iloc[0]["a"])
    perte_melange = float(tableau.iloc[0]["log_loss"])

    verdict = _verdict_melange(perte_modele, perte_marche, perte_melange, meilleur_a)
    if afficher:
        print(verdict)

    return {
        "a": meilleur_a,
        "log_loss_modele": perte_modele,
        "log_loss_marche": perte_marche,
        "log_loss_melange": perte_melange,
        # Meme tolerance que le verdict affiche : a=0 rend le melange EGAL au
        # marche, et un ecart de 1e-16 ne doit pas se lire comme une victoire.
        "melange_bat_marche": perte_melange < perte_marche - 1e-6,
        "melange_bat_modele": perte_melange < perte_modele - 1e-6,
        "nb_matchs": len(utilisables),
        "grille": tableau.to_dict("records"),
        "verdict": verdict,
    }


def _chemin_poids_melange(championnat: str) -> "object":
    return config.DOSSIER_CALIBRATION / f"poids_melange_{championnat.upper()}.json"


def enregistrer_poids_melange(championnat: str, resultat: dict) -> "object":
    """Rend persistant le `a` mesure par backtest.

    Sans cela, `calibrer_poids_melange` calculait le bon poids, l'affichait, et
    tout le reste du projet continuait d'utiliser 0.30 code en dur — donc de
    donner au modele une confiance que le backtest lui refusait.
    """
    config.creer_dossiers()
    destination = _chemin_poids_melange(championnat)
    charge = {cle: resultat[cle] for cle in (
        "a", "log_loss_modele", "log_loss_marche", "log_loss_melange",
        "melange_bat_marche", "nb_matchs")}
    charge["championnat"] = championnat.upper()
    charge["calcule_le"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    destination.write_text(json.dumps(charge, indent=2), encoding="utf-8")
    return destination


def charger_poids_melange(championnat: str) -> dict | None:
    """Poids mesure pour ce championnat, ou None s'il n'a jamais ete calibre."""
    chemin = _chemin_poids_melange(championnat)
    if not chemin.exists():
        return None
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _journal.warning("Poids de melange illisible, il sera regenere : %s", chemin)
        return None


def poids_melange(championnat: str, defaut: float | None = None) -> float:
    """Le `a` a utiliser : celui mesure si on l'a, sinon la valeur par defaut.

    Un `a` mesure a 0 n'est pas une anomalie a corriger : il signifie que sur
    cet historique le modele n'ajoute rien au marche. Le respecter fait
    disparaitre la plupart des paris proposes, et c'est le resultat correct.
    """
    defaut = config.POIDS_MELANGE_DEFAUT if defaut is None else defaut
    mesure = charger_poids_melange(championnat)
    return float(mesure["a"]) if mesure and "a" in mesure else float(defaut)


def _verdict_melange(modele: float, marche: float, melange: float, a: float) -> str:
    lignes = [
        "Calibration du melange modele/marche (log-loss, plus bas = mieux) :",
        f"  modele seul  : {modele:.4f}",
        f"  marche seul  : {marche:.4f}",
        f"  melange a={a:.2f} : {melange:.4f}",
    ]
    if melange < marche - 1e-6 and melange < modele - 1e-6:
        lignes.append("  -> le melange bat les deux sources prises seules.")
    elif melange < marche - 1e-6:
        lignes.append("  -> le melange bat le marche, mais pas le modele seul.")
    else:
        lignes.append(
            "  -> ATTENTION : le melange ne bat PAS le marche seul sur cet historique. "
            "C'est frequent et ce n'est pas masque ici : dans ce cas, le marche reste "
            "la meilleure estimation disponible et le modele ne sert qu'a reperer les ecarts."
        )
    if a <= 0.05:
        lignes.append("  -> a proche de 0 : le backtest dit que ton modele n'apporte rien "
                      "par rapport au marche sur cet echantillon.")
    return "\n".join(lignes)


# --------------------------------------------------------------------------
# Calibration isotonique (optionnelle, en sortie de melange)
# --------------------------------------------------------------------------

def _pav(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pool Adjacent Violators : regression isotonique croissante, sans sklearn."""
    ordre = np.argsort(x, kind="mergesort")
    xs, ys = x[ordre], y[ordre].astype(float)
    valeurs = list(ys)
    poids = [1.0] * len(ys)
    bornes = list(range(len(ys)))
    i = 0
    while i < len(valeurs) - 1:
        if valeurs[i] <= valeurs[i + 1]:
            i += 1
            continue
        total = poids[i] + poids[i + 1]
        valeurs[i] = (valeurs[i] * poids[i] + valeurs[i + 1] * poids[i + 1]) / total
        poids[i] = total
        del valeurs[i + 1], poids[i + 1], bornes[i + 1]
        i = max(i - 1, 0)
    return xs[bornes], np.asarray(valeurs)


@dataclass
class CalibrateurIsotonique:
    """Corrige la sur-confiance residuelle, appris sur l'historique."""

    seuils: np.ndarray
    valeurs: np.ndarray

    def appliquer(self, probas: np.ndarray) -> np.ndarray:
        return np.interp(np.asarray(probas, dtype=float), self.seuils, self.valeurs)


def entrainer_isotonique(probas: Sequence[float], issues: Sequence[int]) -> CalibrateurIsotonique:
    """Apprend la correction sur des couples (probabilite annoncee, 0/1 realise)."""
    x = np.asarray(probas, dtype=float)
    y = np.asarray(issues, dtype=float)
    if len(x) < 30:
        raise ValueError("Au moins 30 observations sont necessaires pour une calibration isotonique.")
    seuils, valeurs = _pav(x, y)
    return CalibrateurIsotonique(seuils, np.clip(valeurs, 0.0, 1.0))


def calibrer_multiclasse(
    probas: np.ndarray, issues: Sequence[str], ordre: Sequence[str] = ("1", "N", "2")
) -> list[CalibrateurIsotonique]:
    """Un calibrateur un-contre-tous par issue ; la renormalisation suit."""
    calibrateurs = []
    for i, issue in enumerate(ordre):
        cible = [1 if str(reel) == issue else 0 for reel in issues]
        calibrateurs.append(entrainer_isotonique(probas[:, i], cible))
    return calibrateurs


def appliquer_calibration(
    probas: np.ndarray, calibrateurs: Sequence[CalibrateurIsotonique]
) -> np.ndarray:
    corrigees = np.column_stack([c.appliquer(probas[:, i]) for i, c in enumerate(calibrateurs)])
    corrigees = np.clip(corrigees, 1e-6, 1.0)
    return corrigees / corrigees.sum(axis=1, keepdims=True)
