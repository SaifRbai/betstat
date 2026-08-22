"""Journal de paris : enregistrer, regler, mesurer — et surtout mesurer le CLV.

Pourquoi le CLV (Closing Line Value) est la metrique centrale de ce module :
le P&L sur 200 paris est domine par la chance. Le CLV compare la cote que tu as
prise a la cote de cloture (la derniere avant le coup d'envoi), qui est
l'estimation la plus precise qui existe. Il converge beaucoup plus vite. Un CLV
moyen positif sur 100 paris est un signal solide ; un P&L positif sur 100 paris
ne prouve rien du tout.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

import config
from data import cotes as cotes_module
from modele import calibration

_journal = logging.getLogger("foot_edge.journal")

STATUTS = ("en_cours", "gagne", "perdu", "rembourse", "annule")

SCHEMA = """
CREATE TABLE IF NOT EXISTS paris (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date_pari     TEXT NOT NULL,
    date_match    TEXT NOT NULL,
    championnat   TEXT,
    dom           TEXT NOT NULL,
    ext           TEXT NOT NULL,
    marche        TEXT NOT NULL,
    selection     TEXT NOT NULL,
    cote_prise    REAL NOT NULL,
    bookmaker     TEXT,
    mise          REAL NOT NULL,
    p_modele      REAL,
    p_marche      REAL,
    p_finale      REAL,
    esperance     REAL,
    statut        TEXT NOT NULL DEFAULT 'en_cours',
    gain          REAL DEFAULT 0.0,
    cote_cloture  REAL,
    clv           REAL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_paris_statut ON paris (statut);
CREATE INDEX IF NOT EXISTS idx_paris_match ON paris (dom, ext, date_match);
"""


def connexion(chemin_db: Path | None = None) -> sqlite3.Connection:
    config.creer_dossiers()
    lien = sqlite3.connect(chemin_db or config.CHEMIN_DB)
    lien.row_factory = sqlite3.Row
    lien.executescript(SCHEMA)
    lien.executescript(cotes_module.SCHEMA_HISTORIQUE)
    return lien


# --------------------------------------------------------------------------
# Ecriture
# --------------------------------------------------------------------------

def enregistrer(
    dom: str,
    ext: str,
    marche: str,
    selection: str,
    cote_prise: float,
    mise: float,
    date_match: str,
    championnat: str = "",
    bookmaker: str = "",
    p_modele: float | None = None,
    p_marche: float | None = None,
    p_finale: float | None = None,
    esperance: float | None = None,
    note: str = "",
    chemin_db: Path | None = None,
) -> int:
    """Ajoute un pari au journal et renvoie son identifiant."""
    if cote_prise <= 1.0:
        raise ValueError(f"Cote invalide ({cote_prise}) : une cote decimale est toujours > 1.")
    if mise <= 0:
        raise ValueError("La mise doit etre strictement positive.")

    lien = connexion(chemin_db)
    try:
        curseur = lien.execute(
            "INSERT INTO paris (date_pari, date_match, championnat, dom, ext, marche, "
            "selection, cote_prise, bookmaker, mise, p_modele, p_marche, p_finale, "
            "esperance, statut, gain, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'en_cours',0.0,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), str(date_match),
             championnat.upper(), dom, ext, marche, selection, float(cote_prise),
             bookmaker, float(mise), p_modele, p_marche, p_finale, esperance, note))
        lien.commit()
        return int(curseur.lastrowid)
    finally:
        lien.close()


def regler(identifiant: int, resultat: str, chemin_db: Path | None = None) -> dict:
    """Solde un pari : "gagne", "perdu", "rembourse" ou "annule"."""
    if resultat not in STATUTS or resultat == "en_cours":
        raise ValueError(f"Resultat invalide : {resultat!r}. "
                         f"Valeurs acceptees : gagne, perdu, rembourse, annule.")
    lien = connexion(chemin_db)
    try:
        ligne = lien.execute("SELECT * FROM paris WHERE id=?", (identifiant,)).fetchone()
        if ligne is None:
            raise KeyError(f"Aucun pari numero {identifiant} dans le journal.")
        mise, cote = float(ligne["mise"]), float(ligne["cote_prise"])
        gain = {"gagne": mise * (cote - 1.0), "perdu": -mise,
                "rembourse": 0.0, "annule": 0.0}[resultat]
        lien.execute("UPDATE paris SET statut=?, gain=? WHERE id=?",
                     (resultat, gain, identifiant))
        lien.commit()
        return {"id": identifiant, "statut": resultat, "gain": gain}
    finally:
        lien.close()


def supprimer(identifiant: int, chemin_db: Path | None = None) -> None:
    lien = connexion(chemin_db)
    try:
        lien.execute("DELETE FROM paris WHERE id=?", (identifiant,))
        lien.commit()
    finally:
        lien.close()


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------

def lister(statut: str | None = None, chemin_db: Path | None = None) -> pd.DataFrame:
    lien = connexion(chemin_db)
    try:
        requete = "SELECT * FROM paris"
        parametres: tuple = ()
        if statut:
            requete += " WHERE statut=?"
            parametres = (statut,)
        return pd.read_sql_query(requete + " ORDER BY date_match, id", lien, params=parametres)
    finally:
        lien.close()


def exposition(chemin_db: Path | None = None) -> float:
    """Somme misee sur les paris non encore soldes."""
    en_cours = lister("en_cours", chemin_db)
    return float(en_cours["mise"].sum()) if not en_cours.empty else 0.0


# --------------------------------------------------------------------------
# Bilans
# --------------------------------------------------------------------------

def _agreger(paris: pd.DataFrame) -> dict:
    soldes = paris[paris["statut"].isin(["gagne", "perdu"])]
    mise_totale = float(soldes["mise"].sum())
    pnl = float(soldes["gain"].sum())
    return {
        "nb_paris": int(len(paris)),
        "nb_soldes": int(len(soldes)),
        "nb_en_cours": int((paris["statut"] == "en_cours").sum()),
        "mise_totale": mise_totale,
        "pnl": pnl,
        "roi": pnl / mise_totale if mise_totale else float("nan"),
        "taux_reussite": float((soldes["statut"] == "gagne").mean()) if len(soldes) else float("nan"),
        "cote_moyenne": float(soldes["cote_prise"].mean()) if len(soldes) else float("nan"),
    }


def bilan(chemin_db: Path | None = None) -> dict:
    """Bilan global : volumes, P&L, ROI et CLV."""
    paris = lister(chemin_db=chemin_db)
    if paris.empty:
        return {"nb_paris": 0, "message": "Journal vide : aucun pari enregistre."}
    resultat = _agreger(paris)
    resultat["exposition"] = float(paris.loc[paris["statut"] == "en_cours", "mise"].sum())
    resultat["clv"] = bilan_clv(chemin_db)
    return resultat


def _bilan_par(colonne: str, chemin_db: Path | None = None) -> pd.DataFrame:
    paris = lister(chemin_db=chemin_db)
    if paris.empty:
        return pd.DataFrame(columns=[colonne, "nb_paris", "mise_totale", "pnl", "roi"])
    lignes = []
    for cle, groupe in paris.groupby(colonne, dropna=False):
        agrege = _agreger(groupe)
        lignes.append({colonne: cle or "(inconnu)", "nb_paris": agrege["nb_paris"],
                       "nb_soldes": agrege["nb_soldes"], "mise_totale": agrege["mise_totale"],
                       "pnl": agrege["pnl"], "roi": agrege["roi"]})
    return pd.DataFrame(lignes).sort_values("pnl", ascending=False).reset_index(drop=True)


def bilan_par_marche(chemin_db: Path | None = None) -> pd.DataFrame:
    return _bilan_par("marche", chemin_db)


def bilan_par_championnat(chemin_db: Path | None = None) -> pd.DataFrame:
    return _bilan_par("championnat", chemin_db)


def courbe_bankroll(bankroll_initiale: float = 0.0,
                    chemin_db: Path | None = None) -> pd.DataFrame:
    """Cumul du P&L dans l'ordre de reglement des paris."""
    paris = lister(chemin_db=chemin_db)
    soldes = paris[paris["statut"].isin(["gagne", "perdu", "rembourse"])].copy()
    if soldes.empty:
        return pd.DataFrame(columns=["numero", "date_match", "gain", "bankroll"])
    soldes = soldes.sort_values(["date_match", "id"]).reset_index(drop=True)
    soldes["numero"] = np.arange(1, len(soldes) + 1)
    soldes["bankroll"] = bankroll_initiale + soldes["gain"].cumsum()
    return soldes[["numero", "date_match", "dom", "ext", "gain", "bankroll"]]


def exporter_csv(chemin: Path, chemin_db: Path | None = None) -> Path:
    paris = lister(chemin_db=chemin_db)
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    paris.to_csv(chemin, index=False)
    return chemin


# ==========================================================================
# T13 — Closing Line Value
# ==========================================================================

_SELECTIONS_1N2 = ("1", "N", "2")
_SELECTIONS_TOTAUX = ("plus", "moins")


def _ordre_selections(marche: str) -> tuple[str, ...]:
    return _SELECTIONS_TOTAUX if marche == cotes_module.MARCHE_TOTAUX else _SELECTIONS_1N2


def cote_cloture_devigguee(
    instantane: pd.DataFrame, marche: str, selection: str, methode: str = "shin"
) -> float | None:
    """Cote de cloture equitable : mediane inter-books, marge retiree.

    On ne compare pas la cote prise a la cote brute de cloture — celle-ci
    contient la marge du book. On compare a la cote "juste" du marche, sinon
    tout le monde aurait un CLV negatif par construction.
    """
    if instantane.empty:
        return None
    ordre = _ordre_selections(marche)
    probas: list[float] = []
    for _, groupe in instantane.groupby("bookmaker"):
        par_selection = groupe.set_index("selection")["cote"].to_dict()
        if not all(s in par_selection for s in ordre):
            continue
        try:
            devigge = calibration.deviguer([par_selection[s] for s in ordre], methode)
        except ValueError:
            continue
        probas.append(float(devigge[ordre.index(selection)]))
    if not probas:
        return None
    proba = float(np.median(probas))
    return 1.0 / proba if proba > 0 else None


def calculer_clv(cote_prise: float, cote_cloture: float) -> float:
    """clv = cote_prise / cote_de_cloture_devigguee - 1."""
    if cote_cloture is None or cote_cloture <= 0:
        return float("nan")
    return cote_prise / cote_cloture - 1.0


def capturer_clotures(
    chemin_db: Path | None = None,
    rafraichir_api: bool = True,
    championnats: Iterable[str] | None = None,
    maintenant: str | None = None,
) -> dict:
    """Renseigne cote_cloture et clv pour les paris en cours.

    A lancer juste avant les coups d'envoi (manuellement, ou par une tache
    planifiee : cron sous Linux, Planificateur de taches sous Windows).
    Chaque championnat concerne coute au plus une requete API, et le cache de
    15 minutes s'applique.
    """
    en_cours = lister("en_cours", chemin_db)
    if en_cours.empty:
        return {"traites": 0, "message": "Aucun pari en cours : rien a capturer."}

    if rafraichir_api:
        _rafraichir_cotes(en_cours, championnats)

    instant = maintenant or datetime.now(timezone.utc).isoformat(timespec="seconds")
    lien = connexion(chemin_db)
    traites, sans_donnees = 0, []
    try:
        for pari in en_cours.itertuples():
            instantane = cotes_module.dernieres_cotes_avant(
                pari.dom, pari.ext, pari.date_match, instant, pari.marche, chemin_db)
            cloture = cote_cloture_devigguee(instantane, pari.marche, pari.selection)
            if cloture is None:
                sans_donnees.append(f"{pari.dom} - {pari.ext} ({pari.marche} {pari.selection})")
                continue
            clv = calculer_clv(float(pari.cote_prise), cloture)
            lien.execute("UPDATE paris SET cote_cloture=?, clv=? WHERE id=?",
                         (cloture, clv, int(pari.id)))
            traites += 1
        lien.commit()
    finally:
        lien.close()

    if sans_donnees:
        _journal.warning(
            "%d pari(s) sans cote de cloture : %s.\n"
            "  Les cotes doivent avoir ete historisees AVANT le coup d'envoi "
            "(data.cotes.recuperer). Elles ne sont pas recuperables apres coup.",
            len(sans_donnees), " ; ".join(sans_donnees[:5]))
    return {"traites": traites, "sans_donnees": sans_donnees}


def _rafraichir_cotes(paris: pd.DataFrame, championnats: Iterable[str] | None) -> None:
    codes = list(championnats) if championnats else [
        c for c in paris["championnat"].dropna().unique() if c
    ]
    for code in codes:
        try:
            cotes_module.recuperer(code)
        except Exception as erreur:
            _journal.warning("Rafraichissement des cotes impossible pour %s : %s", code, erreur)


def bilan_clv(chemin_db: Path | None = None) -> dict:
    """Moyenne, ecart-type et part de paris a CLV positif."""
    paris = lister(chemin_db=chemin_db)
    if paris.empty or paris["clv"].notna().sum() == 0:
        return {"n": 0, "message": (
            "Aucun CLV calcule pour l'instant. Lance capturer_clotures() juste avant "
            "les coups d'envoi : sans cotes de cloture historisees, le CLV est perdu "
            "definitivement.")}
    valeurs = paris["clv"].dropna().to_numpy(dtype=float)
    moyenne = float(valeurs.mean())
    return {
        "n": int(valeurs.size),
        "clv_moyen": moyenne,
        "clv_median": float(np.median(valeurs)),
        "ecart_type": float(valeurs.std(ddof=1)) if valeurs.size > 1 else 0.0,
        "part_positive": float((valeurs > 0).mean()),
        "interpretation": interpreter_clv(moyenne, float((valeurs > 0).mean()), int(valeurs.size)),
    }


def interpreter_clv(clv_moyen: float, part_positive: float, effectif: int) -> str:
    """Une phrase qui dit a l'utilisateur ce que ce chiffre signifie pour lui."""
    base = (
        f"CLV moyen {clv_moyen:+.2%} sur {effectif} pari(s), dont {part_positive:.0%} a CLV positif. "
        "Le CLV compare ta cote a la cote de cloture equitable : c'est le seul indicateur "
        "qui converge assez vite pour dire si ton avantage est reel. Ton P&L sur quelques "
        "centaines de paris, lui, est domine par la chance."
    )
    if effectif < 30:
        return base + " A moins de 30 paris, meme le CLV reste indicatif : continue a mesurer."
    if clv_moyen > 0.01 and part_positive > 0.5:
        return base + " Un CLV positif repete est le signal le plus solide que tu puisses obtenir."
    if clv_moyen > 0:
        return base + " CLV legerement positif : encourageant, mais pas encore concluant."
    return (base + " CLV negatif : tu prends en moyenne des cotes moins bonnes que la cloture. "
            "Un P&L positif dans ce cas serait de la chance, pas un avantage.")


def texte_bilan(chemin_db: Path | None = None) -> str:
    """Bilan complet, formate pour la console et pour l'interface."""
    resultat = bilan(chemin_db)
    if resultat.get("nb_paris", 0) == 0:
        return resultat.get("message", "Journal vide.")
    lignes = [
        "=== BILAN DU JOURNAL ===",
        f"  paris enregistres : {resultat['nb_paris']} "
        f"(dont {resultat['nb_en_cours']} en cours)",
        f"  mise totale       : {resultat['mise_totale']:.2f}",
        f"  P&L               : {resultat['pnl']:+.2f}",
        f"  ROI               : {resultat['roi']:+.2%}" if resultat["nb_soldes"]
        else "  ROI               : n/a (aucun pari solde)",
        f"  taux de reussite  : {resultat['taux_reussite']:.1%}" if resultat["nb_soldes"]
        else "  taux de reussite  : n/a",
        f"  exposition en cours : {resultat['exposition']:.2f}",
        "",
        "--- CLV (l'indicateur qui compte vraiment) ---",
        "  " + (resultat["clv"].get("interpretation") or resultat["clv"].get("message", "")),
    ]
    if resultat["nb_soldes"] and resultat["nb_soldes"] < 200:
        lignes.append(
            f"\n  Rappel : {resultat['nb_soldes']} paris soldes, c'est trop peu pour qu'un "
            "P&L\n  positif ou negatif signifie quoi que ce soit. Regarde le CLV.")
    par_marche = bilan_par_marche(chemin_db)
    if not par_marche.empty:
        lignes += ["", "--- Par marche ---", par_marche.to_string(index=False)]
    return "\n".join(lignes)
