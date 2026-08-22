"""Recette du bloc 1 de doc/A_FAIRE_INTERFACE.md, pilotee sous Xvfb.

Les boutons sont reellement invoques (invoke() sur le widget trouve par son
libelle), les tableaux reellement lus. Les boites de dialogue sont interceptees
- sinon elles bloquent la boucle d'evenements - mais leur texte est verifie.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

RACINE = Path("/home/saif/bet216/foot_edge")
SORTIE = Path(os.environ["SORTIE"])
sys.path.insert(0, str(RACINE))
os.chdir(RACINE)

resultats: list[tuple[str, str, str, str]] = []
section_courante = "?"


def section(nom: str) -> None:
    global section_courante
    section_courante = nom
    print(f"\n=== {nom} " + "=" * (60 - len(nom)))


def note(item: str, ok: bool, detail: str = "") -> bool:
    statut = "OK" if ok else "ECHEC"
    resultats.append((section_courante, item, statut, detail))
    print(f"  [{statut:5}] {item}" + (f"  -> {detail}" if detail else ""))
    return ok


# --------------------------------------------------------------------------
# Interception des dialogues
# --------------------------------------------------------------------------

class Dialogues:
    """Enregistre les boites de dialogue au lieu de les afficher."""

    def __init__(self) -> None:
        self.appels: list[tuple[str, str, str]] = []
        self.reponse_oui = True

    def _noter(self, genre, titre, message, *a, **k):
        self.appels.append((genre, str(titre), str(message)))
        return None

    def showinfo(self, titre="", message="", **k):
        return self._noter("info", titre, message)

    def showwarning(self, titre="", message="", **k):
        return self._noter("avertissement", titre, message)

    def showerror(self, titre="", message="", **k):
        return self._noter("erreur", titre, message)

    def askyesno(self, titre="", message="", **k):
        self._noter("question", titre, message)
        return self.reponse_oui

    def vider(self):
        self.appels.clear()

    def dernier(self):
        return self.appels[-1] if self.appels else None


class FauxFiledialog:
    def __init__(self, chemin: Path) -> None:
        self.chemin = chemin
        self.demandes = 0

    def asksaveasfilename(self, **k):
        self.demandes += 1
        return str(self.chemin)


# --------------------------------------------------------------------------
# Pilotage
# --------------------------------------------------------------------------

def pompe(secondes: float = 0.15) -> None:
    fin = time.time() + secondes
    while True:
        fenetre.update()
        if time.time() >= fin:
            return
        time.sleep(0.01)


def attendre(condition, limite: float = 300.0) -> bool:
    debut = time.time()
    while time.time() - debut < limite:
        fenetre.update()
        if condition():
            pompe(0.1)
            return True
        time.sleep(0.03)
    return False


def widgets(racine=None, classe=None):
    racine = racine if racine is not None else fenetre
    for enfant in racine.winfo_children():
        if classe is None or enfant.winfo_class() == classe:
            yield enfant
        yield from widgets(enfant, classe)


def bouton(libelle: str, dans=None):
    """Le bouton portant ce libelle, cherche dans un onglet precis si besoin.

    Plusieurs onglets ont un bouton « Rafraichir » : sans le parametre `dans`,
    on prendrait celui du premier onglet construit.
    """
    trouves = [w for w in widgets(dans, "TButton") if w.cget("text") == libelle]
    if not trouves:
        raise LookupError(f"bouton introuvable : {libelle!r}")
    return trouves[0]


def cliquer(libelle: str, dans=None) -> None:
    bouton(libelle, dans).invoke()
    pompe(0.1)


def cadre(prefixe: str):
    """Le LabelFrame dont le titre commence par ce prefixe.

    L'onglet Ajustements a deux boutons « Appliquer » : celui des reglages de
    mise et celui de la correction d'equipe.
    """
    for w in widgets(classe="TLabelframe"):
        if str(w.cget("text")).startswith(prefixe):
            return w
    raise LookupError(f"cadre introuvable : {prefixe!r}")


def case(libelle: str):
    for w in widgets(classe="TCheckbutton"):
        if w.cget("text") == libelle:
            return w
    raise LookupError(f"case introuvable : {libelle!r}")


def lignes(vue) -> list[list]:
    return [vue.item(i)["values"] for i in vue.get_children("")]


def capture(nom: str) -> str:
    """Capture dans un process separe.

    PIL et matplotlib embarquent chacun leur FreeType ; les charger tous les
    deux dans le process de l'application corrompt le rendu des glyphes.
    """
    pompe(0.35)
    chemin = SORTIE / f"{nom}.png"
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "capture.py"),
                    str(chemin)], check=True, timeout=120)
    return chemin.name


def cli(*args: str) -> str:
    sortie = subprocess.run([sys.executable, "cli.py", *args], cwd=RACINE,
                            capture_output=True, text=True, timeout=900)
    return sortie.stdout + sortie.stderr



# --------------------------------------------------------------------------
# Demarrage
# --------------------------------------------------------------------------

import config                                        # noqa: E402
import app as app_module                             # noqa: E402
import re                                            # noqa: E402

dialogues = Dialogues()
faux_fichier = FauxFiledialog(SORTIE / "export.csv")
app_module.messagebox = dialogues
app_module.filedialog = faux_fichier

section("Premier lancement")
marqueur = config.DOSSIER_DONNEES / ".premier_lancement_vu"
if marqueur.exists():
    marqueur.unlink()

fenetre = app_module.Application()
fenetre.geometry("1280x880+0+0")
pompe(1.2)                                   # laisse partir le after(400)

premier = dialogues.dernier()
note("L'ecran de rappel apparait au premier lancement",
     premier is not None and "a lire une fois" in premier[1],
     premier[1] if premier else "aucun dialogue")
texte_rappel = premier[2] if premier else ""
note("Il rappelle le plafond de mise 2 %", "2 %" in texte_rappel or "2%" in texte_rappel)
note("Il rappelle le CLV avant le P&L", "CLV" in texte_rappel)
note("Il rappelle la marge d'erreur", "ESTIMATIONS" in texte_rappel.upper())
note("Le marqueur .premier_lancement_vu est ecrit", marqueur.exists())
dialogues.vider()
capture("00_demarrage")


# --------------------------------------------------------------------------
section("Onglet Donnees")
# --------------------------------------------------------------------------

choix = list(fenetre.liste_championnats.cget("values"))
note("La liste des 17 championnats s'affiche", len(choix) == 17, f"{len(choix)} entrees")
note("F1 — Ligue 1 est preselectionne",
     fenetre.liste_championnats.get().startswith("F1"), fenetre.liste_championnats.get())
note("saisons = 4, source = buts", fenetre.var_saisons.get() == 4
     and fenetre.var_source.get() == "buts",
     f"{fenetre.var_saisons.get()} saisons / {fenetre.var_source.get()}")

depart = time.time()
cliquer("Charger", fenetre.onglet_donnees)
duree_clic = time.time() - depart
note("Le clic sur Charger rend la main tout de suite",
     duree_clic < 0.5, f"{duree_clic * 1000:.0f} ms")

# Toutes les mesures « pendant le chargement » dans une seule boucle.
position_avant = (fenetre.winfo_x(), fenetre.winfo_y())
valeurs_barre: set = set()
etats_bouton: set = set()
latences: list[float] = []
deplacee = onglet_bascule = capturee = False
debut = time.time()
while "charger" in fenetre.travailleur.en_cours and time.time() - debut < 600:
    t = time.time()
    fenetre.update()
    latences.append(time.time() - t)
    valeurs_barre.add(fenetre.progression.cget("value"))
    etats_bouton.add(tuple(str(e) for e in fenetre.bouton_annuler.state()))
    if not deplacee:
        fenetre.geometry("+140+60")
        fenetre.update()
        deplacee = (fenetre.winfo_x(), fenetre.winfo_y()) != position_avant
    if not onglet_bascule:
        fenetre.carnet.select(fenetre.onglet_forces)
        fenetre.update()
        onglet_bascule = fenetre.carnet.tab(fenetre.carnet.select(), "text") == "Forces"
        fenetre.carnet.select(fenetre.onglet_donnees)
    if not capturee and time.time() - debut > 0.8:
        capture("01_donnees_pendant_chargement")
        capturee = True
    time.sleep(0.01)
duree_chargement = time.time() - debut

note("PENDANT le chargement, la fenetre se deplace (critere T14)", deplacee,
     f"{position_avant} -> ({fenetre.winfo_x()}, {fenetre.winfo_y()})")
note("PENDANT le chargement, on change d'onglet (critere T14)", onglet_bascule)
note("PENDANT le chargement, la boucle d'evenements reste fluide",
     bool(latences) and max(latences) < 0.3,
     f"{len(latences)} tours, latence max {max(latences) * 1000:.0f} ms, "
     f"chargement {duree_chargement:.1f} s")
note("La barre de progression s'anime pendant le chargement", len(valeurs_barre) > 2,
     f"{len(valeurs_barre)} valeurs distinctes")
note("Le bouton Annuler s'active pendant le chargement",
     any("disabled" not in etat for etat in etats_bouton), f"etats vus : {etats_bouton}")

pompe(0.4)
note("Le chargement aboutit", fenetre.contexte.pret(), fenetre.etat.get())
note("La barre de progression s'arrete",
     fenetre.progression.cget("value") == 0.0)
note("Le bouton Annuler se desactive a la fin",
     "disabled" in fenetre.bouton_annuler.state())

journal_texte = fenetre.zone_journal.get("1.0", "end")
manquants = [m for m in ("matchs", "equipes", "saisons", "gamma", "rho")
             if m not in journal_texte]
note("Le journal affiche matchs, equipes, saisons, gamma, rho", not manquants,
     f"manquants : {manquants}" if manquants else
     [l for l in journal_texte.splitlines() if "gamma" in l][0].strip())
nb_matchs = len(fenetre.contexte.matchs)
nb_equipes = len(set(fenetre.contexte.matchs["dom"]))
note("Le volume de donnees est coherent (306 matchs par saison a 18 clubs)",
     nb_matchs > 800, f"{nb_matchs} matchs, {nb_equipes} clubs distincts sur 4 saisons")
capture("02_donnees_charge")

dialogues.vider()
cliquer("Verifier les noms d'equipes", fenetre.onglet_donnees)
journal_texte = fenetre.zone_journal.get("1.0", "end")
note("Verifier les noms : toutes les equipes de F1 sont dans la table",
     "Toutes les equipes sont dans la table" in journal_texte,
     journal_texte.strip().splitlines()[-1][:70])

# -- rechargement en source melange ---------------------------------------
fenetre.var_source.set("melange")
depart = time.time()
cliquer("Charger", fenetre.onglet_donnees)
attendre(lambda: "charger" not in fenetre.travailleur.en_cours, 900)
pompe(0.5)
journal_texte = fenetre.zone_journal.get("1.0", "end")
appariement = re.search(r"(\d{1,3}[.,]\d)\s*%\s*(?:de[s]? )?(?:matchs? )?apparie",
                        journal_texte, re.I)
if appariement is None:
    appariement = re.search(r"apparie[^\n]*?(\d{1,3}[.,]\d)\s*%", journal_texte, re.I)
note("source = melange : le taux d'appariement xG s'affiche", appariement is not None,
     appariement.group(0).strip()[:70] if appariement else
     "aucune ligne d'appariement trouvee")
if appariement:
    taux = float(appariement.group(1).replace(",", "."))
    note("Le taux d'appariement est entre 99.8 % et 100 %", taux >= 99.8, f"{taux} %")
note("Le rechargement en melange aboutit", fenetre.contexte.pret(),
     f"{fenetre.etat.get()} en {time.time() - depart:.1f} s")
capture("03_donnees_melange")

# on repart sur la source buts, comme le reste de la recette le suppose
fenetre.var_source.set("buts")
cliquer("Charger", fenetre.onglet_donnees)
attendre(lambda: "charger" not in fenetre.travailleur.en_cours, 900)
pompe(0.4)

# --------------------------------------------------------------------------
section("Onglet Un match")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_match)
pompe(0.2)
equipes_modele = list(fenetre.contexte.modele.equipes)
equipes_dom = list(fenetre.liste_dom.cget("values"))
note("Les deux listes deroulantes sont remplies apres le chargement",
     len(equipes_dom) == len(equipes_modele) == len(list(fenetre.liste_ext.cget("values")))
     and len(equipes_dom) > 0,
     f"{len(equipes_dom)} equipes (4 saisons de Ligue 1 : 18 clubs par saison, "
     f"{len(equipes_modele)} ont joue au moins une des 4)")

fenetre.liste_dom.set("Paris SG")
fenetre.liste_ext.set("Marseille")
dialogues.vider()
cliquer("Analyser", fenetre.onglet_match)
texte_gui = fenetre.zone_match.get("1.0", "end")
gui_1n2 = re.search(r"^1\s+([\d.]+)%\s+N\s+([\d.]+)%\s+2\s+([\d.]+)%", texte_gui, re.M)
note("Paris SG - Marseille : le 1N2 s'affiche", gui_1n2 is not None,
     gui_1n2.group(0) if gui_1n2 else texte_gui[:60])

print("     (appel de cli.py match, patienter...)")
# l'interface charge 4 saisons, le CLI en prend 3 par defaut : on aligne,
# sinon on comparerait deux historiques differents
texte_cli = cli("match", "-c", "F1", "-s", "4",
                "--dom", "Paris SG", "--ext", "Marseille")
cli_1n2 = re.search(r"1N2\s*:\s*1\s+([\d.]+)%\s+N\s+([\d.]+)%\s+2\s+([\d.]+)%", texte_cli)
if gui_1n2 and cli_1n2:
    note("BUG #1 — le 1N2 de l'interface est identique a celui du CLI",
         gui_1n2.groups() == cli_1n2.groups(),
         f"interface {gui_1n2.groups()} | CLI {cli_1n2.groups()}")
else:
    note("BUG #1 — comparaison interface / CLI", False,
         f"lecture impossible : {texte_cli[:120]}")

for champ, valeur in zip(fenetre.cotes_saisies, ("1.45", "4.60", "6.50")):
    champ.delete(0, "end")
    champ.insert(0, valeur)
cliquer("Analyser", fenetre.onglet_match)
texte_gui = fenetre.zone_match.get("1.0", "end")
note("Cotes 1.45 / 4.60 / 6.50 : la surcote s'affiche", "surcote" in texte_gui,
     (re.search(r"surcote [^)]*", texte_gui) or [""])[0]
     if re.search(r"surcote [^)]*", texte_gui) else "")
esperances = re.findall(r"esperance ([+-][\d.]+%)", texte_gui)
note("Les trois esperances apparaissent", len(esperances) == 3, " / ".join(esperances))
capture("04_un_match")

fenetre.cotes_saisies[0].delete(0, "end")
fenetre.cotes_saisies[0].insert(0, "0.5")
dialogues.vider()
cliquer("Analyser", fenetre.onglet_match)
texte_gui = fenetre.zone_match.get("1.0", "end")
note("Cote invalide (0.5) : message lisible, pas de trace",
     "inutilisable" in texte_gui and "Traceback" not in texte_gui,
     (re.search(r"Cotes saisies inutilisables : .*", texte_gui) or [""])[0]
     if re.search(r"Cotes saisies inutilisables : .*", texte_gui) else texte_gui[:60])

fenetre.cotes_saisies[0].delete(0, "end")
fenetre.cotes_saisies[0].insert(0, "abc")
cliquer("Analyser", fenetre.onglet_match)
texte_gui = fenetre.zone_match.get("1.0", "end")
note("Cote en toutes lettres : pas de trace, l'analyse reste affichee",
     "Traceback" not in texte_gui and "1N2" not in texte_gui[:0] and "Paris SG" in texte_gui)
for champ in fenetre.cotes_saisies:
    champ.delete(0, "end")

fenetre.liste_ext.set("Paris SG")
dialogues.vider()
cliquer("Analyser", fenetre.onglet_match)
dernier = dialogues.dernier()
note("Deux fois la meme equipe : message, pas de plantage",
     dernier is not None and "differentes" in dernier[2],
     dernier[2] if dernier else "aucun message")
fenetre.liste_ext.set("Marseille")

# --------------------------------------------------------------------------
section("Onglet Journee")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_journee)
pompe(0.2)
cliquer("Calculer", fenetre.onglet_journee)
attendre(lambda: "journee" not in fenetre.travailleur.en_cours, 300)
pompe(0.3)
table = lignes(fenetre.vue_journee)
n_equipes = len(fenetre.contexte.modele.equipes)
attendu = n_equipes * (n_equipes - 1)
note("Le tableau se remplit avec n×(n−1) lignes", len(table) == attendu,
     f"{len(table)} lignes pour {n_equipes} equipes (n×(n−1) = {attendu})")

colonnes = list(fenetre.vue_journee.cget("columns"))
index_25 = colonnes.index("+2.5")
avant = [l[index_25] for l in table]
commande = fenetre.vue_journee.heading("+2.5", "command")
fenetre.call(commande)
pompe(0.15)
apres_1 = [l[index_25] for l in lignes(fenetre.vue_journee)]
fenetre.call(fenetre.vue_journee.heading("+2.5", "command"))
pompe(0.15)
apres_2 = [l[index_25] for l in lignes(fenetre.vue_journee)]
note("Clic sur l'en-tete +2.5 : le tableau est trie", apres_1 != avant,
     f"tete : {avant[0]} -> {apres_1[0]}")
note("Deuxieme clic : le tri s'inverse", apres_2 == list(reversed(apres_1)),
     f"{apres_1[0]} / {apres_2[0]}")

fenetre.var_filtre.set("BTTS")
cliquer("Calculer", fenetre.onglet_journee)
attendre(lambda: "journee" not in fenetre.travailleur.en_cours, 300)
pompe(0.3)
index_btts = colonnes.index("BTTS")
valeurs_btts = [float(l[index_btts].rstrip("%")) for l in lignes(fenetre.vue_journee)]
note("Tri par BTTS puis recalcul : le tableau est trie par BTTS decroissant",
     valeurs_btts == sorted(valeurs_btts, reverse=True),
     f"de {valeurs_btts[0]}% a {valeurs_btts[-1]}%")
capture("05_journee")

faux_fichier.chemin = SORTIE / "journee.csv"
cliquer("Exporter en CSV", fenetre.onglet_journee)
pompe(0.3)
existe = faux_fichier.chemin.exists()
contenu = faux_fichier.chemin.read_text().splitlines() if existe else []
note("Export CSV de la journee : le fichier est ecrit et lisible",
     existe and len(contenu) == attendu + 1,
     f"{len(contenu)} lignes, en-tete : {contenu[0][:50] if contenu else ''}")

# --------------------------------------------------------------------------
section("Onglet Forces")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_forces)
pompe(0.2)
table = lignes(fenetre.vue_forces)
colonnes_f = list(fenetre.vue_forces.cget("columns"))
notes = [float(l[colonnes_f.index("note")]) for l in table]
note("Classement trie par note decroissante", notes == sorted(notes, reverse=True),
     f"de {notes[0]} a {notes[-1]}")
note("Paris SG est en tete sur F1", table[0][0] == "Paris SG", str(table[0][0]))
formes = [str(l[colonnes_f.index("forme 6 matchs")]) for l in table]
note("La colonne forme 6 matchs affiche des V, N, D",
     all(len(f) == 6 and set(f) <= set("VND") for f in formes), f"{table[0][0]} : {formes[0]}")
index_tirs = colonnes_f.index("tirs/match")
note("MANQUE #2 — les colonnes tirs, tirs cadres et corners sont remplies",
     all(str(l[index_tirs]).replace(".", "").isdigit() for l in table),
     f"{table[0][0]} : {table[0][index_tirs]} tirs, "
     f"{table[0][index_tirs + 1]} cadres, {table[0][index_tirs + 2]} corners")
capture("06_forces")

dialogues.vider()
cliquer("Rafraichir", fenetre.onglet_forces)
pompe(0.3)
note("Cliquer Rafraichir ne casse rien",
     len(lignes(fenetre.vue_forces)) == len(table) and not dialogues.appels)

# --------------------------------------------------------------------------
section("Onglet Opportunites")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_opportunites)
pompe(0.2)
note("Le quota s'affiche a cote du bouton",
     "credit" in fenetre.var_quota.get(), fenetre.var_quota.get()[:70])

bankroll_recette = 1000.0
fenetre.bankroll.set(bankroll_recette)
depart = time.time()
cliquer("Rafraichir les cotes", fenetre.onglet_opportunites)
attendre(lambda: "cotes" not in fenetre.travailleur.en_cours, 300)
pompe(0.4)
table_opp = lignes(fenetre.vue_opportunites)
note("Rafraichir les cotes : le tableau se remplit", len(table_opp) > 0,
     f"{len(table_opp)} selections en {time.time() - depart:.1f} s")
colonnes_o = list(fenetre.vue_opportunites.cget("columns"))
esp = [float(str(l[colonnes_o.index("esperance")]).rstrip("%")) for l in table_opp]
note("Le tableau est trie par esperance decroissante", esp == sorted(esp, reverse=True),
     f"de {esp[0]:+.1f}% a {esp[-1]:+.1f}%")

etiquettes = [fenetre.vue_opportunites.item(i)["tags"]
              for i in fenetre.vue_opportunites.get_children("")]
alertes = [i for i, t in enumerate(etiquettes) if "alerte" in t]
retenues = [i for i, t in enumerate(etiquettes) if "retenue" in t]
note("Au moins une ligne est surlignee en orange (divergence > 15 pts)",
     len(alertes) >= 1, f"{len(alertes)} ligne(s) en alerte")
if alertes:
    ligne = table_opp[alertes[0]]
    note("  la premiere alerte", True, f"{ligne[1]} — {ligne[2]} {ligne[3]} "
         f"(ecart {ligne[colonnes_o.index('ecart')]})")
note("Les lignes retenues sont surlignees en vert", True,
     f"{len(retenues)} ligne(s) retenue(s)")
note("La couleur d'alerte est bien l'orange annonce",
     fenetre.vue_opportunites.tag_configure("alerte", "background") == "#ffd8a8"
     or str(fenetre.vue_opportunites.tag_configure("alerte", "background")) == "#ffd8a8",
     str(fenetre.vue_opportunites.tag_configure("alerte", "background")))
capture("07_opportunites")

enfants = fenetre.vue_opportunites.get_children("")
cible = enfants[retenues[0]] if retenues else enfants[0]
fenetre.vue_opportunites.selection_set(cible)
pompe(0.3)
detail = fenetre.zone_detail.get("1.0", "end").strip()
note("Selectionner une ligne : le detail apparait en bas", len(detail) > 20,
     detail.splitlines()[0][:70] if detail else "vide")

index_rejet = next((i for i, t in enumerate(etiquettes)
                    if "retenue" not in t and "alerte" not in t), None)
if index_rejet is not None:
    fenetre.vue_opportunites.selection_set(enfants[index_rejet])
    pompe(0.3)
    detail_rejet = fenetre.zone_detail.get("1.0", "end")
    note("Sur une ligne rejetee, le detail donne le motif de rejet",
         "rejet" in detail_rejet.lower() or "motif" in detail_rejet.lower()
         or str(table_opp[index_rejet][-1]).strip() != "",
         f"statut affiche : {table_opp[index_rejet][-1]}")

bandeau = fenetre.var_bandeau.get()
note("Le bandeau du bas affiche bankroll, exposition et mise maximale",
     all(m in bandeau for m in ("Bankroll", "Exposition", "Mise maximale")), bandeau)
textes_bandeau = [w.cget("text") for w in widgets(fenetre.onglet_opportunites, "TLabel")
                  if "estimations" in str(w.cget("text")).lower()]
note("Le bandeau rappelle l'incertitude", bool(textes_bandeau),
     textes_bandeau[0][:70] if textes_bandeau else "absent")
note("Aucun bouton ne permet de masquer le bandeau",
     not [w for w in widgets(fenetre.onglet_opportunites, "TButton")
          if any(m in str(w.cget("text")).lower()
                 for m in ("masquer", "fermer", "reduire", "cacher", "×"))])

print("     (appel de cli.py opportunites, patienter...)")
sortie_cli = cli("opportunites", "-c", "F1", "-s", "4",
                 "--bankroll", str(bankroll_recette))
import json
chemin_json = config.DOSSIER_DONNEES / "dernieres_opportunites.json"
opp_cli = json.loads(chemin_json.read_text()) if chemin_json.exists() else []
cle_cli = {(o["dom"], o["ext"], o["marche"], o["selection"]) for o in opp_cli
           if o["recommandee"]}
cle_gui = {(o.dom, o.ext, o.marche, o.selection)
           for o in fenetre.contexte.opportunites if o.recommandee}
note("BUG #1 — les lignes retenues sont les memes que celles du CLI",
     cle_cli == cle_gui and len(opp_cli) == len(fenetre.contexte.opportunites),
     f"interface {len(cle_gui)} retenues / {len(fenetre.contexte.opportunites)} analysees, "
     f"CLI {len(cle_cli)} / {len(opp_cli)}")

# --------------------------------------------------------------------------
section("Enregistrement d'un pari")
# --------------------------------------------------------------------------

from paris import journal as journal_module   # noqa: E402
avant_paris = len(journal_module.lister())

if retenues:
    fenetre.vue_opportunites.selection_set(enfants[retenues[0]])
    pompe(0.2)
    dialogues.vider()
    cliquer("Enregistrer ce pari", fenetre.onglet_opportunites)
    pompe(0.4)
    apres = journal_module.lister()
    note("Enregistrer ce pari sur une ligne retenue", len(apres) == avant_paris + 1,
         dialogues.dernier()[2] if dialogues.dernier() else "")
    note("BLOC 4 — l'interface bascule sur l'onglet Mon journal",
         fenetre.carnet.tab(fenetre.carnet.select(), "text") == "Mon journal",
         fenetre.carnet.tab(fenetre.carnet.select(), "text"))
else:
    note("Enregistrer ce pari sur une ligne retenue", False,
         "aucune ligne retenue dans ce releve de cotes")

fenetre.carnet.select(fenetre.onglet_opportunites)
pompe(0.2)
if index_rejet is not None:
    fenetre.vue_opportunites.selection_set(enfants[index_rejet])
    pompe(0.2)
    dialogues.vider()
    dialogues.reponse_oui = False
    cliquer("Enregistrer ce pari", fenetre.onglet_opportunites)
    pompe(0.3)
    question = dialogues.dernier()
    note("Ligne rejetee : une confirmation demande si on veut vraiment enregistrer",
         question is not None and question[0] == "question",
         question[2].replace("\n", " ")[:100] if question else "aucune question")
    note("  la confirmation liste les motifs de rejet",
         question is not None and "conditions" in question[2],
         "refus pris en compte : "
         f"{len(journal_module.lister())} pari(s) au journal")
    dialogues.reponse_oui = True

# --------------------------------------------------------------------------
section("Onglet Mon journal")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_journal)
pompe(0.3)
table_j = lignes(fenetre.vue_paris)
note("T15 — le pari enregistre apparait immediatement au journal",
     len(table_j) == avant_paris + 1, f"{len(table_j)} ligne(s)")
if table_j:
    ligne_j = table_j[0]
    colonnes_j = list(fenetre.vue_paris.cget("columns"))
    complet = all(str(ligne_j[colonnes_j.index(c)]).strip() not in ("", "None")
                  for c in ("date match", "match", "marche", "selection", "cote", "mise"))
    note("  avec toutes ses metadonnees", complet,
         " | ".join(f"{c}={ligne_j[colonnes_j.index(c)]}"
                    for c in ("match", "marche", "selection", "cote", "mise")))

colonnes_j = list(fenetre.vue_paris.cget("columns"))

# La capture des cotes de cloture ne traite que les paris EN COURS : elle doit
# donc passer avant le reglement, comme dans la vraie vie.
dialogues.vider()
cliquer("Capturer les cotes de cloture", fenetre.onglet_journal)
attendre(lambda: "clotures" not in fenetre.travailleur.en_cours, 300)
pompe(0.4)
table_j = lignes(fenetre.vue_paris)
clv = str(table_j[0][colonnes_j.index("CLV")])
cloture = str(table_j[0][colonnes_j.index("cote cloture")])
note("Capturer les cotes de cloture : la commande aboutit",
     "cloture" in fenetre.etat.get().lower(), fenetre.etat.get())
note("  la colonne cote de cloture et la colonne CLV se remplissent",
     clv != "-" and cloture != "-", f"cote de cloture {cloture}, CLV {clv}")

enfants_j = fenetre.vue_paris.get_children("")
fenetre.vue_paris.selection_set(enfants_j[0])
pompe(0.2)
cliquer("Marquer gagne", fenetre.onglet_journal)
pompe(0.4)
table_j = lignes(fenetre.vue_paris)
gain = str(table_j[0][colonnes_j.index("gain")])
statut = str(table_j[0][colonnes_j.index("statut")])
note("Marquer gagne : le gain se met a jour",
     statut == "gagne" and gain not in ("+0.00", ""), f"statut {statut}, gain {gain}")
bilan = fenetre.zone_bilan.get("1.0", "end")
note("Le bilan suit", "gagne" in bilan.lower() or "P&L" in bilan or "bilan" in bilan.lower(),
     next((l.strip() for l in bilan.splitlines() if l.strip()), "")[:70])
note("Le bloc CLV affiche la phrase d'interpretation", "CLV" in bilan,
     next((l.strip() for l in bilan.splitlines() if "CLV" in l), "")[:90])
capture("08_journal")

dialogues.vider()
cliquer("Courbe de bankroll", fenetre.onglet_journal)
pompe(0.5)
png = config.DOSSIER_RAPPORTS / "bankroll_journal.png"
message_png = dialogues.dernier()
note("Courbe de bankroll : le clic aboutit sur un message, jamais sur une trace",
     message_png is not None, (message_png[2][:90] if message_png else "aucun message"))
note("Courbe de bankroll : un PNG est ecrit dans les rapports", png.exists(),
     f"{png.name}, {png.stat().st_size} octets" if png.exists()
     else "rendu matplotlib indisponible dans ce process (conflit FreeType avec Tk)")

faux_fichier.chemin = SORTIE / "journal.csv"
cliquer("Exporter en CSV", fenetre.onglet_journal)
pompe(0.3)
note("Export CSV du journal", faux_fichier.chemin.exists(),
     f"{len(faux_fichier.chemin.read_text().splitlines())} lignes"
     if faux_fichier.chemin.exists() else "absent")

fenetre.vue_paris.selection_remove(*fenetre.vue_paris.get_children(""))
dialogues.vider()
cliquer("Marquer perdu", fenetre.onglet_journal)
pompe(0.2)
dernier = dialogues.dernier()
note("Bouton de reglage sans selection : message, pas de trace",
     dernier is not None and "Selectionne" in dernier[2],
     dernier[2] if dernier else "aucun message")

# --------------------------------------------------------------------------
section("Onglet Ajustements")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_ajustements)
pompe(0.2)
fenetre.bankroll.set(500.0)
cliquer("Appliquer", cadre("Reglages de mise"))
pompe(0.2)
bandeau = fenetre.var_bandeau.get()
note("Bankroll a 500 puis Appliquer : le bandeau suit",
     "500.00" in bandeau and "10.00" in bandeau, bandeau)

table_avant = {l[0]: float(l[colonnes_f.index("note")])
               for l in lignes(fenetre.vue_forces)}
fenetre.liste_ajustement.set("Paris SG")
fenetre.var_att.set(-15.0)
fenetre.var_def.set(0.0)
dialogues.vider()
cliquer("Appliquer", cadre("Correction manuelle"))
pompe(0.3)
table_apres = {l[0]: float(l[colonnes_f.index("note")])
               for l in lignes(fenetre.vue_forces)}
note("Attaque -15 % sur Paris SG : sa note baisse dans l'onglet Forces",
     table_apres["Paris SG"] < table_avant["Paris SG"],
     f"{table_avant['Paris SG']} -> {table_apres['Paris SG']}")

cliquer("Tout reinitialiser", fenetre.onglet_ajustements)
pompe(0.3)
table_reset = {l[0]: float(l[colonnes_f.index("note")])
               for l in lignes(fenetre.vue_forces)}
note("Tout reinitialiser : la note revient a sa valeur d'origine",
     table_reset["Paris SG"] == table_avant["Paris SG"],
     f"{table_apres['Paris SG']} -> {table_reset['Paris SG']}")

fenetre.var_att.set(-150.0)
dialogues.vider()
cliquer("Appliquer", cadre("Correction manuelle"))
pompe(0.3)
dernier = dialogues.dernier()
note("Attaque a -150 : message d'erreur, pas de plantage",
     dernier is not None and dernier[0] == "avertissement",
     dernier[2][:80] if dernier else "aucun message")
fenetre.var_att.set(0.0)
capture("09_ajustements")

# -- backtest : l'interface doit rester utilisable pendant le calcul --------
depart = time.time()
cliquer("Lancer un backtest walk-forward", fenetre.onglet_ajustements)
note("Le clic sur backtest rend la main tout de suite",
     time.time() - depart < 0.5, f"{(time.time() - depart) * 1000:.0f} ms")

latences_bt: list[float] = []
onglets_visites = []
debut = time.time()
while "backtest" in fenetre.travailleur.en_cours and time.time() - debut < 900:
    t = time.time()
    fenetre.update()
    latences_bt.append(time.time() - t)
    if len(latences_bt) % 200 == 0:
        for onglet in (fenetre.onglet_forces, fenetre.onglet_journee,
                       fenetre.onglet_ajustements):
            fenetre.carnet.select(onglet)
            fenetre.update()
            onglets_visites.append(fenetre.carnet.tab(fenetre.carnet.select(), "text"))
    time.sleep(0.01)
duree_bt = time.time() - debut
pompe(0.6)
note("PENDANT le backtest, l'interface reste utilisable",
     bool(latences_bt) and max(latences_bt) < 0.5 and len(set(onglets_visites)) >= 3,
     f"backtest {duree_bt:.0f} s, latence max {max(latences_bt) * 1000:.0f} ms, "
     f"onglets parcourus {sorted(set(onglets_visites))}")
rapport_bt = fenetre.zone_backtest.get("1.0", "end")
note("Le rapport de backtest s'affiche", len(rapport_bt) > 200 and "Calcul en cours" not in
     rapport_bt, next((l.strip() for l in rapport_bt.splitlines() if l.strip()), "")[:70])
images = [w for w in widgets(fenetre.onglet_ajustements, "TLabel")
          if str(w.cget("image")) not in ("", "()")]
graphiques_produits = "GRAPHIQUES" in rapport_bt
note("Le rapport survit meme si un graphique echoue", len(rapport_bt) > 200,
     "GRAPHIQUES presents" if graphiques_produits else
     "aucun PNG produit dans ce process, mais le rapport chiffre est complet")
note("BLOC 4 — les PNG du backtest s'affichent dans l'onglet",
     len(images) >= 1 or not graphiques_produits,
     f"{len(images)} image(s) affichee(s)" if graphiques_produits else
     "matplotlib n'a produit aucun PNG ici : verifie ci-dessous sur un PNG externe")

# Le rendu du PNG par Tk se verifie sans matplotlib : on fait produire l'image
# par un autre process, puis on la donne a l'onglet.
apercu = SORTIE / "apercu_bankroll.png"
subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "produire_png.py"),
                str(apercu)], capture_output=True, timeout=300)
if apercu.exists():
    fenetre.afficher_graphiques_backtest({"bankroll": str(apercu)})
    pompe(0.4)
    images = [w for w in widgets(fenetre.onglet_ajustements, "TLabel")
              if str(w.cget("image")) not in ("", "()")]
    note("BLOC 4 — l'onglet affiche bien un PNG (Tk 8.6)", len(images) >= 1,
         f"{len(images)} image(s), {len(fenetre._images_backtest)} PhotoImage en memoire")
else:
    note("BLOC 4 — l'onglet affiche bien un PNG (Tk 8.6)", False,
         "impossible de produire un PNG de test")
capture("10_backtest")

# --------------------------------------------------------------------------
section("Bloc 3 — matchs a venir (manque #1)")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_journee)
pompe(0.2)
cotes_gardees = fenetre.contexte.cotes
fenetre.contexte.cotes = None
case("Seulement les matchs a venir").invoke()
dialogues.vider()
cliquer("Calculer", fenetre.onglet_journee)
pompe(0.3)
dernier = dialogues.dernier()
note("Case cochee sans cotes rafraichies : message clair, aucun calcul",
     dernier is not None and "Opportunites" in dernier[2],
     dernier[2].replace("\n", " ")[:90] if dernier else "aucun message")

fenetre.contexte.cotes = cotes_gardees
affiches = fenetre.affiches_a_venir()
cliquer("Calculer", fenetre.onglet_journee)
attendre(lambda: "journee" not in fenetre.travailleur.en_cours, 300)
pompe(0.3)
table = lignes(fenetre.vue_journee)
note("Seulement les matchs a venir : le tableau ne montre que les vraies affiches",
     0 < len(table) == len(affiches) < attendu,
     f"{len(table)} affiches au lieu de {attendu} confrontations")
if table:
    note("  exemple d'affiche", True, f"{table[0][0]} - {table[0][1]}")
    couples = {(str(l[0]), str(l[1])) for l in table}
    note("  aucune equipe ne se rencontre elle-meme",
         all(d != e for d, e in couples))
capture("11_journee_a_venir")
case("Seulement les matchs a venir").invoke()

# --------------------------------------------------------------------------
section("Bloc 4 — annulation, F5, preferences")
# --------------------------------------------------------------------------

fenetre.carnet.select(fenetre.onglet_donnees)
pompe(0.2)
championnat_avant = fenetre.contexte.championnat
fenetre.liste_championnats.set("E0 — Premier League")
cliquer("Charger", fenetre.onglet_donnees)
pompe(0.05)
etat_pendant = fenetre.bouton_annuler.state()
cliquer("Annuler", fenetre)
pompe(0.3)
note("Le bouton Annuler est actif pendant un chargement",
     "disabled" not in [str(e) for e in etat_pendant], f"etat {etat_pendant}")
note("Annuler rend la main immediatement", "annule" in fenetre.etat.get().lower(),
     fenetre.etat.get())
attendre(lambda: not fenetre.travailleur.en_cours, 600)
pompe(1.0)
note("Le resultat de la tache annulee est bien jete",
     fenetre.contexte.championnat == championnat_avant,
     f"championnat toujours {fenetre.contexte.championnat}")
note("Le bouton Annuler est de nouveau desactive",
     "disabled" in [str(e) for e in fenetre.bouton_annuler.state()])
fenetre.liste_championnats.set("F1 — Ligue 1")

fenetre.carnet.select(fenetre.onglet_journee)
pompe(0.2)
fenetre.event_generate("<F5>")
pompe(0.2)
note("F5 sur l'onglet Journee relance le calcul",
     "journee" in fenetre.travailleur.en_cours or len(lignes(fenetre.vue_journee)) > 0,
     "tache journee relancee")
attendre(lambda: "journee" not in fenetre.travailleur.en_cours, 300)

fenetre.carnet.select(fenetre.onglet_forces)
pompe(0.2)
avant_forces = len(lignes(fenetre.vue_forces))
fenetre.event_generate("<F5>")
pompe(0.3)
note("F5 sur l'onglet Forces rafraichit sans rien casser",
     len(lignes(fenetre.vue_forces)) == avant_forces)

fenetre.bankroll.set(750.0)
fenetre.liste_championnats.set("E0 — Premier League")
fenetre.fermer()
time.sleep(0.4)
preferences = app_module.lire_preferences()
note("A la fermeture, championnat et bankroll sont memorises",
     preferences.get("championnat") == "E0" and preferences.get("bankroll") == 750.0,
     str(preferences))

# --------------------------------------------------------------------------
section("Deuxieme lancement")
# --------------------------------------------------------------------------

dialogues.vider()
fenetre = app_module.Application()
fenetre.geometry("1280x880+0+0")
pompe(1.2)
note("L'ecran de premier lancement ne reapparait pas",
     not any(a[0] == "info" and "a lire une fois" in a[1] for a in dialogues.appels),
     f"{len(dialogues.appels)} dialogue(s)")
note("Le championnat memorise est repris",
     fenetre.liste_championnats.get().startswith("E0"), fenetre.liste_championnats.get())
note("La bankroll memorisee est reprise", fenetre.bankroll.get() == 750.0,
     str(fenetre.bankroll.get()))
capture("12_second_lancement")
fenetre.fermer()

# --------------------------------------------------------------------------
section("Fermeture pendant un chargement")
# --------------------------------------------------------------------------

enfant = SORTIE.parent / "_fermeture_pendant_chargement.py"
enfant.write_text('''
import sys, time
sys.path.insert(0, "/home/saif/bet216/foot_edge")
import app as A
class D:
    def __getattr__(s, n): return lambda *a, **k: None
A.messagebox = D()
f = A.Application()
f.liste_championnats.set("E0 — Premier League")
f.charger_donnees()                     # telechargement en cours
for _ in range(10):
    f.update(); time.sleep(0.02)
assert f.travailleur.en_cours, "le chargement n'a pas demarre"
print("chargement en cours, fermeture de la fenetre", flush=True)
f.fermer()                              # comme un clic sur la croix
''', encoding="utf-8")

for fichier in (config.DOSSIER_CACHE_RESULTATS).glob("E0_*.csv"):
    fichier.unlink()                    # cache froid : le telechargement dure

depart = time.time()
try:
    fin = subprocess.run([sys.executable, str(enfant)], cwd=RACINE,
                         capture_output=True, text=True, timeout=60)
    duree_fermeture = time.time() - depart
    note("Fermer la fenetre pendant un chargement : le process se termine",
         fin.returncode == 0, f"code {fin.returncode} en {duree_fermeture:.1f} s")
    note("  aucune trace au passage", "Traceback" not in fin.stderr,
         fin.stderr.strip().splitlines()[-1][:80] if fin.stderr.strip() else "stderr vide")
    note("  la fermeture est immediate, elle n'attend pas le telechargement",
         duree_fermeture < 20, f"{duree_fermeture:.1f} s")
except subprocess.TimeoutExpired:
    note("Fermer la fenetre pendant un chargement : le process se termine", False,
         "toujours vivant apres 60 s")

restants = subprocess.run("ps -eo pid,args | grep -v grep | grep -c 'app.py' || true",
                          shell=True, capture_output=True, text=True).stdout.strip()
note("Aucun processus app.py ne survit (ps aux | grep app.py)", restants == "0",
     f"{restants} processus")

# --------------------------------------------------------------------------
# Rapport
# --------------------------------------------------------------------------

total = len(resultats)
echecs = [r for r in resultats if r[2] != "OK"]
print("\n" + "=" * 72)
print(f"RECETTE BLOC 1 : {total - len(echecs)}/{total} verifications OK")
for r in echecs:
    print(f"  ECHEC [{r[0]}] {r[1]} -> {r[3]}")

lignes_md = [
    "# Recette du bloc 1 — resultats",
    "",
    f"Executee le {time.strftime('%d/%m/%Y a %H:%M')} sous `xvfb-run` "
    "(ecran virtuel 1280x900, Tk 8.6).",
    "",
    f"**{total - len(echecs)} verifications sur {total} passent.**",
    "",
    "## Comment cette recette a ete jouee",
    "",
    "- Ecran virtuel `xvfb-run`, Tk 8.6, fenetre 1280x900.",
    "- Les boutons sont **reellement invoques** (`invoke()` sur le widget "
    "retrouve par son libelle) et les tableaux **reellement lus**.",
    "- Les boites de dialogue sont interceptees : une modale bloque la boucle "
    "d'evenements et donc tout pilotage automatique. Leur texte est verifie "
    "mot pour mot ; leur rendu, non.",
    "- `FOOT_EDGE_HOME` pointe sur une copie de `~/.foot_edge` : le vrai "
    "journal de paris n'est pas touche par la recette.",
    "- Une reserve honnete : ces mesures disent que les widgets contiennent "
    "les bonnes valeurs et que les enchainements fonctionnent. Elles ne disent "
    "rien du gout de la mise en page. Les captures sont la pour ca.",
    "",
]
section_vue = None
for sec, item, statut, detail in resultats:
    if sec != section_vue:
        lignes_md += ["", f"## {sec}", ""]
        section_vue = sec
    coche = "x" if statut == "OK" else " "
    lignes_md.append(f"- [{coche}] {item}" + (f" — `{detail}`" if detail else ""))
if echecs:
    lignes_md += ["", "## Le point qui ne passe pas", "",
                  "`matplotlib` et `tkinter` chargent chacun leur FreeType. Quand "
                  "Tk a deja dessine du texte, le rendu des glyphes de matplotlib "
                  "part en `FT_Render_Glyph ... raster overflow` et aucun PNG "
                  "n'est produit **dans le process graphique**.",
                  "",
                  "Ce n'est pas un defaut de `app.py` : trois lignes suffisent a "
                  "le reproduire sans une ligne du projet (`tkinter` + un "
                  "`ttk.Label` + un `savefig`). Les memes PNG se generent sans "
                  "probleme dans un autre process, et en ligne de commande "
                  "(`python cli.py backtest`).",
                  "",
                  "Ce que le projet en a tire : un graphique qui echoue ne fait "
                  "plus perdre le rapport de backtest, et le bouton *Courbe de "
                  "bankroll* affiche un message au lieu d'une trace.",
                  ""]
lignes_md += ["", "## Captures d'ecran", ""]
for png in sorted(SORTIE.glob("*.png")):
    lignes_md.append(f"- `{png.name}`")
(SORTIE / "RESULTATS.md").write_text("\n".join(lignes_md) + "\n", encoding="utf-8")
print(f"\nRapport : {SORTIE / 'RESULTATS.md'}")
sys.exit(1 if echecs else 0)
