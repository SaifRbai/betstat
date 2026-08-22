"""Backtest walk-forward, avec les metriques qui empechent de se mentir.

Trois exigences structurent ce module :
  1. aucune fuite du futur : on entraine sur ce qui precede strictement la
     journee testee, jamais sur elle ni sur ce qui la suit ;
  2. un ROI sans intervalle de confiance ne veut rien dire : il est donc
     toujours accompagne d'un intervalle bootstrap a 95 %, et le rapport ecrit
     noir sur blanc quand cet intervalle contient zero ;
  3. comparaison systematique a deux baselines de marche. Si le systeme ne les
     bat pas, le rapport le dit en toutes lettres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import config
from modele import calibration
from modele import dixon_coles as dc
from paris import valeur as valeur_module

_journal = logging.getLogger("foot_edge.backtest")

NB_BOOTSTRAP = 1000
NB_BUCKETS_CALIBRATION = 10


@dataclass
class ResultatBacktest:
    """Tout ce que produit un backtest, y compris ce qui derange."""

    predictions: pd.DataFrame
    paris: pd.DataFrame
    metriques: dict = field(default_factory=dict)
    baselines: dict = field(default_factory=dict)
    calibration_1n2: pd.DataFrame = field(default_factory=pd.DataFrame)
    graphiques: dict[str, str] = field(default_factory=dict)
    rapport: str = ""


# --------------------------------------------------------------------------
# Metriques de probabilite
# --------------------------------------------------------------------------

def _probabilites_marche(predictions: pd.DataFrame, methode: str = "shin") -> np.ndarray:
    """Probabilites 1N2 de-viggees, ligne par ligne (NaN si cotes absentes)."""
    sortie = np.full((len(predictions), 3), np.nan)
    for i, ligne in enumerate(predictions.itertuples()):
        cotes = (ligne.cote_1, ligne.cote_N, ligne.cote_2)
        if any(pd.isna(c) or c is None or c <= 1.0 for c in cotes):
            continue
        sortie[i] = calibration.deviguer(cotes, methode)
    return sortie


def metriques_probabilistes(predictions: pd.DataFrame, colonnes: Sequence[str],
                            issues: Sequence[str]) -> dict:
    probas = predictions[list(colonnes)].to_numpy()
    return {
        "log_loss": calibration.log_loss(probas, issues),
        "brier": calibration.brier(probas, issues),
        "precision": float(np.mean(
            np.array(["1", "N", "2"])[probas.argmax(axis=1)] == np.array(list(issues)))),
        "n": len(predictions),
    }


def _metriques_binaires(proba: np.ndarray, realise: np.ndarray) -> dict:
    proba = np.clip(np.asarray(proba, dtype=float), 1e-12, 1 - 1e-12)
    realise = np.asarray(realise, dtype=float)
    valides = ~np.isnan(proba) & ~np.isnan(realise)
    proba, realise = proba[valides], realise[valides]
    if proba.size == 0:
        return {"log_loss": float("nan"), "brier": float("nan"), "n": 0}
    return {
        "log_loss": float(-np.mean(realise * np.log(proba) + (1 - realise) * np.log(1 - proba))),
        "brier": float(np.mean((proba - realise) ** 2)),
        "precision": float(np.mean((proba > 0.5) == (realise > 0.5))),
        "n": int(proba.size),
    }


def courbe_calibration(probas: np.ndarray, realise: np.ndarray,
                       nb_buckets: int = NB_BUCKETS_CALIBRATION) -> pd.DataFrame:
    """Parmi les evenements annonces a 60 %, combien se sont produits ?"""
    probas = np.asarray(probas, dtype=float).ravel()
    realise = np.asarray(realise, dtype=float).ravel()
    valides = ~np.isnan(probas) & ~np.isnan(realise)
    probas, realise = probas[valides], realise[valides]
    bords = np.linspace(0.0, 1.0, nb_buckets + 1)
    rangs = np.clip(np.digitize(probas, bords[1:-1]), 0, nb_buckets - 1)
    lignes = []
    for bucket in range(nb_buckets):
        masque = rangs == bucket
        if not masque.any():
            continue
        lignes.append({
            "bucket": f"{bords[bucket]:.0%}-{bords[bucket + 1]:.0%}",
            "annonce_moyen": float(probas[masque].mean()),
            "realise": float(realise[masque].mean()),
            "effectif": int(masque.sum()),
        })
    return pd.DataFrame(lignes)


# --------------------------------------------------------------------------
# Simulation de paris
# --------------------------------------------------------------------------

def _cote_disponible(ligne, selection: str) -> float:
    """Meilleure cote historique disponible pour une selection 1N2."""
    meilleure = getattr(ligne, f"cote_{selection}_max", np.nan)
    moyenne = getattr(ligne, f"cote_{selection}", np.nan)
    for valeur in (meilleure, moyenne):
        if valeur is not None and not pd.isna(valeur) and valeur > 1.0:
            return float(valeur)
    return float("nan")


def simuler_paris(
    predictions: pd.DataFrame,
    p_finales: np.ndarray,
    p_marche: np.ndarray,
    seuil_esperance: float = config.SEUIL_ESPERANCE_DEFAUT,
    bankroll_initiale: float = 1000.0,
    mise_fixe: float | None = None,
) -> pd.DataFrame:
    """Rejoue les paris que le systeme aurait pris, dans l'ordre chronologique.

    Les conditions "au moins 3 bookmakers" et "cote au-dessus de la mediane"
    de T11 ne sont pas verifiables retroactivement sur les CSV historiques
    (ils ne contiennent pas le detail par bookmaker) : le backtest applique
    donc les deux conditions restantes, ce qui le rend plutot pessimiste que
    trop beau.
    """
    bankroll = bankroll_initiale
    lignes: list[dict] = []
    for i, ligne in enumerate(predictions.itertuples()):
        for j, selection in enumerate(("1", "N", "2")):
            proba = p_finales[i, j]
            if np.isnan(proba):
                continue
            cote = _cote_disponible(ligne, selection)
            if np.isnan(cote):
                continue
            esperance = proba * cote - 1.0
            if esperance <= seuil_esperance:
                continue
            ecart = abs(proba - p_marche[i, j]) if not np.isnan(p_marche[i, j]) else 0.0
            if ecart > config.SEUIL_DIVERGENCE_ALERTE:
                continue  # signal d'alerte, pas une opportunite
            if mise_fixe is not None:
                mise = mise_fixe
            else:
                _, mise = valeur_module.mise_conseillee(proba, cote, bankroll)
            if mise <= 0:
                continue
            gagne = ligne.issue == selection
            gain = mise * (cote - 1.0) if gagne else -mise
            bankroll += gain
            lignes.append({
                "date": ligne.date, "dom": ligne.dom, "ext": ligne.ext,
                "selection": selection, "cote": cote, "proba": proba,
                "esperance": esperance, "mise": mise, "gagne": gagne,
                "gain": gain, "bankroll": bankroll,
            })
    return pd.DataFrame(lignes)


def intervalle_bootstrap_roi(gains: np.ndarray, mises: np.ndarray,
                             nb_tirages: int = NB_BOOTSTRAP,
                             graine: int = 0) -> tuple[float, float, float]:
    """(ROI, borne basse 95 %, borne haute 95 %) par re-echantillonnage."""
    if len(gains) == 0 or mises.sum() == 0:
        return float("nan"), float("nan"), float("nan")
    roi = float(gains.sum() / mises.sum())
    generateur = np.random.default_rng(graine)
    indices = generateur.integers(0, len(gains), size=(nb_tirages, len(gains)))
    tirages_gains = gains[indices].sum(axis=1)
    tirages_mises = mises[indices].sum(axis=1)
    rois = tirages_gains / np.where(tirages_mises == 0, np.nan, tirages_mises)
    return roi, float(np.nanpercentile(rois, 2.5)), float(np.nanpercentile(rois, 97.5))


def drawdown_maximum(bankroll: Sequence[float], depart: float) -> float:
    """Plus forte baisse depuis un sommet, en fraction du sommet."""
    serie = np.concatenate([[depart], np.asarray(bankroll, dtype=float)])
    sommets = np.maximum.accumulate(serie)
    return float(np.max((sommets - serie) / sommets))


# --------------------------------------------------------------------------
# Baselines de marche
# --------------------------------------------------------------------------

def baseline_favori_marche(predictions: pd.DataFrame, mise: float = 10.0) -> dict:
    """Parier systematiquement le favori du marche, a mise constante."""
    gains, mises = [], []
    for ligne in predictions.itertuples():
        cotes = {s: _cote_disponible(ligne, s) for s in ("1", "N", "2")}
        cotes = {s: c for s, c in cotes.items() if not np.isnan(c)}
        if not cotes:
            continue
        favori = min(cotes, key=cotes.get)
        gains.append(mise * (cotes[favori] - 1.0) if ligne.issue == favori else -mise)
        mises.append(mise)
    if not gains:
        return {"n": 0, "roi": float("nan")}
    roi, bas, haut = intervalle_bootstrap_roi(np.array(gains), np.array(mises))
    return {"n": len(gains), "roi": roi, "ic_bas": bas, "ic_haut": haut,
            "pnl": float(np.sum(gains))}


def baseline_marche_devigge(predictions: pd.DataFrame, p_marche: np.ndarray) -> dict:
    """Qualite pure des probabilites du marche de-viggees (T08)."""
    valides = ~np.isnan(p_marche).any(axis=1)
    if not valides.any():
        return {"n": 0}
    issues = predictions.loc[valides, "issue"].tolist()
    probas = p_marche[valides]
    return {
        "n": int(valides.sum()),
        "log_loss": calibration.log_loss(probas, issues),
        "brier": calibration.brier(probas, issues),
        "precision": float(np.mean(
            np.array(["1", "N", "2"])[probas.argmax(axis=1)] == np.array(issues))),
    }


# --------------------------------------------------------------------------
# Graphiques
# --------------------------------------------------------------------------

def _tracer(fonction, chemin: Path) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        _journal.warning("matplotlib absent : graphiques non generes.")
        return None
    figure, axes = plt.subplots(figsize=(7, 4.5))
    try:
        fonction(axes)
        figure.tight_layout()
        config.creer_dossiers()
        figure.savefig(chemin, dpi=110)
    except Exception as erreur:
        # Un graphique est un confort ; le rapport chiffre, lui, a coute
        # plusieurs minutes de calcul et ne doit pas etre perdu avec lui.
        # Vu en vrai : matplotlib et tkinter dans le meme process peuvent se
        # disputer FreeType et faire echouer le rendu des glyphes.
        _journal.warning("Graphique %s non genere (%s : %s).",
                         chemin.name, type(erreur).__name__, erreur)
        return None
    finally:
        plt.close(figure)
    return str(chemin)


def tracer_calibration(courbe: pd.DataFrame, chemin: Path, titre: str = "") -> str | None:
    def dessin(axes) -> None:
        axes.plot([0, 1], [0, 1], "--", color="grey", label="calibration parfaite")
        axes.plot(courbe["annonce_moyen"], courbe["realise"], "o-", label="systeme")
        for ligne in courbe.itertuples():
            axes.annotate(str(ligne.effectif),
                          (ligne.annonce_moyen, ligne.realise),
                          textcoords="offset points", xytext=(0, 6), fontsize=7)
        axes.set_xlabel("probabilite annoncee")
        axes.set_ylabel("frequence reellement observee")
        axes.set_title(f"Courbe de calibration 1N2 {titre}".strip())
        axes.legend()
        axes.grid(alpha=0.3)

    return _tracer(dessin, chemin)


def tracer_bankroll(paris: pd.DataFrame, depart: float, chemin: Path,
                    titre: str = "") -> str | None:
    def dessin(axes) -> None:
        serie = [depart, *paris["bankroll"].tolist()]
        axes.plot(range(len(serie)), serie)
        axes.axhline(depart, linestyle="--", color="grey")
        axes.set_xlabel("nombre de paris")
        axes.set_ylabel("bankroll")
        axes.set_title(f"Evolution de la bankroll {titre}".strip())
        axes.grid(alpha=0.3)

    return _tracer(dessin, chemin)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _issues_binaires(predictions: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    total = predictions["buts_dom"] + predictions["buts_ext"]
    plus25 = (total > 2.5).to_numpy(dtype=float)
    btts = ((predictions["buts_dom"] > 0) & (predictions["buts_ext"] > 0)).to_numpy(dtype=float)
    return plus25, btts


def _probabilites_finales(p_modele: np.ndarray, p_marche: np.ndarray,
                          a_melange: float) -> np.ndarray:
    """Melange geometrique, en retombant sur le modele quand les cotes manquent."""
    return np.array([
        calibration.melange_geometrique(p_modele[i], p_marche[i], a_melange)
        if not np.isnan(p_marche[i]).any() else p_modele[i]
        for i in range(len(p_modele))
    ])


def _metriques_qualite(predictions: pd.DataFrame, p_finales: np.ndarray,
                       issues: Sequence[str]) -> dict:
    """Log-loss et Brier du modele seul, du melange, et des marches derives."""
    plus25, btts = _issues_binaires(predictions)
    return {
        "1N2_modele": metriques_probabilistes(
            predictions, ("p_modele_1", "p_modele_N", "p_modele_2"), issues),
        "1N2_final": {
            "log_loss": calibration.log_loss(p_finales, issues),
            "brier": calibration.brier(p_finales, issues),
            "precision": float(np.mean(
                np.array(["1", "N", "2"])[p_finales.argmax(axis=1)] == np.array(issues))),
            "n": len(predictions),
        },
        "plus_2.5": _metriques_binaires(predictions["p_modele_plus25"].to_numpy(), plus25),
        "btts": _metriques_binaires(predictions["p_modele_btts"].to_numpy(), btts),
    }


def _produire_graphiques(courbe: pd.DataFrame, paris: pd.DataFrame,
                         bankroll_initiale: float, suffixe: str) -> dict[str, str]:
    graphes: dict[str, str] = {}
    if not courbe.empty:
        trace = tracer_calibration(courbe, config.DOSSIER_RAPPORTS / f"calibration_{suffixe}.png",
                                   suffixe)
        if trace:
            graphes["calibration"] = trace
    if not paris.empty:
        trace = tracer_bankroll(paris, bankroll_initiale,
                                config.DOSSIER_RAPPORTS / f"bankroll_{suffixe}.png", suffixe)
        if trace:
            graphes["bankroll"] = trace
    return graphes


def backtester(
    matchs: pd.DataFrame,
    championnat: str = "",
    cible: dc.Cible = "buts",
    xi: float = dc.XI_DEFAUT,
    poids_xg: float = dc.POIDS_XG_DEFAUT,
    a_melange: float | None = None,
    seuil_esperance: float = config.SEUIL_ESPERANCE_DEFAUT,
    bankroll_initiale: float = 1000.0,
    frequence_reentrainement: int = 1,
    graphiques: bool = True,
) -> ResultatBacktest:
    """Backtest complet, du walk-forward jusqu'aux graphiques."""
    predictions = calibration.predictions_walk_forward(
        matchs, cible=cible, poids_xg=poids_xg, xi=xi,
        frequence_reentrainement=frequence_reentrainement)
    if predictions.empty:
        raise ValueError(
            "Backtest impossible : aucune prediction hors echantillon.\n"
            "  Il faut au moins deux saisons de donnees pour amorcer le walk-forward."
        )

    p_modele = predictions[["p_modele_1", "p_modele_N", "p_modele_2"]].to_numpy()
    p_marche = _probabilites_marche(predictions)

    calibrage = None
    if a_melange is None:
        calibrage = calibration.calibrer_poids_melange(predictions, afficher=False)
        a_melange = calibrage["a"]

    p_finales = _probabilites_finales(p_modele, p_marche, a_melange)
    issues = predictions["issue"].tolist()

    metriques = _metriques_qualite(predictions, p_finales, issues)
    metriques.update({"a_melange": a_melange, "cible": cible, "xi": xi, "poids_xg": poids_xg})

    paris = simuler_paris(predictions, p_finales, p_marche,
                          seuil_esperance=seuil_esperance,
                          bankroll_initiale=bankroll_initiale)
    metriques["paris"] = _metriques_paris(paris, bankroll_initiale)

    baselines = {
        "favori_marche": baseline_favori_marche(predictions),
        "marche_devigge": baseline_marche_devigge(predictions, p_marche),
    }
    courbe = courbe_calibration(
        p_finales.ravel(),
        np.column_stack([(np.array(issues) == s).astype(float)
                         for s in ("1", "N", "2")]).ravel())
    graphes = _produire_graphiques(courbe, paris, bankroll_initiale,
                                   championnat or "backtest") if graphiques else {}

    resultat = ResultatBacktest(
        predictions=predictions, paris=paris, metriques=metriques,
        baselines=baselines, calibration_1n2=courbe, graphiques=graphes)
    resultat.rapport = redaction_rapport(resultat, championnat, calibrage)
    return resultat


def _metriques_paris(paris: pd.DataFrame, bankroll_initiale: float) -> dict:
    if paris.empty:
        return {"n": 0, "message": "Aucun pari declenche : le seuil d'esperance n'a "
                                   "jamais ete atteint sur cette periode."}
    gains = paris["gain"].to_numpy(dtype=float)
    mises = paris["mise"].to_numpy(dtype=float)
    roi, bas, haut = intervalle_bootstrap_roi(gains, mises)
    return {
        "n": len(paris),
        "mise_totale": float(mises.sum()),
        "pnl": float(gains.sum()),
        "roi": roi, "ic_bas": bas, "ic_haut": haut,
        "ic_contient_zero": bool(bas <= 0.0 <= haut),
        "taux_reussite": float(paris["gagne"].mean()),
        "cote_moyenne": float(paris["cote"].mean()),
        "bankroll_finale": float(paris["bankroll"].iloc[-1]),
        "drawdown_max": drawdown_maximum(paris["bankroll"].tolist(), bankroll_initiale),
    }


def redaction_rapport(resultat: ResultatBacktest, championnat: str = "",
                      calibrage: dict | None = None) -> str:
    """Rapport texte, ecrit pour ne rien maquiller."""
    m = resultat.metriques
    b = resultat.baselines
    lignes = [
        "=" * 74,
        f"BACKTEST {championnat or ''} — cible={m['cible']} xi={m['xi']} "
        f"a_melange={m['a_melange']:.2f}",
        "=" * 74,
        f"Predictions hors echantillon : {m['1N2_final']['n']}",
        "",
        "1. QUALITE DES PROBABILITES (log-loss : plus bas = mieux)",
        f"   modele seul        log-loss {m['1N2_modele']['log_loss']:.4f}  "
        f"Brier {m['1N2_modele']['brier']:.4f}",
        f"   modele + marche    log-loss {m['1N2_final']['log_loss']:.4f}  "
        f"Brier {m['1N2_final']['brier']:.4f}",
    ]
    if b["marche_devigge"].get("n"):
        lignes.append(
            f"   marche de-vigge    log-loss {b['marche_devigge']['log_loss']:.4f}  "
            f"Brier {b['marche_devigge']['brier']:.4f}   <= BASELINE")
    lignes += [
        f"   precision brute du melange : {m['1N2_final']['precision']:.1%} "
        "(indicateur secondaire : il ignore la confiance)",
        "",
        "2. AUTRES MARCHES",
        f"   plus de 2.5 buts   log-loss {m['plus_2.5']['log_loss']:.4f}  "
        f"Brier {m['plus_2.5']['brier']:.4f}  (n={m['plus_2.5']['n']})",
        f"   BTTS               log-loss {m['btts']['log_loss']:.4f}  "
        f"Brier {m['btts']['brier']:.4f}  (n={m['btts']['n']})",
        "",
        "3. SIMULATION DE PARIS",
    ]
    lignes += _bloc_paris(m["paris"])
    lignes += ["", "4. COMPARAISON AUX BASELINES DE MARCHE"]
    lignes += _bloc_baselines(m, b)
    if calibrage:
        lignes += ["", "5. CALIBRATION DU MELANGE", calibrage["verdict"]]
    if resultat.graphiques:
        lignes += ["", "GRAPHIQUES"] + [f"   {k} : {v}" for k, v in resultat.graphiques.items()]
    lignes += ["", config.AVERTISSEMENT_LEGAL, "=" * 74]
    return "\n".join(lignes)


def _bloc_paris(paris: dict) -> list[str]:
    if not paris.get("n"):
        return [f"   {paris.get('message', 'aucun pari')}"]
    lignes = [
        f"   paris pris        : {paris['n']}",
        f"   mise totale       : {paris['mise_totale']:.2f}",
        f"   P&L               : {paris['pnl']:+.2f}",
        f"   ROI               : {paris['roi']:+.2%}   "
        f"IC 95 % [{paris['ic_bas']:+.2%} ; {paris['ic_haut']:+.2%}] (bootstrap 1000 tirages)",
        f"   taux de reussite  : {paris['taux_reussite']:.1%} "
        f"(cote moyenne {paris['cote_moyenne']:.2f})",
        f"   drawdown maximum  : {paris['drawdown_max']:.1%}",
    ]
    if paris["n"] < 30:
        lignes.append(
            f"   >>> Seulement {paris['n']} pari(s) : a ce volume, ni le ROI ni son "
            "intervalle ne sont\n       interpretables. Il faut plusieurs centaines de "
            "paris pour que le chiffre veuille dire\n       quelque chose — et meme la, "
            "le CLV reste un signal plus rapide (voir T13).")
    if paris["ic_contient_zero"]:
        lignes.append(
            "   >>> L'INTERVALLE DE CONFIANCE CONTIENT ZERO : ce resultat n'est PAS "
            "distinguable\n       du hasard. Ni le ROI positif ni le ROI negatif ne "
            "prouvent quoi que ce soit ici.")
    else:
        signe = "positif" if paris["roi"] > 0 else "negatif"
        lignes.append(f"   >>> L'intervalle exclut zero : l'effet {signe} est statistiquement "
                      "net sur cet echantillon (ce qui ne garantit pas le futur).")
    return lignes


def _bloc_baselines(metriques: dict, baselines: dict) -> list[str]:
    lignes: list[str] = []
    favori = baselines["favori_marche"]
    if favori.get("n"):
        lignes.append(
            f"   parier le favori du marche : ROI {favori['roi']:+.2%} "
            f"IC 95 % [{favori['ic_bas']:+.2%} ; {favori['ic_haut']:+.2%}] sur {favori['n']} paris")
    marche = baselines["marche_devigge"]
    if marche.get("n"):
        ecart = metriques["1N2_final"]["log_loss"] - marche["log_loss"]
        lignes.append(f"   marche de-vigge seul       : log-loss {marche['log_loss']:.4f}")
        if ecart < 0:
            lignes.append(f"   -> le systeme bat le marche de {abs(ecart):.4f} de log-loss.")
        else:
            lignes.append(
                f"   -> ATTENTION : le systeme NE BAT PAS le marche "
                f"({ecart:+.4f} de log-loss).\n"
                "      Le marche reste la meilleure estimation disponible. Le modele ne "
                "doit servir\n      qu'a reperer des ecarts, pas a remplacer la cote.")
    if not lignes:
        lignes.append("   (pas de cotes historiques dans ces donnees : baselines indisponibles)")
    return lignes
