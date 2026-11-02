"""Correspondance des noms d'equipes entre sources.

C'est la piece qui casse le plus souvent ce genre de projet : "Paris SG" chez
football-data.co.uk, "PSG" chez FBref, "Paris Saint Germain" chez Understat et
chez The Odds API. Un mauvais appariement pollue silencieusement le modele.

Regle absolue de ce module : on n'apparie JAMAIS deux noms sur la seule foi
d'un score de similarite. Soit la correspondance est certaine (table explicite
ou egalite apres normalisation typographique), soit le cas remonte dans une
liste "a verifier" destinee a un humain. Mieux vaut un trou qu'une fausse
correspondance.
"""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable

CHEMIN_TABLE = Path(__file__).resolve().parent / "noms_equipes.json"

SOURCES = ("football_data", "fbref", "understat", "odds_api", "api_football",
           "football_data_org", "espn")

# Seuil au-dela duquel on propose une correspondance a l'humain. En dessous,
# on ne propose meme pas. Dans tous les cas on ne valide jamais tout seul.
SEUIL_SUGGESTION = 0.55

# Mots generiques de club, sans valeur discriminante une fois isoles.
_MOTS_GENERIQUES = frozenset({
    "fc", "cf", "afc", "ac", "as", "sc", "ssc", "asd", "cd", "ud", "sd", "rcd",
    "rc", "aj", "ogc", "losc", "sm", "sco", "fco", "cs", "if", "bk", "sk",
    "club", "calcio", "de", "the", "1", "1899", "bsc", "tsg", "fsv", "vfl",
    "vfb", "sv", "spvgg", "borussia", "stade", "olympique",
})


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

# Fonctions pures, appelees des centaines de milliers de fois par page : chaque
# rencontre du jour d'API-Football est comparee a chaque equipe du modele.
@lru_cache(maxsize=65536)
def normaliser(nom: str) -> str:
    """Forme comparable d'un nom : sans accents, minuscules, sans ponctuation."""
    if nom is None:
        return ""
    texte = unicodedata.normalize("NFKD", str(nom))
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = texte.lower().replace("&", " and ")
    texte = re.sub(r"[^a-z0-9]+", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


@lru_cache(maxsize=65536)
def noyau(nom: str) -> str:
    """Normalisation plus agressive : retire les mots generiques de club."""
    mots = normaliser(nom).split()
    utiles = [m for m in mots if m not in _MOTS_GENERIQUES]
    return " ".join(utiles) if utiles else " ".join(mots)


def similarite(a: str, b: str) -> float:
    """Score 0-1 combinant ressemblance de chaine et recouvrement de mots."""
    na, nb = normaliser(a), normaliser(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ka, kb = noyau(a), noyau(b)
    if ka == kb:
        return 0.99
    chaine = SequenceMatcher(None, ka, kb).ratio()
    mots_a, mots_b = set(ka.split()), set(kb.split())
    recouvrement = len(mots_a & mots_b) / max(len(mots_a | mots_b), 1)
    inclusion = 1.0 if (ka in kb or kb in ka) else 0.0
    return max(0.6 * chaine + 0.4 * recouvrement, 0.9 * inclusion)


# --------------------------------------------------------------------------
# Chargement de la table
# --------------------------------------------------------------------------

_table: dict | None = None
_index: dict[str, dict[str, str]] | None = None


def charger_table(forcer: bool = False) -> dict:
    """Contenu brut de noms_equipes.json."""
    global _table, _index
    if _table is None or forcer:
        if CHEMIN_TABLE.exists():
            _table = json.loads(CHEMIN_TABLE.read_text(encoding="utf-8"))
        else:
            _table = {"version": 1, "equipes": {}}
        _index = None
    return _table


def _construire_index() -> dict[str, dict[str, str]]:
    """index[source][forme normalisee] -> nom canonique."""
    global _index
    if _index is not None:
        return _index
    table = charger_table()
    index: dict[str, dict[str, str]] = {source: {} for source in (*SOURCES, "canonique")}
    for canonique, alias_par_source in table.get("equipes", {}).items():
        index["canonique"][normaliser(canonique)] = canonique
        for source, alias in alias_par_source.items():
            cible = index.setdefault(source, {})
            cible[normaliser(canonique)] = canonique
            for nom in alias:
                cible[normaliser(nom)] = canonique
    _index = index
    return index


def noms_canoniques() -> list[str]:
    return sorted(charger_table().get("equipes", {}))


def alias_connus(canonique: str, source: str | None = None) -> list[str]:
    entree = charger_table().get("equipes", {}).get(canonique, {})
    if source is None:
        return sorted({a for liste in entree.values() for a in liste})
    return sorted(entree.get(source, []))


# --------------------------------------------------------------------------
# Canonisation
# --------------------------------------------------------------------------

def canoniser(nom: str, source: str = "football_data", identite_si_inconnu: bool = False) -> str | None:
    """Nom canonique d'une equipe, ou None si la table ne le connait pas.

    Aucune approximation : seules l'egalite exacte et l'egalite apres
    normalisation typographique sont acceptees. `identite_si_inconnu` renvoie
    le nom d'origine plutot que None, ce qui laisse un "trou" propre : l'equipe
    reste identifiable mais ne sera appariee a aucune autre source.
    """
    if nom is None or str(nom).strip() == "":
        return None
    index = _construire_index()
    cle = normaliser(nom)
    trouve = index.get(source, {}).get(cle) or index["canonique"].get(cle)
    if trouve is None:
        # Un alias declare pour une autre source reste une information sure.
        for autre in SOURCES:
            trouve = index.get(autre, {}).get(cle)
            if trouve:
                break
    if trouve is not None:
        return trouve
    return str(nom).strip() if identite_si_inconnu else None


def canoniser_liste(
    noms: Iterable[str], source: str = "football_data"
) -> tuple[dict[str, str], list[str]]:
    """Renvoie (correspondances trouvees, noms inconnus)."""
    correspondances: dict[str, str] = {}
    inconnus: list[str] = []
    for nom in dict.fromkeys(noms):
        canonique = canoniser(nom, source)
        if canonique is None:
            inconnus.append(nom)
        else:
            correspondances[nom] = canonique
    return correspondances, inconnus


def resoudre_vers(nom: str, candidats: Iterable[str]) -> str | None:
    """Fait correspondre un nom a UN candidat d'une liste, sans jamais deviner.

    Quatre niveaux, tous deterministes, du plus strict au moins strict :
      1. egalite exacte ;
      2. meme nom canonique dans data/noms_equipes.json (c'est elle qui sait que
         "ADO Den Haag" cote par un book est le "Den Haag" de football-data) ;
      3. egalite apres normalisation typographique (accents, casse, ponctuation) ;
      4. egalite apres retrait des mots generiques de club ("FC Utrecht" et
         "Utrecht" donnent tous deux "utrecht").

    Le niveau 3 n'est accepte que s'il designe UN SEUL candidat. Des que deux
    candidats se ramenent a la meme forme, on renvoie None : une ambiguite doit
    remonter, pas se resoudre au hasard. Aucun score de similarite n'intervient.
    """
    candidats = list(candidats)
    if nom in candidats:
        return nom

    # Niveau 1 bis : passer par la table de correspondance. C'est elle qui sait
    # que "ADO Den Haag" (cote) et "Den Haag" (football-data) sont le meme club.
    canonique = canoniser(nom)
    if canonique is not None:
        memes = [c for c in candidats if canoniser(c) == canonique]
        if len(memes) == 1:
            return memes[0]
        if memes:
            return None

    cible = normaliser(nom)
    exacts = [c for c in candidats if normaliser(c) == cible]
    if len(exacts) == 1:
        return exacts[0]
    if exacts:
        return None  # homonymes stricts : on ne tranche pas
    cible_noyau = noyau(nom)
    proches = [c for c in candidats if noyau(c) == cible_noyau]
    return proches[0] if len(proches) == 1 else None


def suggerer(nom: str, candidats: Iterable[str], nombre: int = 3) -> list[tuple[str, float]]:
    """Les `nombre` candidats les plus proches, tries par score decroissant.

    Sert a proposer une correction a l'utilisateur, jamais a decider.
    """
    scores = [(candidat, similarite(nom, candidat)) for candidat in candidats]
    scores.sort(key=lambda couple: couple[1], reverse=True)
    return scores[:nombre]


# --------------------------------------------------------------------------
# Rapprochement de deux sources
# --------------------------------------------------------------------------

def _separer_connus(noms_source, source: str) -> tuple[dict[str, str], list[str]]:
    """Separe les noms deja presents dans la table de ceux qui n'y sont pas."""
    connus: dict[str, str] = {}
    inconnus: list[str] = []
    for nom in noms_source:
        canonique = canoniser(nom, source)
        if canonique is None:
            inconnus.append(nom)
        else:
            connus[canonique] = nom
    return connus, inconnus


def _proposer(restants_a: list[str], restants_b: list[str]) -> tuple[list, list, list]:
    """Propose des rapprochements a valider par un humain, sans jamais decider."""
    a_verifier: list[tuple[str, str, float]] = []
    orphelins_a: list[str] = []
    encore_b = list(restants_b)
    for nom_a in restants_a:
        if not encore_b:
            orphelins_a.append(nom_a)
            continue
        meilleur, score = max(
            ((nom_b, similarite(nom_a, nom_b)) for nom_b in encore_b),
            key=lambda couple: couple[1],
        )
        if score >= SEUIL_SUGGESTION:
            a_verifier.append((nom_a, meilleur, round(score, 3)))
            encore_b.remove(meilleur)
        else:
            orphelins_a.append(nom_a)
    a_verifier.sort(key=lambda t: t[2], reverse=True)
    return a_verifier, orphelins_a, encore_b


def rapprocher(
    liste_a: Iterable[str],
    liste_b: Iterable[str],
    source_a: str = "football_data",
    source_b: str = "fbref",
) -> dict[str, list]:
    """Compare deux listes d'equipes issues de deux sources differentes.

    Renvoie un dictionnaire a quatre entrees :
      - "surs"       : [(nom_a, nom_b, canonique)] appariements certains
      - "a_verifier" : [(nom_a, nom_b, score)] propositions a valider a la main
      - "orphelins_a" / "orphelins_b" : noms sans aucune piste
    Rien n'est apparie en silence sur un score de similarite.
    """
    liste_a = list(dict.fromkeys(liste_a))
    liste_b = list(dict.fromkeys(liste_b))

    canon_b, restants_b = _separer_connus(liste_b, source_b)
    surs: list[tuple[str, str, str]] = []
    restants_a: list[str] = []
    for nom in liste_a:
        canonique = canoniser(nom, source_a)
        if canonique is not None and canonique in canon_b:
            surs.append((nom, canon_b.pop(canonique), canonique))
        else:
            restants_a.append(nom)

    # Les equipes connues cote B mais absentes cote A restent orphelines.
    restants_b.extend(canon_b.values())
    a_verifier, orphelins_a, encore_b = _proposer(restants_a, restants_b)
    return {
        "surs": surs,
        "a_verifier": a_verifier,
        "orphelins_a": sorted(orphelins_a),
        "orphelins_b": sorted(encore_b),
    }


def ajouter_alias(canonique: str, source: str, alias: str, ecrire: bool = True) -> None:
    """Enregistre un alias valide par un humain dans la table."""
    if source not in SOURCES:
        raise ValueError(f"Source inconnue : {source!r}. Sources : {', '.join(SOURCES)}")
    table = charger_table()
    equipes = table.setdefault("equipes", {})
    entree = equipes.setdefault(canonique, {})
    liste = entree.setdefault(source, [])
    if alias not in liste and normaliser(alias) != normaliser(canonique):
        liste.append(alias)
        liste.sort()
    global _index
    _index = None  # l'index sera reconstruit avec le nouvel alias
    if ecrire:
        CHEMIN_TABLE.write_text(
            json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def taux_appariement(noms: Iterable[str], source: str = "football_data") -> float:
    """Part des noms reconnus par la table, entre 0 et 1."""
    noms = list(dict.fromkeys(noms))
    if not noms:
        return 1.0
    connus = sum(1 for nom in noms if canoniser(nom, source) is not None)
    return connus / len(noms)
