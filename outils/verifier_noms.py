#!/usr/bin/env python3
"""Affiche les equipes non appariees entre football-data.co.uk et FBref/Understat.

C'est l'outil qui sert a remplir data/noms_equipes.json a la main. Il ne
modifie rien tout seul, sauf si on lui passe --ajouter, auquel cas il demande
confirmation pour chaque correspondance proposee.

Usage :
    python outils/verifier_noms.py --championnat F1
    python outils/verifier_noms.py --championnat E0 --source understat --ajouter
    python outils/verifier_noms.py --championnat N1 --source odds_api

La source "odds_api" consomme une requete de ton quota The Odds API (le cache de
15 minutes s'applique). Les deux autres sont gratuites mais lentes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from data import noms  # noqa: E402
from data import resultats  # noqa: E402


def equipes_source(championnat: str, source: str, nb_saisons: int) -> list[str]:
    """Liste des equipes telles que les nomme la source demandee."""
    if source == "odds_api":
        from data import cotes  # import tardif

        cotes_df = cotes.recuperer(championnat)
        if cotes_df.empty:
            return []
        noms_vus = set(cotes_df["dom"].dropna()) | set(cotes_df["ext"].dropna())
        return sorted(str(nom) for nom in noms_vus)

    from data import xg  # import tardif : soccerdata est lent a charger

    return xg.equipes_source(championnat, source=source, nb_saisons=nb_saisons)


def afficher_rapport(rapport: dict[str, list], source_b: str) -> int:
    print(f"\n  Appariements surs : {len(rapport['surs'])}")
    for nom_a, nom_b, canonique in rapport["surs"]:
        print(f"    OK   {nom_a:28s} = {nom_b:28s} -> {canonique}")

    print(f"\n  A VERIFIER A LA MAIN : {len(rapport['a_verifier'])}")
    if not rapport["a_verifier"]:
        print("    (aucun)")
    for nom_a, nom_b, score in rapport["a_verifier"]:
        print(f"    ?    {nom_a:28s} ~ {nom_b:28s} (similarite {score})")

    print(f"\n  Sans correspondance cote football-data : {len(rapport['orphelins_a'])}")
    for nom in rapport["orphelins_a"]:
        print(f"    --   {nom}")
    print(f"\n  Sans correspondance cote {source_b} : {len(rapport['orphelins_b'])}")
    for nom in rapport["orphelins_b"]:
        print(f"    --   {nom}")

    return len(rapport["a_verifier"]) + len(rapport["orphelins_a"]) + len(rapport["orphelins_b"])


def _afficher_resolution_automatique(rapport: dict[str, list],
                                     equipes_fd: list[str]) -> None:
    """Signale les cas que noms.resoudre_vers regle deja sans entree en table.

    Inutile de saisir a la main ce que la normalisation deterministe couvre
    (par exemple "FC Utrecht" -> "Utrecht").
    """
    deja = [(nom_b, noms.resoudre_vers(nom_b, equipes_fd))
            for nom_b, _, _ in ((b, a, s) for a, b, s in rapport["a_verifier"])]
    deja = [(nom_b, cible) for nom_b, cible in deja if cible is not None]
    if not deja:
        return
    print(f"\n  Deja resolus sans entree en table ({len(deja)}) :")
    for nom_b, cible in deja:
        print(f"    auto {nom_b:28s} -> {cible}")
    print("    (normalisation deterministe : rien a saisir pour ceux-la)")


def ajouter_interactif(rapport: dict[str, list], source_b: str) -> None:
    """Demande a l'humain de valider chaque proposition, une par une."""
    for nom_a, nom_b, score in rapport["a_verifier"]:
        canonique = noms.canoniser(nom_a, "football_data") or nom_a
        reponse = input(
            f"  '{nom_b}' ({source_b}) est-il bien '{canonique}' ? "
            f"[o/N] (similarite {score}) "
        ).strip().lower()
        if reponse == "o":
            if noms.canoniser(nom_a, "football_data") is None:
                noms.ajouter_alias(canonique, "football_data", nom_a, ecrire=False)
            noms.ajouter_alias(canonique, source_b, nom_b)
            print(f"    ajoute : {nom_b} -> {canonique}")
        else:
            print("    ignore (un trou vaut mieux qu'une fausse correspondance)")


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--championnat", default="F1", help="code football-data (F1, E0, ...)")
    analyseur.add_argument("--source", default="fbref",
                           choices=("fbref", "understat", "odds_api"),
                           help="odds_api consomme une requete de ton quota")
    analyseur.add_argument("--saisons", type=int, default=2)
    analyseur.add_argument("--ajouter", action="store_true",
                           help="proposer d'enregistrer les correspondances, une par une")
    args = analyseur.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    championnat = args.championnat.upper()
    if championnat not in config.CHAMPIONNATS:
        print(f"Championnat inconnu : {championnat}")
        print(f"Codes valides : {', '.join(config.CODES_CHAMPIONNATS)}")
        return 2

    print(f"=== {config.nom_championnat(championnat)} ({championnat}) "
          f"— football-data vs {args.source} ===")

    historique = resultats.charger(championnat, args.saisons)
    equipes_fd = resultats.equipes(historique)
    if not equipes_fd:
        print("Aucune donnee football-data disponible (cache vide et reseau coupe ?).")
        return 1

    try:
        equipes_b = equipes_source(championnat, args.source, args.saisons)
    except Exception as erreur:
        print(f"Impossible d'interroger {args.source} : {erreur}")
        if args.source == "odds_api":
            print("Verifie ODDS_API_KEY dans .env et ton quota "
                  "(python cli.py diagnostic).")
        else:
            print("Verifie ta connexion, ou que ce championnat a bien des xG publics.")
        return 1

    if not equipes_b:
        print(f"{args.source} ne renvoie aucune equipe pour ce championnat : "
              "rien a apparier (pas de match a venir, ou pas de couverture).")
        return 0

    rapport = noms.rapprocher(equipes_fd, equipes_b, "football_data", args.source)
    restants = afficher_rapport(rapport, args.source)
    _afficher_resolution_automatique(rapport, equipes_fd)

    if args.ajouter and rapport["a_verifier"]:
        print("\n--- Validation manuelle ---")
        ajouter_interactif(rapport, args.source)

    print(_ligne_taux(rapport, equipes_fd, equipes_b, args.source))
    if rapport["a_verifier"] or rapport["orphelins_b"]:
        print("Complete data/noms_equipes.json pour les cas ci-dessus "
              "(ou relance avec --ajouter).")
    elif restants:
        print("Rien a saisir : les restants n'existent que d'un seul cote "
              "(equipe sans match a venir, ou saison ou elle ne jouait pas).")
    return 0


def _ligne_taux(rapport: dict[str, list], equipes_fd: list[str],
                equipes_b: list[str], source: str) -> str:
    """Taux d'appariement, mesure dans le sens qui a un sens pour la source.

    Pour un flux de cotes, seuls les matchs a venir sont cotes : une equipe
    presente cote football-data mais absente cote bookmaker n'est pas un trou,
    elle ne joue simplement pas cette semaine. On mesure donc la part des noms
    de la SOURCE qui sont resolus. Pour FBref/Understat, qui couvrent des
    saisons completes, on mesure dans le sens football-data.
    """
    surs = len(rapport["surs"])
    if source == "odds_api":
        total = len(equipes_b)
        sens = f"noms {source} resolus"
    else:
        total = len(equipes_fd)
        sens = "equipes football-data appariees"
    taux = surs / total if total else 1.0
    return f"\nTaux d'appariement : {taux:.1%} ({surs}/{total} {sens})"


if __name__ == "__main__":
    raise SystemExit(main())
