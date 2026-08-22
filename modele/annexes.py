"""Marches annexes : corners et cartons jaunes.

Modele multiplicatif classique — production x concession x moyenne de la
ligue — ajuste par maximum de vraisemblance Poisson, avec la meme ponderation
temporelle que le modele principal. Pas de correction Dixon-Coles ici : le
terme rho corrige la dependance des petits scores (0-0, 1-1), ce qui n'a
aucun sens sur des comptes qui tournent autour de dix.

Ce que ce module ne peut pas faire, et qui doit rester visible partout ou ses
chiffres sont affiches :

  - **Aucune contre-expertise.** The Odds API ne renvoie ni corners ni cartons
    sur le plan gratuit. Le garde-fou qui rattrape le modele principal quand il
    s'ecarte du marche n'existe pas ici : rien ne peut contredire ces nombres.
  - **L'arbitre n'est pas modelise.** C'est probablement le premier facteur des
    cartons, avant les deux equipes. La colonne existe pourtant dans les CSV
    football-data.co.uk : c'est une limite de ce module, pas de la donnee.
  - **Poisson sous-estime les totaux extremes des corners.** La dispersion
    reelle est mesuree a l'entrainement (`dispersion`) et transportee avec le
    modele, pour que l'interface la montre au lieu de la taire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import poisson

_journal = logging.getLogger("foot_edge.annexes")

XI_DEFAUT = 0.0018            # demi-vie d'environ 385 jours, comme les buts
ITERATIONS_MAX = 60
TOLERANCE = 1e-9

# Force de l'a priori, en "matchs equivalents". Sans lui, une equipe qui n'a
# jamais ete cartonnee sur la fenetre obtient production = 0, donc un lambda nul,
# donc une probabilite ZERO d'etre cartonnee au prochain match. Le backtest en a
# trouve 11 cas sur 4957 : c'est rare, et c'est indefendable. On ramene chaque
# equipe vers la moyenne de la ligue a hauteur de trois matchs de donnees.
PRIOR_MATCHS = 3.0

# Un marche par type de compte : colonnes source, seuils cotes usuellement,
# taille de matrice, et rythme d'oubli.
#
# `xi` = 0.004 (demi-vie ~173 jours, environ une saison) n'est pas repris du
# modele des buts : il a ete choisi par balayage sur 4957 matchs hors
# echantillon, 6 championnats. Il maximise la log-vraisemblance pour les deux
# comptes et divise par plus de deux le biais sur les cartons, dont le taux
# baisse d'annee en annee (4.04 -> 3.73 -> 3.70 par match).
TYPES: dict[str, dict] = {
    "corners": {
        "colonnes": ("corners_dom", "corners_ext"),
        "libelle": "corners",
        "seuils": (7.5, 8.5, 9.5, 10.5, 11.5, 12.5),
        "taille": 30,
        "xi": 0.004,
    },
    "jaunes": {
        "colonnes": ("jaunes_dom", "jaunes_ext"),
        "libelle": "cartons jaunes",
        "seuils": (1.5, 2.5, 3.5, 4.5, 5.5, 6.5),
        "taille": 16,
        "xi": 0.004,
    },
}


class DonneesInsuffisantes(RuntimeError):
    """Levee quand la colonne demandee est absente ou trop peu remplie."""


# --------------------------------------------------------------------------
# Modele entraine
# --------------------------------------------------------------------------

@dataclass
class ModeleComptes:
    """Taux estimes pour un type de compte (corners ou cartons)."""

    type_compte: str
    equipes: list[str]
    production: dict[str, float]     # ce que l'equipe genere, 1.0 = moyenne
    concession: dict[str, float]     # ce qu'elle laisse generer
    moyenne_dom: float
    moyenne_ext: float
    nb_matchs: int
    dispersion: float                # Pearson ; 1.0 = Poisson parfait
    convergence: bool = True
    matchs_par_equipe: dict[str, int] = field(default_factory=dict)

    @property
    def taille_matrice(self) -> int:
        return int(TYPES[self.type_compte]["taille"])

    def lambdas(self, dom: str, ext: str) -> tuple[float, float]:
        """Comptes attendus (domicile, exterieur)."""
        for equipe in (dom, ext):
            if equipe not in self.production:
                raise KeyError(f"Equipe inconnue du modele {self.type_compte} : {equipe!r}")
        return (self.moyenne_dom * self.production[dom] * self.concession[ext],
                self.moyenne_ext * self.production[ext] * self.concession[dom])

    def matrice(self, dom: str, ext: str) -> np.ndarray:
        """Loi jointe des deux comptes, sous hypothese d'independance."""
        lam_dom, lam_ext = self.lambdas(dom, ext)
        grille = np.arange(self.taille_matrice)
        return np.outer(poisson.pmf(grille, lam_dom), poisson.pmf(grille, lam_ext))

    def predire(self, dom: str, ext: str) -> dict:
        """Tous les marches derives : totaux, comparaison des deux equipes."""
        lam_dom, lam_ext = self.lambdas(dom, ext)
        matrice = self.matrice(dom, ext)
        taille = matrice.shape[0]
        totaux_grille = np.add.outer(np.arange(taille), np.arange(taille))

        totaux: dict[str, float] = {}
        for seuil in TYPES[self.type_compte]["seuils"]:
            plus = float(matrice[totaux_grille > seuil].sum())
            totaux[f"plus_{seuil}"] = plus
            totaux[f"moins_{seuil}"] = 1.0 - plus

        return {
            "type": self.type_compte,
            "libelle": TYPES[self.type_compte]["libelle"],
            "attendu_dom": float(lam_dom),
            "attendu_ext": float(lam_ext),
            "attendu_total": float(lam_dom + lam_ext),
            "totaux": totaux,
            "comparaison": {
                "1": float(np.tril(matrice, -1).sum()),   # le dom en a davantage
                "N": float(np.trace(matrice)),
                "2": float(np.triu(matrice, 1).sum()),
            },
            "dispersion": self.dispersion,
            "matchs": self.nb_matchs,
        }


# --------------------------------------------------------------------------
# Entrainement
# --------------------------------------------------------------------------

def _poids_temporels(dates: pd.Series, xi: float) -> np.ndarray:
    """exp(-xi * jours) : les matchs anciens comptent moins, comme ailleurs."""
    reference = pd.to_datetime(dates).max()
    jours = (reference - pd.to_datetime(dates)).dt.days.to_numpy(dtype=float)
    return np.exp(-xi * np.clip(jours, 0.0, None))


def entrainer(matchs: pd.DataFrame, type_compte: str,
              xi: float | None = None) -> ModeleComptes:
    """Ajuste production et concession par iterations proportionnelles.

    Les iterations convergent vers le maximum de vraisemblance du modele
    Poisson multiplicatif ; c'est la meme cible qu'un optimiseur generique,
    mais sans risque de non-convergence et en quelques millisecondes.
    """
    if type_compte not in TYPES:
        raise ValueError(f"Type de compte inconnu : {type_compte!r}. "
                         f"Connus : {', '.join(TYPES)}")
    # Chaque compte a son propre rythme d'oubli : les cartons baissent d'annee
    # en annee la ou les corners sont stables, et une demi-vie unique heritee du
    # modele des buts fait sur-estimer les cartons de facon systematique.
    xi = TYPES[type_compte].get("xi", XI_DEFAUT) if xi is None else xi
    col_dom, col_ext = TYPES[type_compte]["colonnes"]
    if col_dom not in matchs.columns or col_ext not in matchs.columns:
        raise DonneesInsuffisantes(
            f"Colonnes {col_dom}/{col_ext} absentes : football-data.co.uk ne les "
            f"fournit pas pour ce championnat, ou les saisons chargees sont trop "
            f"anciennes. Les marches {TYPES[type_compte]['libelle']} sont indisponibles.")

    utilisables = matchs.dropna(subset=[col_dom, col_ext, "dom", "ext", "date"])
    if len(utilisables) < 30:
        raise DonneesInsuffisantes(
            f"{len(utilisables)} match(s) avec {TYPES[type_compte]['libelle']} : "
            "trop peu pour estimer quoi que ce soit (30 minimum).")

    equipes = sorted(set(utilisables["dom"]) | set(utilisables["ext"]))
    index = {equipe: i for i, equipe in enumerate(equipes)}
    n = len(equipes)

    i_dom = utilisables["dom"].map(index).to_numpy()
    i_ext = utilisables["ext"].map(index).to_numpy()
    y_dom = utilisables[col_dom].to_numpy(dtype=float)
    y_ext = utilisables[col_ext].to_numpy(dtype=float)
    poids = _poids_temporels(utilisables["date"], xi)

    total_poids = poids.sum()
    moyenne_dom = float((poids * y_dom).sum() / total_poids)
    moyenne_ext = float((poids * y_ext).sum() / total_poids)

    # Un "match equivalent" vaut moyenne_dom + moyenne_ext evenements.
    prior = PRIOR_MATCHS * (moyenne_dom + moyenne_ext)
    production = np.ones(n)
    concession = np.ones(n)
    convergence = False
    for _ in range(ITERATIONS_MAX):
        precedent = np.concatenate([production, concession])

        # production[t] : ce que t genere, chez lui et dehors, rapporte a ce
        # que les defenses rencontrees laissent generer.
        genere = (np.bincount(i_dom, weights=poids * y_dom, minlength=n)
                  + np.bincount(i_ext, weights=poids * y_ext, minlength=n))
        attendu = (np.bincount(i_dom, weights=poids * moyenne_dom * concession[i_ext],
                               minlength=n)
                   + np.bincount(i_ext, weights=poids * moyenne_ext * concession[i_dom],
                                 minlength=n))
        # Le pseudo-comptage tire vers 1.0 (la moyenne de la ligue) : une equipe
        # sans historique suffisant ne peut plus se voir attribuer un taux nul.
        production = (genere + prior) / (attendu + prior)

        # concession[t] : ce que les adversaires generent contre t.
        concede = (np.bincount(i_ext, weights=poids * y_dom, minlength=n)
                   + np.bincount(i_dom, weights=poids * y_ext, minlength=n))
        attendu = (np.bincount(i_ext, weights=poids * moyenne_dom * production[i_dom],
                               minlength=n)
                   + np.bincount(i_dom, weights=poids * moyenne_ext * production[i_ext],
                                 minlength=n))
        concession = (concede + prior) / (attendu + prior)

        # Identifiabilite : production et concession sont definies a une
        # constante multiplicative pres, on fixe leur moyenne a 1.
        production /= production.mean()
        concession /= concession.mean()

        if np.max(np.abs(np.concatenate([production, concession]) - precedent)) < TOLERANCE:
            convergence = True
            break

    lam_dom = moyenne_dom * production[i_dom] * concession[i_ext]
    lam_ext = moyenne_ext * production[i_ext] * concession[i_dom]
    dispersion = _dispersion_pearson(
        np.concatenate([y_dom, y_ext]), np.concatenate([lam_dom, lam_ext]), n)

    if not convergence:
        _journal.info("Modele %s : %d iterations sans convergence stricte ; "
                      "les taux restent utilisables.", type_compte, ITERATIONS_MAX)

    comptes = pd.concat([utilisables["dom"], utilisables["ext"]]).value_counts()
    return ModeleComptes(
        type_compte=type_compte, equipes=equipes,
        production={e: float(production[i]) for e, i in index.items()},
        concession={e: float(concession[i]) for e, i in index.items()},
        moyenne_dom=moyenne_dom, moyenne_ext=moyenne_ext,
        nb_matchs=int(len(utilisables)), dispersion=dispersion,
        convergence=convergence,
        matchs_par_equipe={str(e): int(c) for e, c in comptes.items()},
    )


def _dispersion_pearson(observes: np.ndarray, attendus: np.ndarray,
                        nb_equipes: int) -> float:
    """Variance reelle rapportee a celle qu'une loi de Poisson impliquerait.

    1.0 signifie que Poisson decrit bien la donnee ; au-dessus, les valeurs
    extremes sont plus frequentes que le modele ne le dit, et les probabilites
    des gros totaux sont donc sous-estimees. C'est mesure sur les residus du
    modele ajuste, pas sur les comptes bruts : les ecarts entre equipes sont
    deja absorbes par production et concession.
    """
    valides = attendus > 0
    if not valides.any():
        return float("nan")
    residus = (observes[valides] - attendus[valides]) ** 2 / attendus[valides]
    # Degres de liberte : 2 taux par equipe estimes, moins 2 contraintes.
    ddl = max(1, int(valides.sum()) - 2 * nb_equipes + 2)
    return float(residus.sum() / ddl)


# --------------------------------------------------------------------------
# Validation hors echantillon
# --------------------------------------------------------------------------
# Les corners et les cartons ne sont cotes par aucun bookmaker via l'API : rien
# ne peut contredire ces modeles en direct, contrairement au 1N2. Ce qui suit
# est la seule contre-expertise possible — les confronter aux comptes reellement
# observes, en walk-forward. Ca ne dit pas si un pari serait rentable (il n'y a
# aucune cote historique de corners nulle part), seulement si les probabilites
# annoncees correspondent aux frequences constatees.

@dataclass
class Validation:
    """Ce que le modele vaut, mesure contre les comptes reels."""

    type_compte: str
    championnat: str
    nb_matchs: int
    biais: float                 # reel - attendu ; negatif = sur-estime
    biais_ic: float              # demi-largeur a 95 %
    erreur_absolue: float
    dispersion: float            # hors echantillon, Poisson suppose 1.00
    log_vraisemblance: float
    log_vraisemblance_moyenne_ligue: float   # baseline sans force d'equipe
    calibration: list[dict]      # par ligne : proba annoncee vs frequence reelle
    ecart_calibration_max: float
    calcule_le: str

    @property
    def bat_la_moyenne_de_ligue(self) -> bool:
        """Les forces par equipe apportent-elles quelque chose ?"""
        return self.log_vraisemblance > self.log_vraisemblance_moyenne_ligue

    @property
    def biais_significatif(self) -> bool:
        return abs(self.biais) > self.biais_ic

    @property
    def verdict(self) -> str:
        libelle = TYPES[self.type_compte]["libelle"]
        lignes = [
            f"{libelle} — {self.nb_matchs} matchs hors echantillon.",
            f"  Calibration : ecart maximal {self.ecart_calibration_max:.1%} entre "
            f"probabilite annoncee et frequence reelle sur les lignes testees.",
        ]
        if self.biais_significatif:
            sens = "sur-estime" if self.biais < 0 else "sous-estime"
            lignes.append(f"  Biais : le modele {sens} de {abs(self.biais):.2f} "
                          f"{libelle} par match (+/- {self.biais_ic:.2f}).")
        else:
            lignes.append(f"  Biais : {self.biais:+.2f} +/- {self.biais_ic:.2f}, "
                          "non distinguable de zero.")
        if self.bat_la_moyenne_de_ligue:
            lignes.append("  Les forces par equipe ameliorent la prediction par "
                          "rapport a la simple moyenne du championnat.")
        else:
            lignes.append("  ATTENTION : les forces par equipe n'ameliorent PAS la "
                          "prediction par rapport a la simple moyenne du championnat. "
                          "Le detail par equipe est donc decoratif sur ce compte.")
        return "\n".join(lignes)


def _predictions_walk_forward(matchs: pd.DataFrame, type_compte: str,
                              taille_initiale: int, xi: float | None) -> pd.DataFrame:
    """Predictions n'utilisant jamais le futur : on entraine sur le passe strict."""
    col_dom, col_ext = TYPES[type_compte]["colonnes"]
    utilisables = (matchs.dropna(subset=["date", "dom", "ext", col_dom, col_ext])
                   .sort_values("date").reset_index(drop=True))
    lignes: list[dict] = []
    for jour in sorted(utilisables["date"].unique()):
        passe = utilisables[utilisables["date"] < jour]
        if len(passe) < taille_initiale:
            continue
        try:
            modele = entrainer(passe, type_compte, xi=xi)
        except DonneesInsuffisantes:
            continue
        for match in utilisables[utilisables["date"] == jour].itertuples():
            if match.dom not in modele.production or match.ext not in modele.production:
                continue  # promu : on ne devine pas
            prediction = modele.predire(match.dom, match.ext)
            ligne = {
                "reel_dom": float(getattr(match, col_dom)),
                "reel_ext": float(getattr(match, col_ext)),
                "attendu_dom": prediction["attendu_dom"],
                "attendu_ext": prediction["attendu_ext"],
                "attendu_total": prediction["attendu_total"],
                "moyenne_ligue": modele.moyenne_dom + modele.moyenne_ext,
            }
            for seuil in TYPES[type_compte]["seuils"]:
                ligne[f"p_plus_{seuil}"] = prediction["totaux"][f"plus_{seuil}"]
            lignes.append(ligne)
    return pd.DataFrame(lignes)


def valider(matchs: pd.DataFrame, type_compte: str, championnat: str = "",
            taille_initiale: int = 200, xi: float | None = None) -> Validation:
    """Confronte le modele aux comptes reels, sans jamais lui montrer le futur."""
    from datetime import datetime, timezone

    predictions = _predictions_walk_forward(matchs, type_compte, taille_initiale, xi)
    if len(predictions) < 100:
        raise DonneesInsuffisantes(
            f"{len(predictions)} prediction(s) hors echantillon pour "
            f"{TYPES[type_compte]['libelle']} : trop peu pour valider quoi que ce soit. "
            "Charge davantage de saisons.")

    reel = (predictions["reel_dom"] + predictions["reel_ext"]).to_numpy()
    attendu = predictions["attendu_total"].to_numpy()
    erreur = reel - attendu

    calibration_lignes = []
    for seuil in TYPES[type_compte]["seuils"]:
        annonce = float(predictions[f"p_plus_{seuil}"].mean())
        constate = float((reel > seuil).mean())
        calibration_lignes.append({"ligne": seuil, "annonce": annonce,
                                   "constate": constate, "ecart": annonce - constate})

    lam = np.concatenate([predictions["attendu_dom"], predictions["attendu_ext"]])
    obs = np.concatenate([predictions["reel_dom"], predictions["reel_ext"]])

    return Validation(
        type_compte=type_compte, championnat=championnat.upper(),
        nb_matchs=int(len(predictions)),
        biais=float(erreur.mean()),
        biais_ic=float(1.96 * erreur.std(ddof=1) / np.sqrt(len(erreur))),
        erreur_absolue=float(np.abs(erreur).mean()),
        dispersion=float((((obs - lam) ** 2) / lam).mean()),
        log_vraisemblance=float(poisson.logpmf(reel, attendu).mean()),
        log_vraisemblance_moyenne_ligue=float(
            poisson.logpmf(reel, predictions["moyenne_ligue"].to_numpy()).mean()),
        calibration=calibration_lignes,
        ecart_calibration_max=float(max(abs(l["ecart"]) for l in calibration_lignes)),
        calcule_le=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def chemin_validation(championnat: str, type_compte: str):
    import config
    return config.DOSSIER_CALIBRATION / f"validation_{type_compte}_{championnat.upper()}.json"


def enregistrer_validation(validation: Validation):
    import json
    import config
    from dataclasses import asdict
    config.creer_dossiers()
    destination = chemin_validation(validation.championnat, validation.type_compte)
    destination.write_text(json.dumps(asdict(validation), indent=2), encoding="utf-8")
    return destination


def charger_validation(championnat: str, type_compte: str) -> Validation | None:
    """Validation deja calculee, ou None. Ne calcule jamais : trop long pour une requete."""
    import json
    fichier = chemin_validation(championnat, type_compte)
    if not fichier.exists():
        return None
    try:
        return Validation(**json.loads(fichier.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError) as erreur:
        _journal.info("Validation illisible pour %s/%s : %s", championnat, type_compte, erreur)
        return None
