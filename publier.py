#!/usr/bin/env python3
"""Publie une photographie des pronos en site statique, lisible sans le PC.

Le serveur de web.py est demarre en memoire, interroge sur toutes les adresses
que la page appelle, et chaque reponse est ecrite telle quelle dans un fichier
JSON. Meme code, memes validations : la version publiee ne peut pas diverger
de ce qu'affiche le PC. La page, servie depuis ces fichiers, bascule en mode
lecture seule (voir `FOOT_EDGE_STATIQUE` dans web/page.html).

Ce que la photographie ne peut pas faire, et qui est assume :
  - la bankroll et la mise sont fixees (1000 et 10) : les mises affichees
    sont calculees pour ces valeurs-la ;
  - l'objectif du combine du jour est arrondi au palier calcule le plus proche ;
  - aucun rafraichissement des cotes depuis la page : c'est ce script qui le
    fait, une fois par passage, dans la limite d'un budget de credits.

    python publier.py                       # sans depenser de credit
    python publier.py --credits 12          # rafraichit 6 ligues au plus
    python publier.py --sortie public       # dossier a publier (defaut)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import urlopen

import config
import web
from data import cotes as cotes_module

_journal = logging.getLogger("foot_edge.publier")

BANKROLL = 1000
MISE = 10
# Paliers d'objectif du combine du jour. La page arrondit au plus proche :
# le meme tableau est ecrit dans la page, il doit rester identique.
CIBLES = (2, 3, 5, 9, 15, 25, 50, 100)
JOURS_A_VENIR = 7
CREDITS_PAR_LIGUE = 2
# Parametres qui ne font pas partie du nom de fichier : fixes en lecture seule.
PARAMETRES_FIXES = {"bankroll", "mise"}


def cle(adresse: str) -> str:
    """Nom du fichier d'une adresse /api/... — meme regle que `cleStatique` en JS."""
    route = urlparse(adresse)
    nom = route.path.removeprefix("/api/")
    for parametre, valeur in sorted(parse_qsl(route.query, keep_blank_values=True)):
        if parametre not in PARAMETRES_FIXES:
            nom += f"__{parametre}-{valeur}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", nom) + ".json"


# -- credits ---------------------------------------------------------------

def rafraichir(credits: int) -> list[str]:
    """Rafraichit les ligues les plus utiles dans la limite de `credits`.

    D'abord celles qui jouent aujourd'hui, puis celles sans cache, puis les
    caches les plus vieux. Une ligue en echec ne coute rien de plus et
    n'arrete pas les autres.
    """
    avec_api = [c for c, info in config.CHAMPIONNATS.items() if info.get("odds")]
    du_jour = set(web.ligues_du_jour(web.aujourdhui()))

    def priorite(code: str) -> tuple:
        age = cotes_module.age_du_cache(code)
        return (code not in du_jour, age is not None, -(age or 0))

    faites = []
    for code in sorted(avec_api, key=priorite):
        if credits < CREDITS_PAR_LIGUE:
            break
        try:
            cotes_module.recuperer(code, forcer=True)
            faites.append(code)
        except Exception as erreur:
            _journal.warning("Cotes %s non rafraichies : %s", code, erreur)
        credits -= CREDITS_PAR_LIGUE
    return faites


# -- photographie ----------------------------------------------------------

class Photographe:
    """Interroge le vrai serveur et range chaque reponse sous son nom."""

    def __init__(self, dossier: Path) -> None:
        self.dossier = dossier
        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), web.Gestionnaire)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        self.ecrits = 0

    def prendre(self, chemin: str, **parametres) -> dict:
        adresse = chemin + ("?" + urlencode(parametres) if parametres else "")
        try:
            with urlopen(self.base + adresse, timeout=600) as reponse:
                corps = reponse.read()
        except HTTPError as erreur:             # une erreur est un etat a montrer
            corps = erreur.read()
        (self.dossier / cle(adresse)).write_bytes(corps)
        self.ecrits += 1
        return json.loads(corps)

    def fermer(self) -> None:
        self.serveur.shutdown()


def jours_a_publier(etats: list[dict]) -> list[str]:
    """Les journees presentes dans les matchs, des 7 prochains jours."""
    debut = date.fromisoformat(web.aujourdhui())
    fin = debut + timedelta(days=JOURS_A_VENIR)
    jours = set()
    for etat in etats:
        for match in etat.get("matchs", []) + etat.get("ecartes", []):
            try:
                jour = date.fromisoformat(web._jour_local(match["date"]))
            except (KeyError, TypeError, ValueError):
                continue
            if debut <= jour <= fin:
                jours.add(jour.isoformat())
    return sorted(jours | {debut.isoformat()})


def publier(sortie: Path, credits: int) -> dict:
    if sortie.exists():
        shutil.rmtree(sortie)
    donnees = sortie / "donnees"
    donnees.mkdir(parents=True)
    shutil.copytree(web.DOSSIER_WEB / "icones", sortie / "icones")
    for nom in ("manifest.webmanifest", "sw.js"):
        shutil.copy(web.DOSSIER_WEB / nom, sortie / nom)

    rafraichies = rafraichir(credits) if credits > 0 else []
    debut = time.monotonic()
    photo = Photographe(donnees)
    try:
        ligues = photo.prendre("/api/championnats")["championnats"]
        photo.prendre("/api/apis")
        etats = [photo.prendre("/api/aujourdhui", bankroll=BANKROLL)]
        for ligue in ligues:
            if ligue["age_cotes_minutes"] is not None:
                etats.append(photo.prendre("/api/jour", championnat=ligue["code"],
                                           bankroll=BANKROLL))
        jours = jours_a_publier(etats)
        for jour in [""] + jours:
            photo.prendre("/api/equipes-fortes", mise=MISE, jour=jour)
            for cible in CIBLES:
                photo.prendre("/api/combine-du-jour", cible=cible, mise=MISE,
                              jour=jour, etendus=0)
    finally:
        photo.fermer()

    etat = {
        "genere": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bankroll": BANKROLL, "mise": MISE, "cibles": list(CIBLES),
        "jours": jours, "rafraichies": rafraichies,
    }
    # La page publiee : la meme, plus une ligne qui la met en lecture seule.
    page = (web.DOSSIER_WEB / "page.html").read_text(encoding="utf-8")
    marque = "<script>\n"
    assert marque in page, "page.html : balise <script> introuvable"
    page = page.replace(marque, marque + "window.FOOT_EDGE_STATIQUE = "
                        + json.dumps(etat) + ";\n", 1)
    (sortie / "index.html").write_text(page, encoding="utf-8")
    (sortie / ".nojekyll").touch()               # GitHub Pages sert tout tel quel

    _journal.warning("%d reponses ecrites en %.0f s, ligues rafraichies : %s",
                     photo.ecrits, time.monotonic() - debut,
                     ", ".join(rafraichies) or "aucune")
    return etat


def main(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--sortie", type=Path, default=Path("public"))
    analyseur.add_argument("--credits", type=int, default=0,
                           help="credits The Odds API a depenser (2 par ligue)")
    analyseur.add_argument("--verbeux", "-v", action="store_true")
    args = analyseur.parse_args(arguments)
    logging.basicConfig(level=logging.INFO if args.verbeux else logging.WARNING,
                        format="%(message)s")
    config.creer_dossiers()
    publier(args.sortie, args.credits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
