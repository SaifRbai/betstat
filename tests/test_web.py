"""Tests de l'interface web, sans reseau et sans serveur d'affichage.

Le serveur n'ecoute que sur 127.0.0.1 : le demarrer dans un test ne sort pas
de la machine. Les donnees, elles, sont des bouchons — aucun telechargement.
"""

from __future__ import annotations

import json
import pathlib
import re
import threading
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

import config
import web

RACINE = pathlib.Path(__file__).resolve().parent.parent
JOUR_AUJOURDHUI = web.aujourdhui()
DATE_AUJOURDHUI = date.fromisoformat(JOUR_AUJOURDHUI)
JOUR_DIMANCHE = (
    DATE_AUJOURDHUI + timedelta(days=6 - DATE_AUJOURDHUI.weekday())
).isoformat()
JOUR_DEMAIN = (
    DATE_AUJOURDHUI + timedelta(days=1)
    if DATE_AUJOURDHUI.weekday() < 6 else DATE_AUJOURDHUI
).isoformat()
JOUR_HIER = (DATE_AUJOURDHUI - timedelta(days=1)).isoformat()
JOUR_LUNDI_PROCHAIN = (
    DATE_AUJOURDHUI + timedelta(days=7 - DATE_AUJOURDHUI.weekday())
).isoformat()
HORODATAGE_PASSE = (
    pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=1)
).isoformat()


class FauxModele:
    """Repond comme un ModeleDixonColes, sans rien calculer."""

    equipes = ["Paris SG", "Marseille", "Lens"]

    def matrice_scores(self, dom: str, ext: str, taille=None):
        matrice = np.full((4, 4), 1 / 16)
        return matrice

    def predire(self, dom: str, ext: str) -> dict:
        return {
            "1N2": {"1": 0.5, "N": 0.3, "2": 0.2},
            "totaux": {"plus_2.5": 0.55},
            "btts": {"oui": 0.52},
            "buts_attendus": {"total": 2.7},
        }


COTES = pd.DataFrame({
    "date": [f"{JOUR_DEMAIN}T18:45:00Z", f"{JOUR_DIMANCHE}T18:45:00Z"],
    "dom": ["Paris SG", "Le Mans"], "ext": ["Marseille", "Lens"],
    "bookmaker": ["b1", "b1"]})


@pytest.fixture()
def bouchons(monkeypatch):
    monkeypatch.setattr(web.CACHE, "modele", lambda c: (pd.DataFrame(), FauxModele()))
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (COTES, 12.0))
    monkeypatch.setattr(web.valeur_module, "detecter", lambda *a, **k: [])
    monkeypatch.setattr(web.journal_module, "exposition", lambda: 0.0)


# --------------------------------------------------------------------------
# Donnees servies
# --------------------------------------------------------------------------

def test_seules_les_ligues_avec_un_programme_sont_proposees():
    """Une division sans cotes ni calendrier (National League, D3 ecossaise)
    n'existe ici que comme historique du modele inter-divisions. La proposer
    promettrait des matchs qu'on ne pourrait jamais afficher. Les ligues sans
    cotes dont ESPN donne le calendrier (Colombie...) viennent apres les autres."""
    ligues = web.championnats()
    codes = [c["code"] for c in ligues]
    assert codes == list(config.COMPETITIONS_AVEC_COTES) + list(config.CHAMPIONNATS_SANS_COTES)
    assert "F1" in codes and "E0" in codes and "E3" in codes
    assert "COL" in codes and "KSA" in codes
    assert "EC" not in codes and "SC3" not in codes
    assert set(codes) < set(config.COMPETITIONS)
    sans_cotes = {c["code"] for c in ligues if c["sans_cotes"]}
    assert sans_cotes == set(config.CHAMPIONNATS_SANS_COTES)
    assert "COL" in sans_cotes and "KSA" not in sans_cotes


def test_les_coupes_sont_proposees_avec_les_championnats():
    """Coupes d'Europe en tete, coupes nationales juste apres leur pays."""
    codes = [c["code"] for c in web.championnats()]
    assert codes[:3] == ["UCL", "UEL", "UECL"]
    assert set(config.COUPES) <= set(codes)
    assert codes.index("FAC") > codes.index("E0")
    assert codes.index("FAC") < codes.index("SC0")
    assert not set(config.COUPES) & set(config.CHAMPIONNATS)


def test_chaque_ligue_porte_son_pays_pour_le_regroupement():
    for ligne in web.championnats():
        assert ligne["pays"] == config.COMPETITIONS[ligne["code"]]["pays"]


def test_une_equipe_sans_historique_est_signalee_pas_escamotee(bouchons):
    """Sept matchs affiches sur neuf sans un mot serait un mensonge par omission."""
    matchs, ecartes, age = web.affiches("F1")
    assert [(m["dom"], m["ext"]) for m in matchs] == [("Paris SG", "Marseille")]
    assert len(ecartes) == 1
    assert ecartes[0]["inconnues"] == ["Le Mans"]
    assert age == 12.0


def test_la_page_garde_tous_les_matchs_a_venir(bouchons, monkeypatch):
    cotes = pd.DataFrame({
        "date": [
            f"{JOUR_HIER}T18:45:00Z",
            f"{JOUR_DEMAIN}T18:45:00Z",
            f"{JOUR_DIMANCHE}T18:45:00Z",
            f"{JOUR_LUNDI_PROCHAIN}T18:45:00Z",
        ],
        "dom": ["Paris SG"] * 4,
        "ext": ["Marseille"] * 4,
        "bookmaker": ["b1"] * 4,
    })
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (cotes, 12.0))
    matchs, _, _ = web.affiches("F1")
    assert {match["date"][:10] for match in matchs} == {JOUR_DEMAIN, JOUR_DIMANCHE, JOUR_LUNDI_PROCHAIN}


def test_les_opportunites_recoivent_tous_les_matchs_a_venir(
    bouchons, monkeypatch
):
    cotes = pd.DataFrame({
        "date": [
            f"{JOUR_HIER}T18:45:00Z",
            f"{JOUR_DEMAIN}T18:45:00Z",
            f"{JOUR_DIMANCHE}T18:45:00Z",
            f"{JOUR_LUNDI_PROCHAIN}T18:45:00Z",
        ],
        "dom": ["Paris SG"] * 4,
        "ext": ["Marseille"] * 4,
        "bookmaker": ["b1"] * 4,
    })
    recus = []
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (cotes, 12.0))
    monkeypatch.setattr(web.valeur_module, "detecter",
                        lambda tableau, *_a, **_k: recus.extend(tableau["date"]) or [])
    web.opportunites("F1", 1000.0)
    assert {str(date)[:10] for date in recus} == {JOUR_DEMAIN, JOUR_DIMANCHE, JOUR_LUNDI_PROCHAIN}


def test_le_plafond_du_combine_empile_tous_les_favoris():
    candidats = [
        web.quotidien_module.Selection(
            date="2026-10-10", championnat="F1", dom=f"E{i}", ext="X",
            selection="1", equipe=f"E{i}", p_marche=1 - i / 100,
            cote=1.1, bookmaker="book", cote_mediane=1.1, prime=0,
            nb_bookmakers=10, esperance=0,
        )
        for i in range(31)
    ]
    assert web._cote_maximale(candidats) == pytest.approx(1.1 ** 31, abs=0.01)
    assert web.MAX_JAMBES_COMBINE_JOUR is None


def test_etat_du_jour_porte_les_garde_fous(bouchons):
    etat = web.etat_du_jour("F1", 500.0)
    assert etat["plafond_mise"] == round(500.0 * config.PLAFOND_MISE_BANKROLL, 2)
    assert etat["part_plafond"] == config.PLAFOND_MISE_BANKROLL
    assert etat["avertissement"] == config.AVERTISSEMENT_LEGAL
    assert etat["nom"] == "Ligue 1"


def test_consulter_ne_consomme_aucun_credit(bouchons, monkeypatch):
    """Recharger la page ne doit jamais appeler l'API : c'est un bouton, pas un effet de bord."""
    def interdit(*_a, **_k):
        raise AssertionError("l'affichage a appele l'API des cotes")

    monkeypatch.setattr(web.cotes_module, "recuperer", interdit)
    web.etat_du_jour("F1", 1000.0, forcer=False)


def test_rafraichir_passe_bien_par_l_api(bouchons, monkeypatch):
    appels = []
    monkeypatch.setattr(web.cotes_module, "recuperer",
                        lambda c, forcer=False: appels.append((c, forcer)) or COTES)
    web.etat_du_jour("F1", 1000.0, forcer=True)
    assert appels == [("F1", True)]


# --------------------------------------------------------------------------
# La vue du jour, toutes ligues
# --------------------------------------------------------------------------

def test_la_vue_du_jour_ne_garde_que_la_journee_demandee(bouchons):
    """Le bouchon porte un match le 21 et un le 22 : un seul doit passer."""
    matchs, _, _ = web.affiches("F1", jour=JOUR_DEMAIN)
    assert [(m["dom"], m["ext"]) for m in matchs] == [("Paris SG", "Marseille")]
    assert web.affiches("F1", jour=JOUR_LUNDI_PROCHAIN)[0] == []


def test_seules_les_ligues_qui_jouent_sont_retenues(bouchons, monkeypatch):
    """Entrainer un modele coute des secondes : une ligue au repos ne doit pas
    en payer le prix."""
    monkeypatch.setattr(web.cotes_module, "lire_cache",
                        lambda c: (COTES, 12.0) if c in ("F1", "E0")
                        else (COTES.iloc[0:0], None))
    assert web.ligues_du_jour(JOUR_DEMAIN) == ["E0", "F1"]
    assert web.ligues_du_jour("2027-01-01") == []


def test_la_vue_du_jour_n_entraine_pas_les_ligues_au_repos(bouchons, monkeypatch):
    entraines = []
    monkeypatch.setattr(web.cotes_module, "lire_cache",
                        lambda c: (COTES, 12.0) if c == "F1"
                        else (COTES.iloc[0:0], None))
    monkeypatch.setattr(web.CACHE, "modele",
                        lambda c: entraines.append(c) or (pd.DataFrame(), FauxModele()))
    web.etat_aujourdhui(1000.0, jour=JOUR_DEMAIN)
    assert set(entraines) == {"F1"}


def test_la_vue_du_jour_marque_chaque_match_de_sa_ligue(bouchons):
    etat = web.etat_aujourdhui(1000.0, jour=JOUR_DEMAIN)
    assert etat["matchs"], "la journee du bouchon n'est pas vide"
    assert all(m["ligue"] in config.COMPETITIONS for m in etat["matchs"])
    assert etat["multi"] is True and etat["jour"] == JOUR_DEMAIN


def test_la_vue_du_jour_tait_les_mesures_par_championnat(bouchons):
    """Un ROI mesure sur la Ligue 1 ne decrit pas la Bundesliga : cumuler les
    deux melangerait des echantillons qui n'ont rien a voir."""
    etat = web.etat_aujourdhui(1000.0, jour=JOUR_DEMAIN)
    assert etat["performance"] is None
    assert etat["poids_modele"] is None


def test_une_ligue_en_panne_n_emporte_pas_la_journee(bouchons, monkeypatch):
    def modele(championnat):
        if championnat == "E0":
            raise ValueError("Aucune donnee pour E0")
        return pd.DataFrame(), FauxModele()

    monkeypatch.setattr(web.CACHE, "modele", modele)
    etat = web.etat_aujourdhui(1000.0, jour=JOUR_DEMAIN)
    en_panne = [l for l in etat["ligues"] if l["erreur"]]
    assert [l["code"] for l in en_panne] == ["E0"]
    assert "Aucune donnee" in en_panne[0]["erreur"]
    assert etat["matchs"], "les autres ligues doivent rester affichees"


def test_consulter_la_journee_ne_consomme_aucun_credit(bouchons, monkeypatch):
    def interdit(*_a, **_k):
        raise AssertionError("la vue du jour a appele l'API des cotes")

    monkeypatch.setattr(web.cotes_module, "recuperer", interdit)
    web.etat_aujourdhui(1000.0, jour=JOUR_DEMAIN)


# --------------------------------------------------------------------------
# Verdicts : le prono confronte au resultat
# --------------------------------------------------------------------------

def test_un_match_a_venir_ne_porte_aucun_verdict(bouchons, monkeypatch):
    """Seul un match joue peut etre juge."""
    futur = pd.DataFrame({
        "date": [f"{JOUR_DEMAIN}T18:45:00Z"],
        "dom": ["Paris SG"], "ext": ["Marseille"],
        "bookmaker": ["b1"]})
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (futur, 12.0))
    matchs, _, _ = web.affiches("F1")
    assert matchs and all(not m.get("joue") for m in matchs)
    assert all("verdicts" not in m for m in matchs)


def _joue(monkeypatch, buts_dom, buts_ext, **extra):
    """Un match du bouchon place dans le passe, avec son score publie."""
    passe = pd.DataFrame({
        "date": [HORODATAGE_PASSE], "dom": ["Paris SG"], "ext": ["Marseille"],
        "bookmaker": ["b1"]})
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (passe, 12.0))
    monkeypatch.setattr(
        web._Resultats, "du_match",
        lambda self, champ, dom, ext, equipes=None, jour=None: {
            "buts_dom": buts_dom, "buts_ext": buts_ext, **extra})


def test_un_match_joue_porte_son_score_et_ses_verdicts(bouchons, monkeypatch):
    _joue(monkeypatch, 2, 0, corners_dom=6, corners_ext=3,
          jaunes_dom=1, jaunes_ext=1)
    matchs, _, _ = web.affiches("F1")
    assert len(matchs) == 1
    match = matchs[0]
    assert match["joue"] is True
    assert match["resultat"]["issue"] == "1"
    # FauxModele annonce 1N2 = 50/30/20, donc le prono est « 1 » : le score
    # 2-0 lui donne raison.
    assert match["verdicts"]["prono_1n2"] == "juste"
    assert match["verdicts"]["bilan"]["juste"] >= 1


class ModeleAvecCouverture(FauxModele):
    """FauxModele plus la double chance, pour eprouver la couverture."""

    def predire(self, dom: str, ext: str) -> dict:
        prediction = super().predire(dom, ext)
        prediction["double_chance"] = {"1N": 0.8, "12": 0.7, "N2": 0.5}
        return prediction


def test_un_prono_dementi_est_marque_rate(bouchons, monkeypatch):
    monkeypatch.setattr(web.CACHE, "modele",
                        lambda c: (pd.DataFrame(), ModeleAvecCouverture()))
    _joue(monkeypatch, 0, 3)
    matchs, _, _ = web.affiches("F1")
    verdicts = matchs[0]["verdicts"]
    assert verdicts["prono_1n2"] == "rate"
    # « Paris SG ou nul » ne couvre pas une defaite : la couverture rate aussi.
    # C'est le cas que la verification a la main avait laisse passer.
    assert verdicts["couverture"] == "rate"
    assert verdicts["nuance"] and "echouer" in verdicts["nuance"]


def test_un_match_joue_sans_resultat_publie_le_dit(bouchons, monkeypatch):
    """football-data publie par lots : de deux a treize jours de retard selon
    la ligue. Se taire laisserait croire que le match n'a pas eu lieu."""
    passe = pd.DataFrame({
        "date": [HORODATAGE_PASSE], "dom": ["Paris SG"], "ext": ["Marseille"],
        "bookmaker": ["b1"]})
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (passe, 12.0))
    monkeypatch.setattr(web._Resultats, "du_match",
                        lambda self, c, d, e, equipes=None, jour=None: None)
    matchs, _, _ = web.affiches("F1")
    assert matchs[0]["joue"] is True
    assert matchs[0]["resultat"] is None
    assert "verdicts" not in matchs[0]


def test_les_colonnes_absentes_ne_gonflent_pas_le_bilan(bouchons, monkeypatch):
    """Sans corners publies, le prono corners reste non verifiable — jamais
    compte comme juste."""
    _joue(monkeypatch, 1, 0)                      # ni corners ni cartons
    matchs, _, _ = web.affiches("F1")
    bilan = matchs[0]["verdicts"]["bilan"]
    assert bilan["juste"] + bilan["rate"] == bilan["verifiables"]
    assert bilan["verifiables"] <= bilan["juste"] + bilan["rate"] + bilan["inconnu"]


# --------------------------------------------------------------------------
# Serveur
# --------------------------------------------------------------------------

@pytest.fixture()
def serveur(bouchons):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Gestionnaire)
    fil = threading.Thread(target=httpd.serve_forever, daemon=True)
    fil.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(base: str, chemin: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base + chemin, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read())
    except urllib.error.HTTPError as erreur:
        return erreur.code, json.loads(erreur.read())


def test_la_page_est_servie(serveur):
    with urllib.request.urlopen(serveur + "/", timeout=10) as reponse:
        page = reponse.read().decode("utf-8")
    assert reponse.status == 200
    assert "<title>" in page


def test_l_etat_du_jour_est_servi(serveur):
    code, charge = _get(serveur, "/api/jour?championnat=F1&bankroll=800")
    assert code == 200
    assert charge["bankroll"] == 800.0
    assert charge["matchs"]


@pytest.mark.parametrize("requete,attendu", [
    ("/api/jour?championnat=ZZ", "Championnat inconnu"),
    ("/api/jour?championnat=F1&bankroll=abc", "Bankroll illisible"),
    ("/api/jour?championnat=F1&bankroll=-5", "strictement positive"),
])
def test_une_saisie_invalide_donne_un_message_lisible(serveur, requete, attendu):
    code, charge = _get(serveur, requete)
    assert code == 400
    assert attendu in charge["erreur"]
    assert "Traceback" not in charge["erreur"]


def test_la_journee_est_servie(serveur):
    code, charge = _get(serveur, f"/api/aujourdhui?bankroll=800&jour={JOUR_DEMAIN}")
    assert code == 200
    assert charge["bankroll"] == 800.0
    assert charge["jour"] == JOUR_DEMAIN
    assert charge["multi"] is True
    assert all(m["ligue"] for m in charge["matchs"])


@pytest.mark.parametrize("requete,attendu", [
    ("/api/aujourdhui?jour=hier", "Jour illisible"),
    ("/api/aujourdhui?bankroll=abc", "Bankroll illisible"),
    ("/api/aujourdhui?bankroll=-5", "strictement positive"),
])
def test_une_journee_mal_demandee_donne_un_message_lisible(serveur, requete, attendu):
    code, charge = _get(serveur, requete)
    assert code == 400
    assert attendu in charge["erreur"]
    assert "Traceback" not in charge["erreur"]


def test_le_combine_ne_retient_que_la_journee_demandee(serveur):
    """Le bouton s'appelle « du jour » : il ne doit pas empiler trois journees."""
    code, charge = _get(
        serveur, f"/api/combine-du-jour?cible=1.5&mise=10&jour={JOUR_DEMAIN}")
    assert code == 200
    if "erreur" in charge:                      # bouchon sans favori ce jour-la
        return
    assert charge["jours_couverts"] == [JOUR_DEMAIN]
    assert all(j["date"].startswith(JOUR_DEMAIN) for j in charge["jambes"])


def test_le_combine_dit_sur_combien_de_journees_il_porte(serveur):
    """Sans jour precis, il empile tout le cache : la page doit pouvoir le dire
    plutot que de laisser croire a une seule journee."""
    code, charge = _get(serveur, "/api/combine-du-jour?cible=1.5&mise=10")
    assert code == 200
    if "erreur" not in charge:
        assert charge["jours_couverts"]
        assert charge["jour"] is None


def test_un_jour_illisible_est_refuse_par_le_combine(serveur):
    code, charge = _get(serveur, "/api/combine-du-jour?cible=9&jour=samedi")
    assert code == 400
    assert "Jour illisible" in charge["erreur"]


def test_une_route_inconnue_renvoie_404(serveur):
    code, charge = _get(serveur, "/nimporte-quoi")
    assert code == 404 and "erreur" in charge


# --------------------------------------------------------------------------
# La page elle-meme
# --------------------------------------------------------------------------

def test_la_page_ne_contient_aucun_libelle_interdit():
    texte = (RACINE / "web" / "page.html").read_text(encoding="utf-8").lower()
    for interdit in ("match sur", "banker", "pronostic garanti"):
        assert interdit not in texte


def test_la_page_porte_la_marque_rab3ounstat():
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert "<title>RAB3OUNSTAT" in page
    assert "foot_edge —" not in page, "ancien titre encore present"
    assert 'aria-label="RAB3OUNSTAT"' in page, "le logo doit rester lisible sans image"


def test_la_jauge_du_combine_ne_reutilise_pas_la_classe_des_quotas():
    """`.jauge` appartient aux barres de quota des API (display:inline-block,
    width:90px). La reutiliser reduisait la jauge du combine a 90 pixels."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert ".jauge-combine" in page
    assert 'class="jauge-combine' in page


def test_aucune_classe_css_n_est_definie_par_deux_composants():
    """Deux collisions ont deja coute cher : `.jauge` (barres de quota API,
    width:90px) a reduit la jauge du combine a 90 pixels, et `.barre` (barre de
    probabilites 1N2, height:22px) a ecrase la navigation.

    Le signal fiable est la RACINE d'un composant — la classe par laquelle un
    selecteur commence. `.tuile .valeur` et `.chiffresCle .valeur` partagent
    `.valeur` sans se gener : elle est portee par deux parents differents. Deux
    regles qui commencent toutes deux par `.barre` sont en revanche deux
    composants qui se disputent le meme nom.
    """
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    css = page[page.index("<style>"):page.index("</style>")]
    # Les commentaires d'abord : sans cela ils sont avales avec le selecteur qui
    # les suit, et toute regle commentee devient invisible a l'analyse — ce qui
    # ferait passer ce test alors meme que la collision est la.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)  # surcharges

    # Les racines dans l'ordre du document. Un composant ecrit ses regles
    # d'affilee ; si les occurrences d'un meme nom forment DEUX groupes separes
    # par d'autres composants, c'est que deux composants portent ce nom. Pas de
    # seuil de distance : la vraie collision `.jauge` n'etait separee que de
    # 1373 caracteres, un seuil l'aurait laissee passer.
    ordre: list[str] = []
    for regle in re.finditer(r"(?:^|\})\s*([^{}@]+?)\s*\{", css):
        for selecteur in regle.group(1).split(","):
            premier = re.match(r"\s*\.([a-zA-Z][\w-]*)", selecteur)
            if premier and (not ordre or ordre[-1] != premier.group(1)):
                ordre.append(premier.group(1))

    disputees = sorted({nom for nom in ordre if ordre.count(nom) > 1})
    assert not disputees, (
        f"racines de composant definies en plusieurs endroits : {disputees}. "
        "Soit les regles appartiennent au meme composant et doivent etre "
        "regroupees, soit ce sont deux composants et l'un doit etre renomme.")


def test_chaque_section_est_dans_une_vue():
    """Une section hors vue serait inatteignable : la navigation n'affiche
    qu'une vue a la fois."""
    from html.parser import HTMLParser

    class Arbre(HTMLParser):
        def __init__(self):
            super().__init__()
            self.pile: list[str | None] = []
            self.orphelines: list[str] = []
            self.vues: set[str] = set()
            self.onglets: set[str] = set()

        def handle_starttag(self, tag, attrs):
            att = dict(attrs)
            if tag == "button" and "onglet" in (att.get("class") or ""):
                self.onglets.add(att.get("data-vue", ""))
            if tag == "div" and "vue" in (att.get("class") or "").split():
                self.vues.add(att.get("data-vue", ""))
                self.pile.append("vue")
                return
            if tag == "section" and "vue" not in self.pile:
                self.orphelines.append(att.get("id", "?"))
            if tag not in ("br", "meta", "input", "img", "path", "hr"):
                self.pile.append(None)

        def handle_endtag(self, tag):
            if self.pile:
                self.pile.pop()

    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    corps = page[page.index("<nav"):page.index("<footer")]
    arbre = Arbre()
    arbre.feed(corps)
    assert not arbre.orphelines, f"sections hors de toute vue : {arbre.orphelines}"
    assert arbre.vues == arbre.onglets, f"vues {arbre.vues} != onglets {arbre.onglets}"


def test_le_chiffre_du_combine_ne_depend_pas_de_l_animation():
    """La valeur vraie est ecrite avant toute animation : un onglet en
    arriere-plan gele requestAnimationFrame, et le chiffre resterait a zero."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    corps = page[page.index("function compter("):page.index("async function afficherCombineDuJour")]
    assert corps.index("ecrire(cible);") < corps.index("requestAnimationFrame")


def test_la_mise_du_combine_n_est_plus_codee_en_dur():
    """Elle valait 10 EUR en dur dans le script : l'utilisateur ne pouvait
    choisir que le gain vise, jamais ce qu'il engageait."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert "const mise = 10;" not in page
    assert 'id="mise"' in page and 'id="cible"' in page


def test_l_objectif_du_combine_se_donne_en_euros():
    """La mise et le gain minimum sont les deux chiffres qu'on a en tete avant
    de parier ; la cote a atteindre n'en est que le quotient. Le champ manquait :
    on ne pouvait regler qu'un multiplicateur et lire le gain sans le fixer."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert 'id="gain-minimum"' in page
    assert "CIBLE = borner(gainDemande() / miseChoisie());" in page


def test_le_gain_demande_est_un_plancher_jamais_arrondi_vers_le_bas():
    """Arrondir la cible deduite du gain (x9.3 ramene a x9) rendrait un combine
    payant moins que le minimum demande. Seul le curseur, qui part de la cote,
    a le droit d'arrondir."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    corps = page[page.index("function cibleDepuisGain"):
                 page.index("function cibleDepuisCurseur")]
    assert "Math.round" not in corps


def test_le_combine_accepte_un_objectif_non_rond(serveur):
    """Une cible deduite d'un gain tombe rarement juste : 93 EUR pour 10 de mise
    valent x9.3. La refuser obligerait la page a arrondir, donc a rendre moins
    que le minimum demande."""
    code, charge = _get(serveur, "/api/combine-du-jour?cible=9.3&mise=10")
    assert code == 200
    if "erreur" not in charge:
        assert charge["cible"] == pytest.approx(9.3)


def test_l_echelle_du_curseur_est_multiplicative():
    """Une cote se multiplie. En echelle lineaire, les trois quarts de la course
    du curseur tomberaient entre x25 et x100, la zone la moins utile."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    corps = page[page.index("const CIBLE_MIN"):page.index("const cibleChoisie")]
    assert "Math.log" in corps and "Math.exp" in corps


def test_le_serveur_annonce_ce_qu_il_sait_faire(bouchons):
    """`web/page.html` est relu a chaque requete, `web.py` seulement au
    demarrage. Sans ce drapeau, une page neuve devant un serveur ancien affiche
    « resultat pas encore publie » alors que le serveur ne sait pas les lire."""
    etat = web.etat_du_jour("F1", 1000.0)
    assert "resultats_api_football" in etat["capacites"]
    assert "verdicts" in etat["capacites"]


def test_la_page_distingue_serveur_ancien_et_resultat_manquant():
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    corps = page[page.index("function blocScore("):page.index("function carteMatch(")]
    assert "resultats_api_football" in corps
    assert "Relance-le" in corps, "le remede doit etre donne, pas seulement le constat"


def test_le_cadre_du_match_suit_la_double_chance():
    """Demande explicite : cadre vert quand 1X, 12 ou X2 se realise."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert ".match.cadre-juste" in page and ".match.cadre-rate" in page
    corps = page[page.index("function carteMatch("):page.index("function afficherMatchs(")]
    assert "verdicts || {}).couverture" in corps
    assert "cadre-juste" in corps and "cadre-rate" in corps


def test_les_animations_sont_desactivables():
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in page


def test_le_bandeau_ne_peut_pas_etre_masque():
    """Meme regle que dans l'interface tkinter : ni croix, ni repli, ni reglage."""
    texte = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    bandeau = texte[texte.index('class="bandeau"'):texte.index('id="erreur"')]
    for interdit in ("masquer", "fermer", "hidden", "display:none", "toggle"):
        assert interdit not in bandeau.lower()
    assert "marge d'erreur" in texte


# --------------------------------------------------------------------------
# Fiabilite des estimations et melange de ligues
# --------------------------------------------------------------------------

def test_une_equipe_sans_historique_suffisant_est_signalee(bouchons, monkeypatch):
    """Un promu a deux matchs recoit des forces absurdes : il faut le dire."""
    charges = pd.DataFrame({
        "dom": ["Cambuur"] + ["Ajax"] * 20,
        "ext": ["Feyenoord"] * 21})
    monkeypatch.setattr(web.CACHE, "modele", lambda c: (charges, FauxModele()))
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (pd.DataFrame({
        "date": [f"{JOUR_DEMAIN}T14:45:00Z"], "dom": ["Paris SG"], "ext": ["Marseille"],
        "bookmaker": ["b1"]}), 5.0))
    matchs, _, _ = web.affiches("N1")
    fragiles = {e["equipe"]: e["matchs"] for e in matchs[0]["peu_de_donnees"]}
    assert fragiles == {"Paris SG": 0, "Marseille": 0}


def test_un_combine_peut_melanger_deux_championnats(monkeypatch):
    """Chaque jambe est evaluee avec le modele de SA ligue, pas d'une autre."""
    demandes = []

    def modele(code):
        demandes.append(code)
        return (pd.DataFrame(), FauxModele())

    monkeypatch.setattr(web.CACHE, "modele", modele)
    aiguilleur = web.ModeleParLigue([
        {"ligue": "F1", "dom": "Paris SG", "ext": "Marseille"},
        {"ligue": "N1", "dom": "Lens", "ext": "Paris SG"}], defaut="F1")
    assert sorted(demandes) == ["F1", "N1"]
    assert aiguilleur.matrice_scores("Paris SG", "Marseille") is not None


def test_un_championnat_inconnu_dans_un_combine_est_refuse(monkeypatch):
    monkeypatch.setattr(web.CACHE, "modele", lambda c: (pd.DataFrame(), FauxModele()))
    with pytest.raises(ValueError, match="Championnat inconnu"):
        web.ModeleParLigue([{"ligue": "ZZ", "dom": "A", "ext": "B"}], defaut="F1")


def test_les_championnats_portent_l_age_de_leurs_cotes(monkeypatch):
    monkeypatch.setattr(web.cotes_module, "age_du_cache",
                        lambda c: 600.0 if c == "F1" else None)
    lignes = {c["code"]: c["age_cotes_minutes"] for c in web.championnats()}
    assert lignes["F1"] == 10.0          # 600 secondes = 10 minutes
    assert lignes["E0"] is None


def test_le_filtre_jour_connait_les_journees_sans_match_estimable():
    """Une journee dont toutes les rencontres sont ecartees doit rester dans le
    filtre. Absente, elle se lirait comme « aucun match ce jour-la », alors que
    le match existe — il est seulement inestimable (Arsenal - Coventry City)."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    debut = page.index("function remplirJours(")
    corps = page[debut:page.index("function filtrer(")]
    assert "ecartes" in corps, "les jours sont construits sans les ecartes"
    assert "remplirJours(etat.matchs, etat.ecartes)" in page


def test_le_filtre_jour_porte_aussi_sur_les_ecartes():
    """Choisir un jour ne doit pas afficher les ecartes de toute la semaine."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    corps = page[page.index("function filtrer("):page.index("function afficherMatchs(")]
    assert "ETAT.ecartes" in corps and "afficherEcartes(ecartes)" in corps


def test_le_script_de_la_page_est_du_javascript_valide():
    """Un template literal mal ferme ne se voit qu'a l'execution dans le navigateur."""
    import re
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node absent")
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
    fichier = RACINE / "web" / ".verification.js"
    try:
        fichier.write_text(script, encoding="utf-8")
        fin = subprocess.run(["node", "--check", str(fichier)],
                             capture_output=True, text=True, timeout=60)
        assert fin.returncode == 0, fin.stderr
    finally:
        fichier.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Etat des cles API
# --------------------------------------------------------------------------

def test_les_trois_services_sont_decrits():
    services = web.etat_des_apis()
    assert [s["nom"] for s in services] == ["odds", "football_data_org", "api_football"]
    for service in services:
        assert service["limite"] > 0
        assert service["periode"] in ("minute", "jour", "mois")


def test_seuls_les_services_reellement_appeles_ont_un_compteur(monkeypatch):
    """Deux services sont interroges, un seul ne l'est pas — et ce n'est pas un oubli.

    football-data.org reste sans compteur parce que son plan gratuit n'apporte
    rien d'exploitable ici : arbitres jamais renseignes, cotes derriere un
    paquet payant, classement deja couvert par les CSV. Lui inventer un
    compteur a zero laisserait croire a un usage qui n'existe pas.
    """
    monkeypatch.setattr(web.cotes_module, "lire_compteur", lambda: {
        "mois": "2026-08", "appels": 5, "credits": 14,
        "restant_api": 486, "dernier_appel": "2026-08-20T09:33:05+00:00"})
    monkeypatch.setattr(web.absences_module, "lire_compteur", lambda: {
        "jour": "2026-08-20", "appels": 3, "restant_api": 95,
        "dernier_appel": "2026-08-20T20:10:00+00:00"})
    services = {s["nom"]: s for s in web.etat_des_apis()}

    assert services["odds"]["utilise_par_l_outil"] is True
    assert services["odds"]["consomme"] == 14
    assert services["odds"]["restant"] == 500 - 14
    assert services["odds"]["annonce_par_l_api"] == 486

    # API-Football : quota quotidien, un credit par appel.
    assert services["api_football"]["utilise_par_l_outil"] is True
    assert services["api_football"]["consomme"] == 3
    assert services["api_football"]["restant"] == 100 - 3
    assert services["api_football"]["annonce_par_l_api"] == 95
    assert len(services["api_football"]["fenetre"]) == 2

    # football-data.org : resultats de Ligue des champions, sans compteur.
    assert services["football_data_org"]["utilise_par_l_outil"] is True
    assert "Ligue des champions" in services["football_data_org"]["usage"]
    assert services["football_data_org"]["consomme"] is None
    assert services["football_data_org"]["restant"] is None


def test_le_restant_ne_descend_jamais_sous_zero(monkeypatch):
    monkeypatch.setattr(web.cotes_module, "lire_compteur", lambda: {
        "mois": "2026-08", "appels": 400, "credits": 900,
        "restant_api": 0, "dernier_appel": None})
    odds = next(s for s in web.etat_des_apis() if s["nom"] == "odds")
    assert odds["restant"] == 0


def test_l_etat_des_apis_est_servi(serveur):
    code, charge = _get(serveur, "/api/apis")
    assert code == 200
    assert len(charge["services"]) == 3


# --------------------------------------------------------------------------
# Onglet Equipes fortes
# --------------------------------------------------------------------------

def _favori(dom: str, p: float, cote: float, instant: str):
    return web.quotidien_module.Selection(
        date=instant, championnat="F1", dom=dom, ext="X", selection="1",
        equipe=dom, p_marche=p, cote=cote, bookmaker="book", cote_mediane=cote,
        prime=0.0, nb_bookmakers=10, esperance=p * cote - 1)


@pytest.fixture()
def favoris(monkeypatch):
    demain = f"{JOUR_DEMAIN}T18:45:00Z"
    liste = [_favori("Lens", 0.72, 1.40, demain),
             _favori("Paris SG", 0.85, 1.15, demain),
             _favori("Monaco", 0.78, 1.25, demain),
             _favori("Deja joue", 0.95, 1.05, HORODATAGE_PASSE)]
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (COTES, 12.0))
    monkeypatch.setattr(web.quotidien_module, "du_jour", lambda *a, **k: list(liste))


def test_les_equipes_fortes_sont_triees_et_ecartent_les_matchs_joues(favoris):
    charge = web.equipes_fortes(None, 10.0)
    assert [f["dom"] for f in charge["fortes"]] == ["Paris SG", "Monaco", "Lens"]
    assert charge["fortes"][0]["perd_une_fois_sur"] == pytest.approx(1 / 0.15)


def test_les_combines_prets_portent_leur_probabilite_jointe(favoris):
    combines = web.equipes_fortes(JOUR_DEMAIN, 10.0)["combines"]
    assert combines, "trois favoris a 1.15, 1.25, 1.40 atteignent x2"
    prudent = combines[0]
    assert prudent["nom"] == "Prudent" and prudent["cote_totale"] >= 2.0
    attendu = 1.0
    for jambe in prudent["jambes"]:
        attendu *= jambe["p_marche"]
    assert prudent["probabilite"] == pytest.approx(attendu)
    # Le meme empilement ne revient pas sous un autre nom.
    cles = [tuple(j["dom"] for j in c["jambes"]) for c in combines]
    assert len(cles) == len(set(cles))


def test_equipes_fortes_sans_cotes_dit_quoi_faire(monkeypatch):
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (pd.DataFrame(), None))
    assert "Rafraichir" in web.equipes_fortes(None, 10.0)["erreur"]


@pytest.mark.parametrize("requete", [
    "/api/equipes-fortes?jour=samedi", "/api/equipes-fortes?mise=-3"])
def test_equipes_fortes_refuse_une_demande_illisible(serveur, requete):
    code, charge = _get(serveur, requete)
    assert code == 400 and "erreur" in charge


def test_une_reponse_ne_contient_jamais_nan():
    """JSON.parse refuse NaN : une seule case vide faisait tomber toute la page."""
    assert web._sans_nan({"a": [float("nan"), 1.0], "b": float("inf")}) == {
        "a": [None, 1.0], "b": None}


def test_la_vue_aujourdhui_affiche_les_combines_prets():
    """Les combines prets apparaissent aussi en tete de « Aujourd'hui — toutes
    ligues », et seulement la : `etat.jour` n'existe que dans cette vue."""
    page = (RACINE / "web" / "page.html").read_text(encoding="utf-8")
    assert 'id="combines-aujourdhui"' in page
    corps = page[page.index("function afficher(etat)"):page.index("async function demanderLeJour(")]
    assert "afficherCombinesAujourdhui(etat.jour || null)" in corps


def test_un_score_d_une_autre_rencontre_de_la_meme_affiche_est_refuse():
    """Ligues a trois ou quatre confrontations : meme affiche, autre match."""
    assert web._meme_journee("2026-10-31 00:00:00", "2026-10-31")
    assert web._meme_journee("2026-10-30 00:00:00", "2026-10-31")    # fuseau
    assert not web._meme_journee("2026-08-02 00:00:00", "2026-10-31")


def test_les_listes_a_plus_de_50_pour_cent(monkeypatch):
    """Vainqueurs (jamais le nul) et cote probable du plus/moins 2.5, tries."""
    demain = f"{JOUR_DEMAIN}T18:45:00Z"
    nul = _favori("Nul", 0.55, 1.80, demain)
    nul.selection = "N"
    vainqueurs = [_favori("Lens", 0.56, 1.70, demain), nul,
                  _favori("Lyon", 0.64, 1.50, demain),
                  _favori("Deja joue", 0.90, 1.05, HORODATAGE_PASSE)]
    plus = _favori("Brest", 0.58, 1.65, demain)
    plus.selection, plus.equipe = "plus_moins_2.5:plus", "plus de 2.5 buts"
    gg = _favori("Nice", 0.70, 1.40, demain)
    gg.selection = "btts:oui"
    monkeypatch.setattr(web.quotidien_module, "candidats",
                        lambda *a, **k: list(vainqueurs))
    monkeypatch.setattr(web.quotidien_module, "candidats_multi",
                        lambda *a, **k: [plus, gg])

    listes = web.listes_elargies({"F1": COTES}, None)
    assert [v["dom"] for v in listes["vainqueurs"]] == ["Lyon", "Lens"]
    assert [b["equipe"] for b in listes["buts"]] == ["plus de 2.5 buts"]
    assert listes["buts"][0]["perd_une_fois_sur"] == pytest.approx(1 / 0.42)


def test_les_statuts_en_direct_ne_renvoient_que_ce_qui_bouge(monkeypatch):
    carte = {"date": "2026-10-31T14:00:00+00:00", "dom": "Lens", "ext": "Lille",
             "statut": "en_cours", "direct": {"buts_dom": 1.0, "buts_ext": 0.0,
                                               "minute": 55, "statut": "2H"},
             "resultat": None, "joue": True, "1": 0.4, "pronos": ["lourd"]}
    monkeypatch.setattr(web, "affiches", lambda code, jour=None: ([carte], [], None))
    charge = web.statuts_du_jour(["F1"])
    assert charge["matchs"] == [{"ligue": "F1", "verdicts": None, "annulation": None} | {
        cle: carte[cle] for cle in ("date", "dom", "ext", "statut", "direct",
                                    "resultat", "joue")}]


@pytest.mark.parametrize("requete", ["/api/statuts", "/api/statuts?ligues=F1,ZZZ"])
def test_les_statuts_refusent_une_ligue_inconnue(serveur, requete):
    code, charge = _get(serveur, requete)
    assert code == 400 and "erreur" in charge


def test_un_match_de_coupe_porte_son_modele_et_sa_reserve(bouchons, monkeypatch):
    """Une coupe n'a pas de modele a elle : la carte doit dire lequel elle emprunte."""
    description = {"type": "europe", "championnats": ["E0", "F1"], "matchs": 2000,
                   "equipes": 40, "matchs_europeens": 30, "ecartes": []}
    monkeypatch.setattr(web.CACHE, "coupe", lambda c: description)
    matchs, _, _ = web.affiches("UCL")
    assert matchs and matchs[0]["coupe"] == description
    assert matchs[0]["pronos_annexes"] == []
    reserves = " ".join(r for p in matchs[0]["pronos"] for r in p.get("reserves", []))
    assert "30 matchs de Ligue des champions" in reserves


def test_une_coupe_n_a_ni_corners_ni_cartons():
    assert web.CACHE.annexes("UCL") == {}


def test_les_resultats_d_une_coupe_ne_sont_pas_cherches_chez_football_data(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("football-data.co.uk ne publie aucune coupe")
    monkeypatch.setattr(web.resultats_module, "charger", interdit)
    assert web._Resultats()._ligue("FAC") == {}


# --------------------------------------------------------------------------
# Pronos surs
# --------------------------------------------------------------------------

def test_les_pronos_surs_portent_leur_taux_annonce_et_mesure(bouchons, monkeypatch):
    """Un prono par match, son taux annonce, et la mesure du meme type au meme niveau."""
    choix = {"marche": "double chance", "selection": "1N", "libelle": "Paris SG ou nul",
             "p_modele": 0.81, "p_marche": 0.80, "p_finale": 0.805, "cote": 1.22,
             "echoue_1_fois_sur": 5.1}
    monkeypatch.setattr(web, "ligues_du_jour", lambda jour: ["F1"])
    monkeypatch.setattr(web, "affiches", lambda code, jour=None: ([
        {"date": f"{JOUR_DEMAIN}T18:45:00Z", "dom": "Paris SG", "ext": "Marseille",
         "statut": "a_venir", "choix_sur": {"choix": choix, "sur": True, "reserves": []}},
        {"date": f"{JOUR_DEMAIN}T20:45:00Z", "dom": "Lens", "ext": "Lille",
         "statut": "a_venir", "choix_sur": {"choix": None, "sur": False, "reserves": []}},
    ], [], 1.0))
    table = {"familles": {"double chance": {"0.8": [200, 162]}}}
    monkeypatch.setattr(web.reussite_module, "en_cache",
                        lambda c: table if c == "F1" else None)
    monkeypatch.setattr(web, "_lancer_mesures", lambda codes: [])
    r = web.pronos_surs(JOUR_DEMAIN)
    assert r["sans_choix"] == 1 and len(r["pronos"]) == 1
    p = r["pronos"][0]
    assert p["probabilite"] == 0.805 and p["cote_reelle"] is True
    assert p["mesure"]["portee"] == "ligue" and p["mesure"]["nombre"] == 200
    assert abs(p["mesure"]["taux"] - 0.81) < 1e-9


def test_un_prono_sur_joue_recoit_son_verdict(bouchons, monkeypatch):
    choix = {"marche": "1N2", "selection": "1", "libelle": "Victoire Paris SG",
             "p_modele": 0.7, "p_marche": None, "p_finale": 0.7, "cote": 1.4,
             "echoue_1_fois_sur": 3.3}
    monkeypatch.setattr(web, "ligues_du_jour", lambda jour: ["F1"])
    monkeypatch.setattr(web, "affiches", lambda code, jour=None: ([
        {"date": HORODATAGE_PASSE, "dom": "Paris SG", "ext": "Marseille",
         "statut": "termine", "resultat": {"buts_dom": 0, "buts_ext": 1},
         "choix_sur": {"choix": choix, "sur": True, "reserves": []}}], [], 1.0))
    monkeypatch.setattr(web.reussite_module, "en_cache", lambda c: None)
    monkeypatch.setattr(web, "_lancer_mesures", lambda codes: ["F1"])
    r = web.pronos_surs(JOUR_AUJOURDHUI)
    assert r["pronos"][0]["verdict"] == "rate"
    assert r["bilan"]["rate"] == 1 and r["mesures_en_cours"] == ["F1"]
    assert r["pronos"][0]["mesure"] is None


def test_un_match_present_sous_deux_orthographes_n_apparait_qu_une_fois(bouchons, monkeypatch):
    """L'historique des cotes garde un nom brut que le cache a canonise depuis."""
    deux = pd.DataFrame({
        "date": [f"{JOUR_DEMAIN}T18:45:00Z", f"{JOUR_DEMAIN}T18:45:00Z"],
        "dom": ["Paris SG", "Paris Saint-Germain"], "ext": ["Marseille", "Marseille"],
        "bookmaker": ["b1", "b1"]})
    monkeypatch.setattr(web.cotes_module, "lire_cache", lambda c: (deux, 12.0))
    monkeypatch.setattr(web.noms_module, "resoudre_vers",
                        lambda nom, equipes: "Paris SG" if "Paris" in nom else nom)
    matchs, _, _ = web.affiches("F1")
    assert len(matchs) == 1
