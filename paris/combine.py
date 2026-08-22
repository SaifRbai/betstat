"""Evaluation honnete d'un combine.

Deux pieges, que la plupart des calculateurs de combine ignorent.

**La correlation.** « Marseille gagne » et « plus de 2.5 buts » dans le meme
match ne sont pas independants : multiplier leurs probabilites donne un chiffre
faux. On passe donc par la matrice de scores du modele, qui porte la loi
jointe exacte : on somme les cases qui satisfont les deux conditions a la fois.
Entre deux matchs differents, l'independance reste une approximation
raisonnable et c'est celle qu'on fait.

**Le recalage sur le marche.** Un pari simple est evalue avec la probabilite
melangee modele/marche, pas avec le modele brut : le modele est plus extreme
que le marche et le melange le calibre. Un combine evalue au modele seul
paraitrait donc bien meilleur que les simples dont il est fait — l'erreur est
grosse, de l'ordre de plusieurs dizaines de points d'esperance. On garde donc
la structure de correlation du modele et on la recale jambe par jambe sur le
melange :

    p = p_jointe_modele x produit(p_finale_i / p_modele_i)

**La marge composee.** Chaque jambe porte la marge du bookmaker. Sur quatre
jambes a 5 %, il reste environ 19 % de marge contre le parieur. Un combine ne
transforme pas quatre paris mediocres en un bon ; il multiplie les cotes et les
marges en meme temps. La fonction renvoie de quoi le voir en face : la
probabilite reelle que tout passe, la cote equitable, et l'ecart avec la cote
offerte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

import config
from paris import valeur as valeur_module


@dataclass(frozen=True)
class Jambe:
    """Une selection du combine."""

    dom: str
    ext: str
    marche: str
    selection: str
    cote: float
    p_finale: float | None = None
    """Probabilite melangee modele/marche de cette jambe, si elle est connue.

    C'est celle qui sert aux paris simples. La fournir rend le combine
    comparable a eux ; sans elle on retombe sur le modele seul, plus extreme.
    """

    @property
    def match(self) -> tuple[str, str]:
        return (self.dom, self.ext)

    def libelle(self) -> str:
        return f"{self.dom} - {self.ext} : {self.marche} {self.selection} @ {self.cote:.2f}"


@dataclass
class Combine:
    """Resultat d'une evaluation, tout en clair."""

    jambes: list[Jambe]
    cote_totale: float
    p_modele: float
    """Probabilite retenue : recalee sur le marche quand c'est possible."""
    p_modele_seul: float
    """La meme, au modele brut, sans recalage. Pour comparaison."""
    p_independance: float
    cote_equitable: float
    esperance: float
    mise: float
    fraction: float
    p_par_jambe: list[float] = field(default_factory=list)
    correlations: list[str] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)

    @property
    def retenu(self) -> bool:
        return not self.motifs

    def resume(self) -> str:
        lignes = [f"Combine a {len(self.jambes)} jambe(s)"]
        lignes += [f"   {j.libelle()}" for j in self.jambes]
        lignes += [
            f"Cote totale     : {self.cote_totale:.2f}",
            f"Probabilite que TOUT passe : {self.p_modele:.1%}",
            f"   (modele seul, sans recalage : {self.p_modele_seul:.1%})",
            f"Cote equitable  : {self.cote_equitable:.2f}",
            f"Esperance       : {self.esperance:+.1%}",
            f"Mise conseillee : {self.mise:.2f}",
        ]
        lignes += [f"   {m}" for m in self.correlations]
        lignes += [f"   rejet : {m}" for m in self.motifs]
        return "\n".join(lignes)


# --------------------------------------------------------------------------
# Probabilite jointe
# --------------------------------------------------------------------------

def masque(matrice: np.ndarray, marche: str, selection: str) -> np.ndarray:
    """Cases de la matrice de scores ou la selection est gagnante.

    La matrice est indexee [buts domicile, buts exterieur].
    """
    taille = matrice.shape[0]
    dom, ext = np.indices((taille, taille))
    if marche == "1N2":
        return {"1": dom > ext, "N": dom == ext, "2": dom < ext}[selection]
    if marche.startswith("plus_moins_"):
        seuil = float(marche.rsplit("_", 1)[1])
        total = dom + ext
        return total > seuil if selection == "plus" else total < seuil
    raise ValueError(
        f"Marche non gere dans un combine : {marche!r}. "
        "Geres : 1N2 et plus_moins_<seuil>.")


def probabilite_jointe(modele, jambes: Sequence[Jambe]) -> tuple[float, float, list[str]]:
    """(probabilite reelle, probabilite si on supposait l'independance, notes).

    Les jambes d'un meme match passent par la loi jointe ; les matchs entre eux
    sont supposes independants.
    """
    par_match: dict[tuple[str, str], list[Jambe]] = {}
    for jambe in jambes:
        par_match.setdefault(jambe.match, []).append(jambe)

    reelle, independante, notes = 1.0, 1.0, []
    for (dom, ext), groupe in par_match.items():
        matrice = modele.matrice_scores(dom, ext)
        commun = np.ones(matrice.shape, dtype=bool)
        produit = 1.0
        for jambe in groupe:
            cases = masque(matrice, jambe.marche, jambe.selection)
            commun &= cases
            produit *= float(matrice[cases].sum())
        jointe = float(matrice[commun].sum())
        reelle *= jointe
        independante *= produit
        if len(groupe) > 1:
            notes.append(_note_correlation(dom, ext, jointe, produit))
    return reelle, independante, notes


def _note_correlation(dom: str, ext: str, jointe: float, produit: float) -> str:
    if produit <= 0:
        return f"{dom} - {ext} : jambes incompatibles, le combine ne peut pas passer."
    ecart = jointe / produit
    if ecart > 1.05:
        sens = "se renforcent"
    elif ecart < 0.95:
        sens = "se genent"
    else:
        sens = "sont presque independantes"
    return (f"{dom} - {ext} : deux jambes du meme match, elles {sens} "
            f"({jointe:.1%} en realite, contre {produit:.1%} si on les multipliait).")


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

MAX_JAMBES_RAISONNABLE = 3


def evaluer(modele, jambes: Sequence[Jambe], bankroll: float,
            seuil_esperance: float = config.SEUIL_ESPERANCE_DEFAUT) -> Combine:
    """Evalue un combine et dit pourquoi il est retenu ou non.

    Comme pour un pari simple, on renvoie toujours un objet complet : les
    motifs de rejet sont une information, pas un booleen a cacher.
    """
    jambes = list(jambes)
    if not jambes:
        raise ValueError("Un combine demande au moins une selection.")
    doublons = _doublons(jambes)
    if doublons:
        raise ValueError(f"La meme selection apparait deux fois : {doublons}.")

    cote_totale = float(np.prod([j.cote for j in jambes]))
    p_modele, p_independance, correlations = probabilite_jointe(modele, jambes)
    p_par_jambe = probabilites_par_jambe(modele, jambes)
    p_retenue = _recaler(p_modele, jambes, p_par_jambe)
    esperance = p_retenue * cote_totale - 1.0
    fraction, mise = valeur_module.mise_conseillee(p_retenue, cote_totale, bankroll)

    motifs = []
    if esperance <= seuil_esperance:
        motifs.append(f"esperance {esperance:+.1%} sous le seuil de {seuil_esperance:.1%}")
    if len(jambes) > MAX_JAMBES_RAISONNABLE:
        motifs.append(
            f"{len(jambes)} jambes : chacune porte la marge du bookmaker et elles se "
            f"multiplient. Au-dela de {MAX_JAMBES_RAISONNABLE}, le combine travaille "
            "surtout pour le book")
    if mise <= 0:
        motifs.append("Kelly nul ou negatif : aucune mise ne se justifie")

    return Combine(
        jambes=jambes, cote_totale=cote_totale, p_modele=p_retenue,
        p_modele_seul=p_modele,
        p_independance=p_independance,
        cote_equitable=(1.0 / p_retenue) if p_retenue > 0 else float("inf"),
        esperance=esperance, mise=mise if not motifs else 0.0, fraction=fraction,
        p_par_jambe=p_par_jambe, correlations=correlations, motifs=motifs)


def probabilites_par_jambe(modele, jambes: Sequence[Jambe]) -> list[float]:
    """Probabilite de chaque jambe prise isolement, selon le modele.

    Utile en face de la probabilite jointe : c'est ce qui rend visible ce que
    le combine coute. Trois jambes a 60 % font un combine a 21 %.
    """
    return [float(modele.matrice_scores(j.dom, j.ext)[
        masque(modele.matrice_scores(j.dom, j.ext), j.marche, j.selection)].sum())
        for j in jambes]


def _recaler(p_jointe: float, jambes: Sequence[Jambe],
             p_par_jambe: Sequence[float]) -> float:
    """Recale la probabilite jointe sur le melange modele/marche.

    Sans les p_finale des jambes, on ne peut pas recaler : on rend la
    probabilite du modele telle quelle, et l'appelant doit savoir qu'elle est
    plus optimiste que ce qu'un pari simple afficherait.
    """
    if any(j.p_finale is None for j in jambes):
        return p_jointe
    facteur = 1.0
    for jambe, p_modele in zip(jambes, p_par_jambe):
        if p_modele <= 0:
            return 0.0
        facteur *= jambe.p_finale / p_modele
    return float(min(max(p_jointe * facteur, 0.0), 1.0))


def _doublons(jambes: Sequence[Jambe]) -> str:
    vues, doubles = set(), []
    for jambe in jambes:
        cle = (jambe.dom, jambe.ext, jambe.marche, jambe.selection)
        (doubles.append(jambe.libelle()) if cle in vues else vues.add(cle))
    return " ; ".join(doubles)
