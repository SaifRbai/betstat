#!/usr/bin/env python3
"""foot_edge en ligne de commande.

Toutes les fonctions de l'interface graphique sont accessibles ici, ce qui
permet d'automatiser (tache planifiee pour capturer les cotes de cloture, par
exemple) et de travailler sans tkinter.

Exemples :
    python cli.py charger --championnat F1 --saisons 3 --source melange
    python cli.py match --championnat F1 --dom "Paris SG" --ext Marseille
    python cli.py opportunites --championnat F1 --bankroll 500
    python cli.py bilan
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

import config
from data import cotes as cotes_module
from data import noms as noms_module
from data import resultats as resultats_module
from data import xg as xg_module
from modele import annexes as annexes_module
from modele import backtest as backtest_module
from modele import calibration
from modele import dixon_coles as dc
from paris import journal as journal_module
from paris import performance as performance_module
from paris import quotidien as quotidien_module
from paris import valeur as valeur_module

_journal = logging.getLogger("foot_edge.cli")

CHEMIN_OPPORTUNITES = config.DOSSIER_DONNEES / "dernieres_opportunites.json"


# --------------------------------------------------------------------------
# Aides communes
# --------------------------------------------------------------------------

def resoudre_equipe(nom: str, disponibles: Sequence[str]) -> str:
    """Resolution floue d'un nom d'equipe, avec suggestions en cas d'echec."""
    exact = [e for e in disponibles if e.lower() == nom.lower()]
    if exact:
        return exact[0]
    canonique = noms_module.canoniser(nom, "football_data")
    if canonique:
        for equipe in disponibles:
            if noms_module.canoniser(equipe, "football_data") == canonique:
                return equipe
    propositions = noms_module.suggerer(nom, disponibles, 3)
    if propositions and propositions[0][1] > 0.85:
        return propositions[0][0]
    noms_proches = ", ".join(f"{n!r}" for n, _ in propositions)
    raise SystemExit(
        f"Equipe introuvable : {nom!r}.\n"
        f"  Voulais-tu dire : {noms_proches} ?\n"
        f"  (liste complete : python cli.py forces --championnat <code>)"
    )


def charger_donnees(championnat: str, saisons: int, source: str,
                    forcer: bool = False) -> tuple[pd.DataFrame, dict | None]:
    """Historique, enrichi des xG si la source les demande.

    `forcer` retelecharge le CSV de la saison en cours au lieu de lire le cache.
    Sans lui, les matchs joues depuis moins de six heures manquent a l'appel :
    le TTL du cache les rend invisibles, ce qui n'est pas grave pour un modele
    entraine sur quatre saisons, mais l'est pour une validation qu'on relance
    justement pour integrer les derniers resultats.
    """
    if source == "buts":
        return resultats_module.charger(championnat, saisons, forcer=forcer), None
    matchs, rapport = xg_module.charger_avec_xg(championnat, saisons)
    return matchs, rapport


def entrainer_modele(matchs: pd.DataFrame, source: str, championnat: str,
                     xi: float | None = None) -> dc.ModeleDixonColes:
    cible = {"buts": "buts", "xg": "xg", "melange": "melange"}[source]
    parametres = calibration.charger_hyperparametres(championnat) or {}
    xi_retenu = xi if xi is not None else parametres.get("xi", dc.XI_DEFAUT)
    poids = parametres.get("w", dc.POIDS_XG_DEFAUT)
    return dc.entrainer(matchs, cible=cible, poids_xg=poids, xi=xi_retenu,
                        regularisation=parametres.get("regularisation"))


def _verifier_donnees(matchs: pd.DataFrame, championnat: str) -> None:
    if matchs.empty:
        raise SystemExit(
            f"Aucune donnee pour {championnat}.\n"
            "  Verifie ta connexion internet, puis relance. Les fichiers deja "
            "telecharges restent utilisables hors ligne."
        )


# --------------------------------------------------------------------------
# Sous-commandes
# --------------------------------------------------------------------------

def commande_charger(args: argparse.Namespace) -> int:
    matchs, rapport = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    print(f"{config.nom_championnat(args.championnat)} : {len(matchs)} matchs, "
          f"{len(resultats_module.equipes(matchs))} equipes, "
          f"saisons {', '.join(sorted(matchs['saison'].unique()))}")
    if rapport:
        print(xg_module.afficher_rapport(rapport, args.championnat))
    taux = noms_module.taux_appariement(resultats_module.equipes(matchs))
    print(f"Noms d'equipes reconnus par la table : {taux:.0%}")
    if taux < 0.95:
        print("  Complete data/noms_equipes.json : "
              f"python outils/verifier_noms.py --championnat {args.championnat}")
    return 0


def commande_match(args: argparse.Namespace) -> int:
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    equipes = resultats_module.equipes(matchs)
    dom = resoudre_equipe(args.dom, equipes)
    ext = resoudre_equipe(args.ext, equipes)
    modele = entrainer_modele(matchs, args.source, args.championnat)
    prediction = modele.predire(dom, ext)
    print(_rapport_match(prediction, matchs, dom, ext, args.cote))
    return 0


def _rapport_match(prediction: dict, matchs: pd.DataFrame, dom: str, ext: str,
                   cotes_saisies: Sequence[float] | None) -> str:
    p = prediction["1N2"]
    lignes = [
        f"=== {dom} - {ext} ===",
        f"Buts attendus : {prediction['lambda_dom']:.2f} - {prediction['lambda_ext']:.2f}",
        "",
        f"1N2        : 1 {p['1']:.1%}  N {p['N']:.1%}  2 {p['2']:.1%}",
        f"Double chance : 1N {prediction['double_chance']['1N']:.1%}  "
        f"12 {prediction['double_chance']['12']:.1%}  "
        f"N2 {prediction['double_chance']['N2']:.1%}",
        f"+2.5 buts  : {prediction['totaux']['plus_2.5']:.1%}   "
        f"-2.5 buts : {prediction['totaux']['moins_2.5']:.1%}",
        f"+1.5 buts  : {prediction['totaux']['plus_1.5']:.1%}   "
        f"+3.5 buts : {prediction['totaux']['plus_3.5']:.1%}",
        f"BTTS       : {prediction['btts']['oui']:.1%}",
        f"Clean sheet : {dom} {prediction['clean_sheet']['dom']:.1%}  "
        f"{ext} {prediction['clean_sheet']['ext']:.1%}",
        "",
        "Scores les plus probables :",
    ]
    for score, proba in prediction["scores_probables"][:6]:
        lignes.append(f"   {score}  {proba:.1%}")

    if cotes_saisies:
        lignes += ["", "Comparaison avec les cotes saisies :"]
        lignes += _comparer_cotes(p, cotes_saisies)

    confrontations = matchs[((matchs["dom"] == dom) & (matchs["ext"] == ext))
                            | ((matchs["dom"] == ext) & (matchs["ext"] == dom))]
    if not confrontations.empty:
        lignes += ["", "Confrontations directes recentes :"]
        for ligne in confrontations.tail(5).itertuples():
            lignes.append(f"   {ligne.date:%d/%m/%Y}  {ligne.dom} {ligne.buts_dom:.0f}"
                          f"-{ligne.buts_ext:.0f} {ligne.ext}")
    lignes += ["", config.AVERTISSEMENT_LEGAL]
    return "\n".join(lignes)


def _comparer_cotes(probas: dict[str, float], cotes: Sequence[float]) -> list[str]:
    try:
        marche = calibration.deviguer(list(cotes))
    except ValueError as erreur:
        return [f"   {erreur}"]
    lignes = [f"   surcote du book : {calibration.surcote(list(cotes)):.1%}"]
    for i, selection in enumerate(("1", "N", "2")):
        esperance = probas[selection] * cotes[i] - 1
        lignes.append(
            f"   {selection} @ {cotes[i]:.2f} : marche {marche[i]:.1%} | "
            f"modele {probas[selection]:.1%} | esperance {esperance:+.1%}")
    return lignes


def commande_journee(args: argparse.Namespace) -> int:
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    modele = entrainer_modele(matchs, args.source, args.championnat)
    tableau = _tableau_confrontations(modele)
    tableau = _filtrer_journee(tableau, args.filtre)
    tableau = tableau.head(args.top)
    print(tableau.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    if args.export:
        chemin = Path(args.export)
        tableau.to_csv(chemin, index=False)
        print(f"\nExporte vers {chemin}")
    print("\n" + config.AVERTISSEMENT_LEGAL)
    return 0


def _tableau_confrontations(modele: dc.ModeleDixonColes) -> pd.DataFrame:
    lignes = []
    for dom in modele.equipes:
        for ext in modele.equipes:
            if dom == ext:
                continue
            p = modele.predire(dom, ext)
            lignes.append({
                "dom": dom, "ext": ext,
                "p1": p["1N2"]["1"], "pN": p["1N2"]["N"], "p2": p["1N2"]["2"],
                "plus25": p["totaux"]["plus_2.5"], "plus35": p["totaux"]["plus_3.5"],
                "moins25": p["totaux"]["moins_2.5"], "btts": p["btts"]["oui"],
                "buts_attendus": p["buts_attendus"]["total"],
            })
    return pd.DataFrame(lignes)


_FILTRES = {
    "favori": lambda t: t.sort_values("p1", ascending=False),
    "over25": lambda t: t.sort_values("plus25", ascending=False),
    "over35": lambda t: t.sort_values("plus35", ascending=False),
    "under25": lambda t: t.sort_values("moins25", ascending=False),
    "btts": lambda t: t.sort_values("btts", ascending=False),
    "aucun": lambda t: t.sort_values("buts_attendus", ascending=False),
}


def _filtrer_journee(tableau: pd.DataFrame, filtre: str) -> pd.DataFrame:
    return _FILTRES.get(filtre, _FILTRES["aucun"])(tableau).reset_index(drop=True)


def commande_forces(args: argparse.Namespace) -> int:
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    modele = entrainer_modele(matchs, args.source, args.championnat)
    print(f"=== Forces estimees — {config.nom_championnat(args.championnat)} ===")
    print(modele.forces().to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\nAvantage du terrain (gamma) : {modele.gamma:.3f}  |  "
          f"correction petits scores (rho) : {modele.rho:.3f}")
    return 0


def commande_cotes(args: argparse.Namespace) -> int:
    try:
        cotes = cotes_module.recuperer(args.championnat, forcer=args.forcer)
    except config.CleApiManquante as erreur:
        print(erreur)
        return 2
    if cotes.empty:
        print("Aucun match a venir cote pour ce championnat.")
        return 0
    print(cotes_module.matchs_a_venir(cotes).to_string(index=False))
    print("\n" + cotes_module.message_quota())
    return 0


def commande_opportunites(args: argparse.Namespace) -> int:
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    modele = entrainer_modele(matchs, args.source, args.championnat)
    try:
        cotes = cotes_module.recuperer(args.championnat, forcer=args.forcer)
    except config.CleApiManquante as erreur:
        print(erreur)
        return 2
    if cotes.empty:
        print("Aucune cote disponible : pas de match a venir, ou quota epuise.")
        return 0

    opportunites = valeur_module.detecter(
        cotes, modele, args.bankroll, a_melange=args.a, seuil_esperance=args.seuil)
    _sauvegarder_opportunites(opportunites)
    print(_affichage_opportunites(opportunites, args))
    return 0


def _sauvegarder_opportunites(opportunites: Sequence[valeur_module.Opportunite]) -> None:
    config.creer_dossiers()
    donnees = [{
        "numero": i, "dom": o.dom, "ext": o.ext, "date": str(o.date), "marche": o.marche,
        "selection": o.selection, "cote": o.cote, "bookmaker": o.bookmaker,
        "p_modele": o.p_modele, "p_marche": o.p_marche, "p_finale": o.p_finale,
        "esperance": o.esperance, "mise": o.mise, "recommandee": o.recommandee,
    } for i, o in enumerate(opportunites, start=1)]
    CHEMIN_OPPORTUNITES.write_text(json.dumps(donnees, indent=2), encoding="utf-8")


def _affichage_opportunites(opportunites: Sequence[valeur_module.Opportunite],
                            args: argparse.Namespace) -> str:
    retenues = [o for o in opportunites if o.recommandee]
    alertes = [o for o in opportunites if o.alerte]
    lignes = [f"=== Opportunites {config.nom_championnat(args.championnat)} "
              f"(seuil {args.seuil:.0%}, bankroll {args.bankroll:.0f}) ==="]
    if not retenues:
        lignes.append("Aucune opportunite ne passe les quatre conditions. "
                      "C'est le cas le plus frequent : le marche est efficace.")
    for numero, o in enumerate(opportunites, start=1):
        if o.recommandee:
            lignes.append(f"[{numero}] " + o.resume())
    if alertes:
        lignes += ["", "--- Signaux d'alerte (divergence modele/marche, PAS des conseils) ---"]
        for o in alertes[:10]:
            lignes.append(f"  {o.dom} - {o.ext} {o.marche} {o.selection} : "
                          f"modele {o.p_modele:.0%} vs marche {o.p_marche:.0%}")
    lignes += ["",
               f"{len(opportunites)} selections evaluees, {len(retenues)} retenue(s). "
               "Les numeros portent sur la liste complete : ils sautent la ou une "
               "selection a ete ecartee.",
               "Pour enregistrer : python cli.py parier --id <numero> --mise <montant>",
               "",
               f"Exposition en cours : {journal_module.exposition():.2f}",
               config.AVERTISSEMENT_LEGAL]
    return "\n".join(lignes)


def commande_parier(args: argparse.Namespace) -> int:
    if not CHEMIN_OPPORTUNITES.exists():
        print("Lance d'abord : python cli.py opportunites --championnat <code>")
        return 2
    donnees = json.loads(CHEMIN_OPPORTUNITES.read_text(encoding="utf-8"))
    choix = next((o for o in donnees if o["numero"] == args.id), None)
    if choix is None:
        print(f"Numero {args.id} absent de la derniere liste d'opportunites "
              f"({len(donnees)} entrees).")
        return 2
    mise = args.mise if args.mise is not None else choix["mise"]
    if mise <= 0:
        print("Mise nulle : ce pari n'etait pas recommande. Precise --mise pour forcer.")
        return 2
    identifiant = journal_module.enregistrer(
        dom=choix["dom"], ext=choix["ext"], marche=choix["marche"],
        selection=choix["selection"], cote_prise=choix["cote"], mise=mise,
        date_match=choix["date"], championnat=args.championnat or "",
        bookmaker=choix["bookmaker"], p_modele=choix["p_modele"],
        p_marche=choix["p_marche"], p_finale=choix["p_finale"],
        esperance=choix["esperance"])
    print(f"Pari #{identifiant} enregistre : {choix['dom']} - {choix['ext']} "
          f"{choix['marche']} {choix['selection']} @ {choix['cote']:.2f}, mise {mise:.2f}")
    return 0


def commande_regler(args: argparse.Namespace) -> int:
    try:
        resultat = journal_module.regler(args.id, args.resultat)
    except (KeyError, ValueError) as erreur:
        print(erreur)
        return 2
    print(f"Pari #{resultat['id']} : {resultat['statut']}, gain {resultat['gain']:+.2f}")
    return 0


def commande_bilan(args: argparse.Namespace) -> int:
    print(journal_module.texte_bilan())
    if args.export:
        print(f"\nExporte vers {journal_module.exporter_csv(Path(args.export))}")
    return 0


def commande_clotures(args: argparse.Namespace) -> int:
    resultat = journal_module.capturer_clotures(rafraichir_api=not args.hors_ligne)
    print(f"Cotes de cloture capturees pour {resultat['traites']} pari(s).")
    if resultat.get("sans_donnees"):
        print(f"  Sans donnees : {len(resultat['sans_donnees'])} pari(s).")
    print("\n" + json.dumps(journal_module.bilan_clv(), indent=2, ensure_ascii=False))
    return 0


def commande_backtest(args: argparse.Namespace) -> int:
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    resultat = backtest_module.backtester(
        matchs, championnat=args.championnat, cible=args.source,
        seuil_esperance=args.seuil, bankroll_initiale=args.bankroll,
        frequence_reentrainement=args.frequence)
    print(resultat.rapport)
    if not resultat.calibration_1n2.empty:
        print("\nCourbe de calibration :")
        print(resultat.calibration_1n2.to_string(index=False))
    return 0


def commande_du_jour(args: argparse.Namespace) -> int:
    """Une selection par jour, toutes ligues, sans jamais consommer de credit API.

    Lit uniquement les cotes deja en cache : le rafraichissement reste un geste
    explicite, ici comme dans l'interface web.
    """
    par_championnat = {}
    sans_cotes = []
    for code in config.CODES_CHAMPIONNATS:
        cotes, age = cotes_module.lire_cache(code)
        if cotes.empty or age is None:
            sans_cotes.append(code)
            continue
        par_championnat[code] = cotes

    if not par_championnat:
        print("Aucune cote en cache. Lance d'abord : python cli.py cotes -c F1")
        return 1

    selections = quotidien_module.du_jour(
        par_championnat, seuil=args.seuil_proba, prime_minimale=args.prime)
    if args.jour:
        selections = [s for s in selections if str(s.date)[:10] == args.jour]

    print(quotidien_module.avertissement())
    print(f"\n{len(par_championnat)} ligue(s) lue(s) en cache"
          + (f", {len(sans_cotes)} sans cotes" if sans_cotes else "") + ".")

    if not selections:
        print("\nAucune selection ne remplit la regle aujourd'hui.")
        print("  C'est frequent et normal : il faut un favori a plus de "
              f"{args.seuil_proba:.0%} ET une cote au moins {args.prime:.0%} au-dessus "
              "de la mediane des books. Ne pas parier est une option.")
        return 0

    montant = quotidien_module.mise(args.bankroll)
    print(f"\n{len(selections)} selection(s) eligible(s), la mieux payee en tete :\n")
    for rang, s in enumerate(selections[:args.nombre], start=1):
        marque = "==>" if rang == 1 else "   "
        print(f"{marque} [{s.championnat}] {s.libelle}")
        print(f"      {str(s.date)[:16]} | marche {s.p_marche:.1%} | cote {s.cote:.2f} "
              f"chez {s.bookmaker} (mediane {s.cote_mediane:.2f}, +{s.prime:.1%})")
        print(f"      {s.nb_bookmakers} bookmakers | mise plate conseillee {montant:.2f}")
    print("\nMise plate volontairement : la probabilite utilisee est celle du marche, "
          "\n  donc Kelly amplifierait un avantage qu'on ne revendique pas.")
    return 0


def commande_valider_annexes(args: argparse.Namespace) -> int:
    """Confronte les modeles corners et cartons aux comptes reellement observes.

    C'est la seule contre-expertise possible sur ces marches : aucun bookmaker
    ne les cote via l'API, donc rien ne peut les contredire en direct. Le
    resultat est enregistre et remplace, dans l'interface, l'etiquette
    « non verifiable » par des chiffres.
    """
    codes = (list(config.CODES_CHAMPIONNATS) if args.championnat.strip() == "*"
             else [args.championnat.upper()])
    if args.forcer:
        print(f"Retelechargement de la saison en cours pour {len(codes)} championnat(s) "
              "— les matchs des dernieres heures seront inclus.\n")

    resume: list[tuple] = []
    code_retour = 0
    for championnat in codes:
        try:
            matchs, _ = charger_donnees(championnat, args.saisons, args.source,
                                        forcer=args.forcer)
        except Exception as erreur:                     # reseau, CSV absent
            print(f"{championnat} : donnees indisponibles ({erreur})")
            resume.append((championnat, None, None))
            code_retour = 1
            continue
        if matchs.empty:
            print(f"{championnat} : aucune donnee.")
            resume.append((championnat, None, None))
            continue

        dernier = pd.to_datetime(matchs["date"]).max()
        print(f"\n{'=' * 70}\n{championnat} — {config.nom_championnat(championnat)} : "
              f"{len(matchs)} matchs, dernier le {dernier:%Y-%m-%d}\n{'=' * 70}")

        verdicts = {}
        for type_compte in annexes_module.TYPES:
            try:
                validation = annexes_module.valider(
                    matchs, type_compte, championnat, taille_initiale=args.initial)
            except annexes_module.DonneesInsuffisantes as erreur:
                print(f"\n{type_compte} : {erreur}")
                verdicts[type_compte] = None
                continue
            verdicts[type_compte] = validation
            print(f"\n{validation.verdict}")
            print(f"  Dispersion hors echantillon : {validation.dispersion:.2f} "
                  "(une loi de Poisson suppose 1.00)")
            print(f"  Erreur absolue moyenne      : {validation.erreur_absolue:.2f}")
            if args.detail:
                print(f"\n  {'ligne':>7s} {'annonce':>9s} {'constate':>9s} {'ecart':>8s}")
                for ligne in validation.calibration:
                    print(f"  {ligne['ligne']:7.1f} {ligne['annonce']:9.1%} "
                          f"{ligne['constate']:9.1%} {ligne['ecart']:+8.1%}")
            annexes_module.enregistrer_validation(validation)
        resume.append((championnat, verdicts.get("corners"), verdicts.get("jaunes")))

    if len(codes) > 1:
        print(f"\n{'=' * 78}\nRECAPITULATIF\n{'=' * 78}")
        print(f"{'ligue':>6s} | {'corners':^34s} | {'cartons jaunes':^34s}")
        print(f"{'':6s} | {'ecart cal.':>10s} {'biais':>14s} {'>moy':>6s} | "
              f"{'ecart cal.':>10s} {'biais':>14s} {'>moy':>6s}")
        for championnat, corners, jaunes in resume:
            cases = []
            for v in (corners, jaunes):
                if v is None:
                    cases.append(f"{'indisponible':>32s}")
                else:
                    biais = (f"{v.biais:+.2f}+/-{v.biais_ic:.2f}"
                             + ("*" if v.biais_significatif else " "))
                    cases.append(f"{v.ecart_calibration_max:10.1%} {biais:>14s} "
                                 f"{'oui' if v.bat_la_moyenne_de_ligue else 'NON':>6s}")
            print(f"{championnat:>6s} | {cases[0]} | {cases[1]}")
        print("\n  ecart cal. = ecart maximal entre probabilite annoncee et frequence reelle")
        print("  biais      = comptes reels moins comptes attendus, par match ; "
              "* = hors intervalle a 95 %")
        print("  >moy       = les forces par equipe battent-elles la simple moyenne "
              "du championnat ?")
    return code_retour


def commande_performance(args: argparse.Namespace) -> int:
    """Mesure ce que la regle de selection a reellement rapporte.

    Le resultat est enregistre pour que l'interface web l'affiche a cote des
    paris qu'elle propose : une esperance annoncee sans son bilan mesure est la
    moitie de l'information, et la moitie flatteuse.
    """
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    mesure = performance_module.mesurer(
        matchs, args.championnat, saisons=args.saisons,
        seuil_esperance=args.seuil, frequence_reentrainement=args.frequence)

    print(f"\n{config.nom_championnat(args.championnat)} — "
          f"{mesure.matchs} matchs testes hors echantillon")
    print(f"  selections analysees   : {mesure.nb_selections}")
    print(f"  paris retenus          : {mesure.nb_paris}")
    print(f"  esperance annoncee     : {mesure.esperance_annoncee:+.2%} par pari")
    print(f"  ROI REELLEMENT realise : {mesure.roi:+.2%} par pari")
    print(f"  intervalle 95 %        : [{mesure.roi_bas:+.2%}, {mesure.roi_haut:+.2%}]")
    print(f"\n  Pour comparaison, sur les memes matchs :")
    print(f"    parier le favori du marche : {mesure.roi_favori_marche:+.2%}")
    print(f"    parier toutes les selections : {mesure.roi_tout_parier:+.2%}")
    print(f"\n  {mesure.verdict}")
    print(f"\nEnregistre dans {performance_module.enregistrer(mesure)}")
    return 0


def commande_calibrer(args: argparse.Namespace) -> int:
    matchs, _ = charger_donnees(args.championnat, args.saisons, args.source)
    _verifier_donnees(matchs, args.championnat)
    resultat = calibration.optimiser_hyperparametres(
        matchs, args.championnat, cible=args.source, utiliser_cache=not args.forcer)
    print(f"\nRetenu : xi={resultat['xi']} w={resultat['w']} "
          f"retrecissement={resultat['regularisation']} "
          f"(log-loss {resultat['log_loss']:.4f})")
    predictions = calibration.predictions_walk_forward(
        matchs, cible=args.source, xi=resultat["xi"], poids_xg=resultat["w"],
        frequence_reentrainement=5, regularisation=resultat["regularisation"])
    if not predictions.empty:
        melange = calibration.calibrer_poids_melange(predictions)
        chemin = calibration.enregistrer_poids_melange(args.championnat, melange)
        print(f"\nPoids retenu a={melange['a']:.2f}, enregistre dans {chemin}")
        if melange["a"] <= 0.05:
            print("  Consequence : le modele ne pesera plus rien face au marche, "
                  "et la plupart des paris proposes vont disparaitre. C'est le "
                  "resultat du backtest, pas un reglage a contourner.")
    return 0


def commande_diagnostic(_: argparse.Namespace) -> int:
    print(f"Dossier de donnees : {config.DOSSIER_DONNEES}")
    print(f"Base SQLite        : {config.CHEMIN_DB}")
    print(f"Fichier .env       : {config.CHEMIN_ENV} "
          f"({'present' if config.CHEMIN_ENV.exists() else 'ABSENT'})")
    for service, presente in config.cles_disponibles().items():
        print(f"  cle {service:20s} : {'configuree' if presente else 'manquante'}")
    print("\n" + cotes_module.message_quota())
    return 0


# --------------------------------------------------------------------------
# Analyseur d'arguments
# --------------------------------------------------------------------------

def _ajouter_options_donnees(sous: argparse.ArgumentParser, saisons: int = 3) -> None:
    sous.add_argument("--championnat", "-c", default="F1",
                      help="code football-data : " + ", ".join(config.CODES_CHAMPIONNATS))
    sous.add_argument("--saisons", "-s", type=int, default=saisons,
                      help="nombre de saisons d'historique")
    sous.add_argument("--source", default="buts", choices=("buts", "xg", "melange"),
                      help="donnees d'entrainement du modele")


def _sous_commandes_analyse(sous_analyseurs) -> None:
    """Sous-commandes qui ne font que lire et calculer."""
    p = sous_analyseurs.add_parser("charger", help="telecharger et resumer un championnat")
    _ajouter_options_donnees(p)
    p.set_defaults(fonction=commande_charger)

    p = sous_analyseurs.add_parser("match", help="rapport complet sur une affiche")
    _ajouter_options_donnees(p)
    p.add_argument("--dom", required=True, help="equipe a domicile")
    p.add_argument("--ext", required=True, help="equipe a l'exterieur")
    p.add_argument("--cote", type=float, nargs=3, metavar=("C1", "CN", "C2"),
                   help="cotes 1 N 2 pour comparer au modele")
    p.set_defaults(fonction=commande_match)

    p = sous_analyseurs.add_parser("journee", help="toutes les confrontations, triees")
    _ajouter_options_donnees(p)
    p.add_argument("--filtre", default="aucun", choices=tuple(_FILTRES))
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--export", help="chemin d'un CSV de sortie")
    p.set_defaults(fonction=commande_journee)

    p = sous_analyseurs.add_parser("forces", help="classement attaque / defense / note")
    _ajouter_options_donnees(p)
    p.set_defaults(fonction=commande_forces)


def _sous_commandes_paris(sous_analyseurs) -> None:
    """Sous-commandes qui touchent aux cotes et au journal."""
    p = sous_analyseurs.add_parser("cotes", help="cotes des matchs a venir")
    p.add_argument("--championnat", "-c", default="F1")
    p.add_argument("--forcer", action="store_true", help="ignorer le cache de 15 min")
    p.set_defaults(fonction=commande_cotes)

    p = sous_analyseurs.add_parser("opportunites", help="detecter la valeur et la mise")
    _ajouter_options_donnees(p)
    p.add_argument("--seuil", type=float, default=config.SEUIL_ESPERANCE_DEFAUT)
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--a", type=float, default=0.3, help="poids du modele dans le melange")
    p.add_argument("--forcer", action="store_true")
    p.set_defaults(fonction=commande_opportunites)

    p = sous_analyseurs.add_parser("parier", help="enregistrer une opportunite au journal")
    p.add_argument("--id", type=int, required=True, help="numero affiche par 'opportunites'")
    p.add_argument("--mise", type=float, help="par defaut, la mise conseillee")
    p.add_argument("--championnat", "-c", default="")
    p.set_defaults(fonction=commande_parier)

    p = sous_analyseurs.add_parser("regler", help="solder un pari du journal")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--resultat", required=True,
                   choices=("gagne", "perdu", "rembourse", "annule"))
    p.set_defaults(fonction=commande_regler)

    p = sous_analyseurs.add_parser("bilan", help="P&L, ROI, CLV")
    p.add_argument("--export", help="chemin d'un CSV de sortie")
    p.set_defaults(fonction=commande_bilan)

    p = sous_analyseurs.add_parser("clotures", help="capturer les cotes de cloture (CLV)")
    p.add_argument("--hors-ligne", action="store_true",
                   help="n'appelle pas l'API, utilise les cotes deja historisees")
    p.set_defaults(fonction=commande_clotures)


def _sous_commandes_evaluation(sous_analyseurs) -> None:
    """Sous-commandes qui evaluent le systeme lui-meme."""
    p = sous_analyseurs.add_parser(
        "valider-annexes", help="confronter les modeles corners et cartons aux comptes reels")
    _ajouter_options_donnees(p, saisons=4)
    p.add_argument("--initial", type=int, default=200,
                   help="matchs minimum avant la premiere prediction")
    p.add_argument("--forcer", "-f", action="store_true",
                   help="retelecharger la saison en cours (inclut les matchs du jour)")
    p.add_argument("--tous", dest="championnat", action="store_const", const="*",
                   help="valider tous les championnats (equivaut a -c '*')")
    p.add_argument("--detail", action="store_true",
                   help="afficher le tableau de calibration ligne par ligne")
    p.set_defaults(fonction=commande_valider_annexes, tous_possibles=True)

    p = sous_analyseurs.add_parser(
        "du-jour", help="une selection par jour, toutes ligues, sur le critere mesure")
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--nombre", "-n", type=int, default=3,
                   help="combien de selections afficher (la 1re est celle a jouer)")
    p.add_argument("--jour", help="filtrer sur une date AAAA-MM-JJ")
    p.add_argument("--seuil-proba", type=float, default=quotidien_module.SEUIL_PROBABILITE,
                   dest="seuil_proba")
    p.add_argument("--prime", type=float, default=quotidien_module.PRIME_MINIMALE,
                   help="ecart minimal entre la meilleure cote et la mediane")
    p.set_defaults(fonction=commande_du_jour)

    p = sous_analyseurs.add_parser(
        "performance", help="ce que la regle de selection a reellement rapporte")
    _ajouter_options_donnees(p, saisons=4)
    p.add_argument("--seuil", type=float, default=config.SEUIL_ESPERANCE_DEFAUT)
    p.add_argument("--frequence", type=int, default=5,
                   help="reentrainement toutes les N journees")
    p.set_defaults(fonction=commande_performance)

    p = sous_analyseurs.add_parser("backtest", help="backtest walk-forward complet")
    _ajouter_options_donnees(p, saisons=4)
    p.add_argument("--seuil", type=float, default=config.SEUIL_ESPERANCE_DEFAUT)
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--frequence", type=int, default=3,
                   help="reentrainement toutes les N journees")
    p.set_defaults(fonction=commande_backtest)

    p = sous_analyseurs.add_parser("calibrer", help="choisir xi, w et le poids du melange")
    _ajouter_options_donnees(p, saisons=4)
    p.add_argument("--forcer", action="store_true", help="ignorer le cache de calibration")
    p.set_defaults(fonction=commande_calibrer)

    p = sous_analyseurs.add_parser("diagnostic", help="chemins, cles API, quota")
    p.set_defaults(fonction=commande_diagnostic)


def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="cli.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=config.AVERTISSEMENT_LEGAL)
    analyseur.add_argument("--verbeux", "-v", action="store_true", help="journal detaille")
    sous_analyseurs = analyseur.add_subparsers(dest="commande", required=True)
    _sous_commandes_analyse(sous_analyseurs)
    _sous_commandes_paris(sous_analyseurs)
    _sous_commandes_evaluation(sous_analyseurs)
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    arguments = construire_analyseur().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if arguments.verbeux else logging.WARNING,
        format="%(message)s")
    if hasattr(arguments, "championnat") and arguments.championnat:
        arguments.championnat = arguments.championnat.upper()
        # "*" demande tous les championnats : les commandes qui ne le gerent pas
        # tomberont sur le message d'inconnu comme avant.
        if arguments.championnat == "*" and getattr(arguments, "tous_possibles", False):
            pass
        elif arguments.championnat not in config.CHAMPIONNATS:
            propositions = noms_module.suggerer(arguments.championnat,
                                                config.CODES_CHAMPIONNATS, 3)
            print(f"Championnat inconnu : {arguments.championnat}.")
            print(f"  Codes proches : {', '.join(n for n, _ in propositions)}")
            print(f"  Liste complete : {', '.join(config.CODES_CHAMPIONNATS)}")
            return 2
    try:
        return int(arguments.fonction(arguments))
    except config.CleApiManquante as erreur:
        print(erreur)
        return 2
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
