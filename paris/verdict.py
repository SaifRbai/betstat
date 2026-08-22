"""Confronter un prono au resultat reel, une fois le match joue.

Un outil qui annonce des probabilites sans jamais montrer ce qu'elles ont donne
demande une confiance qu'il ne justifie pas. Ce module fait le seul geste qui
la justifie : il rejoue chaque prono affiche contre le score final.

Trois etats, et le troisieme compte autant que les deux autres :

  JUSTE     le prono s'est realise ;
  RATE      il ne s'est pas realise ;
  INCONNU   on ne peut pas trancher — le resultat n'est pas encore publie, ou
            la colonne necessaire manque (football-data ne remplit pas les
            corners partout). Un prono non verifiable ne doit jamais etre
            compte comme juste : c'est ainsi qu'on se ment sur un bilan.

Un verdict sur UN match ne dit rien de la qualite du modele : une estimation a
80 % doit se tromper une fois sur cinq. C'est ce que rappelle `nuance()`. La
mesure serieuse est ailleurs — `paris/performance.py`, sur des centaines de
paris hors echantillon.
"""

from __future__ import annotations

from typing import Any

JUSTE = "juste"
RATE = "rate"
INCONNU = "inconnu"


def _nombre(resultat: dict, cle: str) -> float | None:
    valeur = resultat.get(cle)
    if valeur is None:
        return None
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if nombre != nombre else nombre        # NaN


def _buts(resultat: dict) -> tuple[float, float] | None:
    dom, ext = _nombre(resultat, "buts_dom"), _nombre(resultat, "buts_ext")
    return None if dom is None or ext is None else (dom, ext)


def _total(resultat: dict, prefixe: str) -> float | None:
    dom = _nombre(resultat, f"{prefixe}_dom")
    ext = _nombre(resultat, f"{prefixe}_ext")
    return None if dom is None or ext is None else dom + ext


def issue_1n2(resultat: dict) -> str | None:
    """« 1 », « N » ou « 2 » selon le score final."""
    buts = _buts(resultat)
    if buts is None:
        return None
    dom, ext = buts
    return "1" if dom > ext else ("2" if ext > dom else "N")


def verifier(marche: str, selection: str, resultat: dict) -> str:
    """Verdict d'un prono (marche, selection) face au resultat.

    `marche` est celui produit par `paris.confiance` : « 1N2 », « double
    chance », « btts », « clean sheet », « totaux 2.5 », « corners »,
    « cartons jaunes ». Un marche inconnu renvoie INCONNU plutot que de lever :
    ajouter un marche a l'affichage ne doit pas casser la page.
    """
    marche = (marche or "").strip().lower()
    selection = (selection or "").strip()

    if marche == "1n2":
        issue = issue_1n2(resultat)
        return INCONNU if issue is None else (JUSTE if issue == selection else RATE)

    if marche in ("double chance", "double_chance"):
        issue = issue_1n2(resultat)
        if issue is None:
            return INCONNU
        # « 12 » se lit « pas de match nul » : les deux issues non nulles.
        return JUSTE if issue in set(selection) else RATE

    if marche == "btts":
        buts = _buts(resultat)
        if buts is None:
            return INCONNU
        les_deux = buts[0] > 0 and buts[1] > 0
        return JUSTE if les_deux == (selection == "oui") else RATE

    if marche in ("clean sheet", "clean_sheet"):
        buts = _buts(resultat)
        if buts is None:
            return INCONNU
        encaisse = buts[1] if selection == "dom" else buts[0]
        return JUSTE if encaisse == 0 else RATE

    if marche.startswith("totaux"):
        seuil = _seuil(marche)
        buts = _buts(resultat)
        if seuil is None or buts is None:
            return INCONNU
        return _plus_ou_moins(buts[0] + buts[1], seuil, selection)

    return INCONNU


def verifier_compte(libelle_compte: str, selection: str, ligne: float,
                    resultat: dict) -> str:
    """Verdict d'un prono corners ou cartons, dont le seuil est porte a part.

    Ces marches n'ont pas de cote : leur verdict est la seule contradiction
    qu'ils puissent recevoir, ce qui les rend plus utiles ici qu'ailleurs.
    """
    prefixes = {"corners": "corners", "cartons jaunes": "jaunes"}
    prefixe = prefixes.get((libelle_compte or "").strip().lower())
    if prefixe is None:
        return INCONNU
    total = _total(resultat, prefixe)
    return INCONNU if total is None else _plus_ou_moins(total, float(ligne), selection)


def verifier_duel(selection: str, libelle_compte: str, resultat: dict) -> str:
    """« Qui en obtient le plus » : 1 pour le domicile, 2 pour l'exterieur."""
    prefixes = {"corners": "corners", "cartons jaunes": "jaunes"}
    prefixe = prefixes.get((libelle_compte or "").strip().lower())
    if prefixe is None:
        return INCONNU
    dom = _nombre(resultat, f"{prefixe}_dom")
    ext = _nombre(resultat, f"{prefixe}_ext")
    if dom is None or ext is None:
        return INCONNU
    reel = "1" if dom > ext else ("2" if ext > dom else "N")
    return JUSTE if reel == selection else RATE


def _seuil(marche: str) -> float | None:
    morceaux = marche.split()
    try:
        return float(morceaux[-1])
    except (IndexError, ValueError):
        return None


def _plus_ou_moins(total: float, seuil: float, selection: str) -> str:
    """Les seuils sont en demis (2.5, 8.5) : aucune egalite possible."""
    depasse = total > seuil
    if selection == "plus":
        return JUSTE if depasse else RATE
    if selection == "moins":
        return RATE if depasse else JUSTE
    return INCONNU


def nuance(p_modele: float | None, verdict: str) -> str | None:
    """Ce qu'un verdict isole ne prouve pas.

    Un prono a 80 % qui echoue n'est pas une erreur du modele : il doit echouer
    une fois sur cinq. Sans ce rappel, une pastille rouge se lit comme une faute
    et une verte comme une preuve — les deux sont fausses.
    """
    if p_modele is None or verdict == INCONNU:
        return None
    sur = 1.0 / (1.0 - p_modele) if verdict == RATE and p_modele < 1 else (
        1.0 / p_modele if p_modele > 0 else None)
    if sur is None:
        return None
    if verdict == RATE:
        return (f"annonce a {p_modele:.0%} : ce prono devait echouer environ "
                f"1 fois sur {sur:.0f}")
    return (f"annonce a {p_modele:.0%} : il devait se realiser environ "
            f"{p_modele:.0%} du temps")


def bilan(verdicts: list[str]) -> dict[str, Any]:
    """Compte des verdicts d'un match. Les inconnus restent a part."""
    return {
        "juste": verdicts.count(JUSTE),
        "rate": verdicts.count(RATE),
        "inconnu": verdicts.count(INCONNU),
        "verifiables": verdicts.count(JUSTE) + verdicts.count(RATE),
    }
