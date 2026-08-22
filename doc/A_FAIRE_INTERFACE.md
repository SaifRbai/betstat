# A FAIRE — interface graphique (app.py)

**Etat au 20/08/2026 au soir : tout est fait, ce fichier n'a plus de « a
faire ».** Il est conserve comme compte rendu.

L'interface a tourne pour de vrai sous `xvfb-run` : les 7 onglets ont ete
pilotes, les boutons cliques, les tableaux lus. **les 97 verifications
passent** ; le detail et les captures d'ecran sont dans `doc/recette/`. Sept
defauts ont ete trouves en executant l'interface, et corriges — aucun n'etait
visible a la relecture.

Ce qui suit garde la structure d'origine : les blocs 2, 3 et 4 tels qu'ils
avaient ete identifies par relecture, puis la recette du bloc 1, puis le
compte rendu de ce qui a change.

<details>
<summary>Etat initial, le matin du 20/08/2026</summary>

Tout le reste du projet etait termine et verifie ; seule l'interface graphique
n'avait jamais ete executee. `tkinter` n'etait pas installe sur la machine de
developpement et `sudo` y demande un mot de passe interactif. Le fichier
compilait, s'importait sans erreur avec un tkinter bouchon, aucune de ses 50
methodes ne depassait 60 lignes, mais aucun pixel n'avait jamais ete affiche.

</details>

---

## Bloc 0 — Debloquer (5 minutes) — FAIT

```bash
sudo apt install python3-tk        # Debian / Ubuntu
# sudo dnf install python3-tkinter # Fedora

cd foot_edge
python -c "import tkinter; print(tkinter.TkVersion)"   # doit afficher 8.6 ou plus
python app.py
```

Si la fenetre s'ouvre, le bloc 1 est une checklist de 10 minutes.
Si elle ne s'ouvre pas, le message d'erreur est le point de depart.

> Sur une machine sans serveur graphique (SSH, WSL sans WSLg), il faut un
> affichage : `export DISPLAY=:0`, ou passer par `xvfb-run python app.py` pour
> un test purement fonctionnel.

---

## Bloc 1 — Checklist de recette — FAIT (voir `doc/recette/`)

A cocher une par une. Chaque ligne correspond a un critere d'acceptation de
T14 ou T15 dans `TACHES.txt`.

### Onglet Donnees

- [ ] La liste des 17 championnats s'affiche, `F1 — Ligue 1` est preselectionne.
- [ ] Cliquer **Charger** avec `saisons = 4`, `source = buts`.
- [ ] **Pendant le chargement, la fenetre reste deplacable et cliquable.**
      C'est le critere d'acceptation principal de T14. Si elle se fige, le
      thread ne fait pas son travail.
- [ ] La barre de progression du bas s'anime, puis s'arrete.
- [ ] Le journal affiche : nombre de matchs, equipes, saisons, gamma, rho.
- [ ] Recommencer avec `source = melange` : le taux d'appariement xG doit
      s'afficher (attendu : 99.8 % a 100 % sur les 5 grands championnats).
- [ ] Bouton **Verifier les noms d'equipes** : sur F1 il doit dire que toutes
      les equipes sont dans la table.
- [ ] **Fermer la fenetre pendant un chargement.** Verifier ensuite avec
      `ps aux | grep app.py` qu'aucun processus ne survit.

### Onglet Un match

- [ ] Les deux listes deroulantes sont remplies apres le chargement.
- [ ] `Paris SG` vs `Marseille` : verifier que le 1N2 correspond a
      `python cli.py match -c F1 -s 4 --dom "Paris SG" --ext Marseille`.
      Le `-s 4` n'est pas un detail : l'interface charge 4 saisons, le CLI en
      prend 3 par defaut. Sans lui on compare deux historiques differents et on
      croit avoir retrouve le bug #1.
- [ ] Saisir les cotes `1.45 / 4.60 / 6.50` puis Analyser : la surcote et les
      trois esperances apparaissent.
- [ ] Saisir une cote invalide (`0.5`, ou du texte) : message lisible, pas de trace.
- [ ] Choisir deux fois la meme equipe : message, pas de plantage.

### Onglet Journee

- [ ] **Calculer**, case « Seulement les matchs a venir » decochee : le tableau
      se remplit (n×(n−1) lignes, soit 306 pour un championnat a 18).
- [ ] Cliquer sur l'en-tete `+2.5` : le tri s'inverse a chaque clic.
- [ ] Changer le tri en `BTTS` et recalculer.
- [ ] **Exporter en CSV** puis ouvrir le fichier.
- [ ] Cocher « Seulement les matchs a venir » sans avoir rafraichi les cotes :
      message qui renvoie vers l'onglet Opportunites, pas de trace.
- [ ] Rafraichir les cotes (onglet Opportunites), revenir ici, recocher la case
      et **Calculer** : seules les vraies affiches restent, une dizaine de lignes.

### Onglet Forces

- [ ] Classement trie par note decroissante, `Paris SG` en tete sur F1.
- [ ] La colonne `forme 6 matchs` affiche bien des `V`, `N`, `D`.
- [ ] Les colonnes `tirs/match`, `tirs cadres/match` et `corners/match` sont
      remplies (un tiret est normal sur un championnat qui ne les publie pas).
- [ ] Cliquer **Rafraichir** ne casse rien.

### Onglet Opportunites

- [ ] Le quota s'affiche a cote du bouton (`... credit(s) consomme(s) ...`).
- [ ] **Rafraichir les cotes** : le tableau se remplit, trie par esperance
      decroissante.
- [ ] Comparer avec `python cli.py opportunites -c F1 --bankroll <la meme>` :
      **les memes lignes doivent apparaitre** (voir bug #1).
- [ ] Une ligne au moins doit etre **surlignee en orange** (divergence > 15 pts).
      Sur le releve du 19/08, `Angers - Lille` en `-2.5 buts` l'etait.
- [ ] Les lignes retenues sont surlignees en vert.
- [ ] Selectionner une ligne : le detail apparait dans la zone du bas, avec le
      motif de rejet s'il y en a un.
- [ ] **Le bandeau du bas** affiche bankroll, exposition, mise maximale, et le
      rappel d'incertitude. Verifier qu'il n'existe aucun moyen de le masquer.
- [ ] **Enregistrer ce pari** sur une ligne retenue : l'interface doit basculer
      toute seule sur l'onglet *Mon journal*.
- [ ] Essayer sur une ligne rejetee : une confirmation doit demander si on veut
      vraiment l'enregistrer, en listant les motifs.
- [ ] Sans avoir charge de championnat, cliquer Rafraichir : message clair.

### Onglet Mon journal

- [ ] Le pari enregistre a l'etape precedente **y apparait immediatement**,
      avec toutes ses metadonnees. C'est le critere d'acceptation de T15.
- [ ] **Marquer gagne** : le gain se met a jour, le bilan aussi.
- [ ] Le bloc CLV affiche la phrase d'interpretation.
- [ ] **Capturer les cotes de cloture** : sur un pari dont les cotes ont ete
      historisees, la colonne CLV doit se remplir.
- [ ] **Courbe de bankroll** : un PNG est ecrit dans `~/.foot_edge/rapports/`.
- [ ] **Exporter en CSV**.
- [ ] Cliquer un bouton de reglage sans rien selectionner : message, pas de trace.

### Onglet Ajustements

- [ ] Changer la bankroll a `500`, cliquer **Appliquer**, revenir sur
      Opportunites : le bandeau et les mises conseillees ont suivi.
- [ ] Selectionner une equipe, `Attaque % = -15`, Appliquer : sa note baisse
      dans l'onglet Forces.
- [ ] **Tout reinitialiser** : la note revient a sa valeur d'origine.
- [ ] Saisir `-150` en attaque : message d'erreur, pas de plantage.
- [ ] **Lancer un backtest** : l'interface reste utilisable pendant le calcul
      (il dure 30 s a 2 min), puis le rapport s'affiche, et les deux PNG
      (calibration, bankroll) apparaissent sous le rapport.

### Nouveautes du bloc 4

- [ ] Pendant un chargement, le bouton **Annuler** de la barre d'etat s'active ;
      cliquer dessus rend la main tout de suite et le tableau ne change plus.
- [ ] `F5` sur les onglets Journee, Forces, Opportunites et Mon journal relance
      bien le rafraichissement de l'onglet visible.
- [ ] Changer de championnat et de bankroll, fermer, relancer : les deux valeurs
      sont retrouvees (`~/.foot_edge/preferences.json`).

### Premier lancement

- [ ] Supprimer `~/.foot_edge/.premier_lancement_vu` puis relancer : l'ecran de
      rappel doit reapparaitre (plafond de mise, CLV vs P&L, marge d'erreur).
- [ ] Relancer une seconde fois : il ne doit plus apparaitre.

---

## Bloc 2 — Bugs identifies par relecture — FAIT

Ces quatre points ont ete trouves par audit statique, pas par execution. Le
premier est le seul qui ait un effet visible pour l'utilisateur.

### Bug #1 — L'interface ignore les hyperparametres calibres

**Gravite : reelle.** L'interface et la ligne de commande donnent des chiffres
differents pour les memes donnees.

`cli.py` lit le cache de calibration produit par `cli.py calibrer` :

```python
# cli.py:81
parametres = calibration.charger_hyperparametres(championnat) or {}
xi_retenu = xi if xi is not None else parametres.get("xi", dc.XI_DEFAUT)
poids = parametres.get("w", dc.POIDS_XG_DEFAUT)
return dc.entrainer(matchs, cible=cible, poids_xg=poids, xi=xi_retenu)
```

`app.py` ne le lit pas :

```python
# app.py:314, dans charger_donnees()
modele = dc.entrainer(matchs, cible=source)     # xi = XI_DEFAUT = 0.0018
```

Or la calibration a retenu `xi = 0.003` pour F1 et E0. Les probabilites de
l'interface sont donc calculees avec un autre oubli du passe que celles du CLI.

Ecart mesure sur 4 saisons de Ligue 1 (`xi` interface = 0.0018, CLI = 0.003) :

| affiche | interface | ligne de commande | ecart max sur le 1N2 |
|---|---|---|---|
| Paris SG - Marseille | 65.3 % | 65.8 % | 0.68 pt |
| Lens - Auxerre | 56.7 % | 57.9 % | 1.20 pt |

L'ecart est modeste mais il grandira a mesure que la calibration s'eloignera du
defaut, et surtout il rend impossible de recouper l'interface avec le CLI.

**Correctif** : reutiliser la fonction du CLI plutot que de dupliquer la
logique. Dans `charger_donnees`, remplacer la ligne par :

```python
from cli import entrainer_modele          # en haut du fichier
...
modele = entrainer_modele(matchs, source, championnat)
```

**Verification** : le 1N2 affiche dans l'onglet *Un match* doit etre identique,
au centieme pres, a celui de `python cli.py match -c F1 --dom "Paris SG" --ext Marseille`.

### Bug #2 — `rename` sans effet

`app.py:840` : `courbe.rename(columns={"bankroll": "bankroll"})` renomme une
colonne en elle-meme. Vestige d'une refonte. A supprimer.

### Bug #3 — `self.var_championnat` est du code mort

Cree dans `_construire_donnees`, jamais lu : c'est `self.liste_championnats`
qui sert. A supprimer pour eviter qu'on croie un jour qu'il pilote quelque chose.

### Bug #4 — Les references d'onglets ne servent a rien

`self.onglet_donnees`, `self.onglet_match`, ... sont affectes puis jamais
relus. Soit les supprimer, soit s'en servir pour activer un onglet par
programme (par exemple basculer sur *Mon journal* apres l'enregistrement d'un
pari, ce qui serait un vrai plus d'ergonomie).

---

## Bloc 3 — Ce qui manque par rapport a la specification — FAIT

### Manque #1 — L'onglet Journee n'affiche pas les vraies affiches

`TACHES.txt` T14 dit : « liste d'affiches **ou** toutes confrontations ».
Seule la seconde moitie est faite : on calcule les n×(n−1) duels possibles, ce
qui sert a reperer des profils de match mais ne correspond a aucune journee
reelle.

Les vraies affiches sont pourtant deja disponibles sans requete
supplementaire : `data.cotes.matchs_a_venir(cotes)` les renvoie a partir du
cache.

**Piste** : ajouter une case a cocher « seulement les matchs a venir » qui
filtre le tableau sur les couples renvoyes par `matchs_a_venir`. Une dizaine
de lignes.

### Manque #2 — L'onglet Forces n'affiche pas les statistiques brutes

T14 demande « stats brutes ». On affiche aujourd'hui attaque, defense, note,
forme, buts marques et encaisses. Les colonnes tirs, tirs cadres et corners
existent dans `data/resultats.py` (`tirs_dom`, `tirs_cadres_dom`,
`corners_dom`, ...) mais ne sont pas remontees.

**Piste** : ajouter trois colonnes moyennes par match dans `afficher_forces`.

### Manque #3 — Aucun test automatise sur app.py

Les 146 tests du projet ne couvraient pas une seule ligne de l'interface.
Un test d'import avec un tkinter bouchon attraperait deja les erreurs de
definition et les attributs manquants. Point de depart, a mettre dans
`tests/test_app.py` :

```python
"""Tests de app.py sans serveur graphique : tkinter est remplace par un bouchon."""

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def app_module(monkeypatch):
    for nom in ("tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog"):
        module = types.ModuleType(nom)
        module.__getattr__ = lambda n: MagicMock()
        monkeypatch.setitem(sys.modules, nom, module)
    tk = sys.modules["tkinter"]
    tk.Tk = type("Tk", (), {"__init__": lambda s: None,
                            "__getattr__": lambda s, n: MagicMock()})
    tk.ttk = sys.modules["tkinter.ttk"]
    monkeypatch.delitem(sys.modules, "app", raising=False)
    import app
    return app


def test_import_sans_serveur_graphique(app_module):
    assert hasattr(app_module, "Application")


def test_ecran_de_premier_lancement_contient_les_garde_fous(app_module):
    message = app_module.MESSAGE_PREMIER_LANCEMENT
    assert "2 %" in message or "2%" in message      # plafond de mise
    assert "CLV" in message                          # CLV avant P&L
    assert "ESTIMATIONS" in message.upper()          # marge d'erreur


def test_aucun_libelle_interdit(app_module):
    import pathlib
    texte = pathlib.Path("app.py").read_text().lower()
    for interdit in ("match sur", "banker", "pronostic garanti"):
        assert interdit not in texte


def test_forme_sur_six_matchs(app_module):
    import pandas as pd
    matchs = pd.DataFrame({
        "dom": ["A", "B", "A"], "ext": ["B", "A", "C"],
        "buts_dom": [2, 0, 1], "buts_ext": [1, 0, 3]})
    forme, marques, encaisses = app_module.Application._forme(matchs, "A")
    assert forme == "VND"
    assert (marques, encaisses) == (2 + 0 + 1, 1 + 0 + 3)
```

**Ces quatre tests ont ete executes et passent tels quels** : le fichier est
copiable sans retouche.

**Attention** : ils ne verifient pas le rendu. Ils attrapent les erreurs de
definition, pas les fenetres vides. La checklist du bloc 1 reste indispensable.

---

## Bloc 4 — Confort — FAIT

- [x] Basculer automatiquement sur l'onglet *Mon journal* apres l'enregistrement
      d'un pari (utilise les references du bug #4).
- [x] Memoriser le dernier championnat et la derniere bankroll entre deux
      sessions (`~/.foot_edge/preferences.json`).
- [x] Bouton **Annuler** pendant un chargement long.
- [x] Afficher les deux PNG du backtest directement dans l'onglet Ajustements.
- [x] Raccourci clavier `F5` pour rafraichir l'onglet actif.

---

## Rappel : ce qui est deja fait et verifie

Pour ne pas refaire ce travail par erreur.

| | Etat |
|---|---|
| Les 18 taches T01 a T18 | implementees |
| 232 tests | passent, **reseau coupe** (`python -m pytest`) |
| Aucune fonction > 60 lignes | verifie |
| Aucune cle API en dur | verifie par recherche |
| Aucun libelle « match sur » / « banker » / « pronostic garanti » | verifie |
| Plafond de mise 2 %, Kelly/4 | non contournable par parametre, teste |
| Backtest avec IC bootstrap et 2 baselines | verifie sur donnees reelles |
| xG apparies sur les 5 grands championnats | 99.8 % a 100 % |
| Table de noms | 151 equipes |
| CLV | valide de bout en bout sur cotes reelles |
| CLI, 13 sous-commandes | toutes testees a la main |
| `doc/UTILISATION.md` | 20 commandes documentees, toutes verifiees par parsing |

Le seul trou restant est l'**execution** de l'interface graphique (blocs 0 et 1).

---

## Ce qui a ete fait le 20/08/2026

`python -m pytest` : **232 tests**, reseau coupe. Aucune fonction de `app.py`
ne depasse 60 lignes. Et, depuis le soir du 20/08, l'interface a reellement
tourne : voir plus bas, blocs 0 et 1.

### Bloc 2 — les quatre bugs

| | Correctif |
|---|---|
| #1 hyperparametres ignores | `app.py` importe `entrainer_modele` de `cli.py` et l'appelle dans `charger_donnees`. Plus aucun appel a `dc.entrainer` dans `app.py` : interface et CLI passent par le meme code, donc par le meme `xi` calibre. |
| #2 `rename` sans effet | supprime. |
| #3 `self.var_championnat` | supprime. |
| #4 references d'onglets | elles servent : `activer_onglet()` bascule sur *Mon journal* apres l'enregistrement d'un pari, et `rafraichir_onglet_actif()` alimente le F5. |

### Bloc 3 — les trois manques

- **Affiches reelles** : case a cocher « Seulement les matchs a venir » dans
  l'onglet *Journee*. Elle filtre sur `data.cotes.matchs_a_venir()`, dont les
  noms sont ramenes a ceux du modele par `noms.resoudre_vers`. Aucune requete
  supplementaire : on relit les cotes deja recuperees. Si elles ne l'ont pas
  ete, un message renvoie vers *Opportunites*.
- **Stats brutes** : trois colonnes de plus dans *Forces* — tirs, tirs cadres
  et corners par rencontre. Un tiret quand le championnat ne les fournit pas.
- **Tests** : `tests/test_app.py`, 36 tests avec un tkinter bouchon. Les quatre
  du bloc 3 sont repris tels quels ; s'y ajoutent la construction complete de la
  fenetre (les 7 onglets) et le declenchement des 16 actions **sans championnat
  charge**, qui doivent toutes afficher un message et ne lancer aucun calcul.

### Bloc 4 — confort

Bascule automatique vers *Mon journal*, `~/.foot_edge/preferences.json`
(championnat, bankroll, saisons, source), bouton **Annuler** dans la barre
d'etat, PNG du backtest affiches dans *Ajustements*, `F5` sur l'onglet actif.

Sur l'annulation, une precision honnete : un thread Python ne s'interrompt pas
de l'exterieur. `Travailleur.annuler()` jette le resultat et rend la main tout
de suite ; le telechargement deja lance va a son terme dans un thread daemon,
qui ne survit pas au process.

### Bloc 0 — tkinter, sans les droits d'administrateur

`sudo` demande un mot de passe interactif sur cette machine. Contournement :
recuperer les paquets et les deplier dans le repertoire personnel.

```bash
apt-get download python3.14-tk libtk8.6 libxss1 xvfb x11-xkb-utils
for d in *.deb; do dpkg-deb -x "$d" ~/.local/opt/python-tk; done

export PREFIX=~/.local/opt/python-tk
export PATH="$PREFIX/usr/bin:$PATH"
export PYTHONPATH="$PREFIX/usr/lib/python3.14:$PREFIX/usr/lib/python3.14/lib-dynload"
export LD_LIBRARY_PATH="$PREFIX/usr/lib/x86_64-linux-gnu"
export TK_LIBRARY="$PREFIX/usr/share/tcltk/tk8.6"
export TCL_LIBRARY=/usr/share/tcltk/tcl8.6

python -c "import tkinter; print(tkinter.TkVersion)"      # 8.6
xvfb-run -a --server-args="-screen 0 1280x900x24" python app.py
```

Sur une machine ou l'on peut administrer, `sudo apt install python3-tk` rend
tout ce bloc inutile.

### Bloc 1 — la recette a ete jouee

**97 verifications sur 97.** Le detail ligne a ligne, les valeurs relevees et
les captures d'ecran sont dans `doc/recette/` (`RESULTATS.md`).

Quelques resultats qui valent d'etre cites :

| | |
|---|---|
| Critere T14 | pendant un telechargement reel (cache vide), la fenetre se deplace, change d'onglet, et la boucle d'evenements repond en moins de 200 ms |
| Bug #1 | `Paris SG - Marseille` : `65.8 / 19.0 / 15.2` a l'interface **et** au CLI ; les 35 selections d'opportunites et les 4 retenues sont les memes des deux cotes |
| Alerte orange | `Angers - Lille` en `-2.5 buts`, ecart 16.1 pts — exactement la ligne annoncee par le releve du 19/08 |
| CLV | pari enregistre a 5.00, cote de cloture capturee a 4.70, CLV **+6.43 %** |
| Critere T15 | le pari apparait au journal dans la seconde, avec ses metadonnees |
| Backtest | 29 s, latence max 28 ms pendant le calcul, les trois onglets restent parcourables |
| Fermeture en plein chargement | le process rend la main en 1.2 s, sans trace, aucun processus survivant |
| xG | 917/918 matchs apparies (99.9 %) |

### Sept defauts trouves en executant, et corriges

1. **La colonne `statut` etait hors d'atteinte.** Les douze colonnes des
   opportunites font 1320 px, la fenetre par defaut 1180. Il manquait un
   ascenseur horizontal — et c'est le motif de rejet qui disparaissait.
   `tableau()` a maintenant ses deux ascenseurs.
2. **Une page HTML mise en cache sous un nom `.csv`.** Quand une saison n'est
   pas encore publiee, football-data repond « 300 Multiple Choices » avec une
   page HTML. `raise_for_status()` laisse passer un 3xx et la page contient des
   virgules : elle passait le controle. Quatre fichiers `*_2627.csv` du cache
   etaient dans ce cas. Le controle regarde desormais le contenu, et un cache
   deja pollue se purge tout seul.
3. **Un graphique rate faisait perdre tout le backtest.** Une exception de
   matplotlib remontait a travers `backtester()` : deux minutes de calcul
   perdues pour une image. Le rapport chiffre survit maintenant a l'echec du
   dessin.
4. **Un `after()` en attente se declenchait apres `destroy()`**, et Tk se
   plaignait d'un « invalid command name ..._sonder » a la fermeture.
   `Travailleur.fermer()` annule le rappel programme.
5. **`ecrire_preferences` pouvait empecher la fermeture** de la fenetre si la
   serialisation echouait.
6. **Aucun PNG n'etait produit** tant que matplotlib etait charge apres
   tkinter — voir la section suivante, c'est la plus instructive.
7. **Les deux graphiques du backtest debordaient de la fenetre** et se
   retrouvaient rognes : ils sont maintenant reduits de moitie et la bande qui
   les porte reserve sa hauteur avant le rapport.

### Le plus interessant : les PNG que matplotlib refusait de dessiner

Au premier passage, aucun graphique ne sortait : ni la courbe de bankroll du
journal, ni les deux PNG du backtest. Toujours la meme erreur,
`FT_Render_Glyph ... raster overflow`.

La cause, apres coup, est simple. `matplotlib` (installe par pip) **embarque sa
propre FreeType, 2.14.3, et en exporte les 160 symboles `FT_*`**. `tkinter`,
lui, charge celle du systeme, 2.14.2, via Xft. Les deux ne peuvent pas coexister
sans se marcher dessus : **la premiere arrivee dans le process l'emporte pour
tout le monde**. Chargee apres tkinter, matplotlib appelait donc une FreeType
d'une autre version que celle de ses structures.

Trois verifications pour en etre sur :

| | |
|---|---|
| `nm -D ft2font*.so` | 160 symboles `FT_*` **definis** et exportes, zero indefini : matplotlib porte bien sa propre copie |
| `ctypes.CDLL("libfreetype.so.6", RTLD_GLOBAL)` puis un `savefig`, **sans tkinter du tout** | meme erreur : ni tkinter ni le montage a la main ne sont en cause |
| le meme `savefig` **avant** de charger FreeType | passe |

D'ou le correctif, trois lignes en tete de `app.py` : importer
`matplotlib.ft2font` **avant** `tkinter`. Le simple chargement du `.so` suffit a
lui donner la priorite ; il est garde par un `try/except ImportError` puisque
matplotlib reste optionnel. Un test verifie l'ordre des deux imports dans le
source, parce que c'est le genre de ligne qu'une reorganisation deplace sans y
penser, et que ca ne se voit qu'a l'execution.

Depuis, les 97 verifications passent et les deux PNG du backtest s'affichent
dans l'onglet Ajustements.

### Ce que ces tests ne prouvent pas

Les tests a tkinter bouchon ne prouvent rien de ce qui s'affiche : un bouchon
accepte n'importe quelle geometrie et n'a jamais refuse un widget. C'est la
recette du bloc 1 qui valide T14 et T15, et elle a tourne.

Ce que la recette elle-meme ne prouve pas : le gout de la mise en page. Le
pilotage automatique verifie que les widgets contiennent les bonnes valeurs et
que les enchainements marchent. Les treize captures de `doc/recette/` sont la
pour le reste — et c'est en les regardant qu'on a vu la colonne `statut`
coupee.

---

Outil d'analyse statistique. Les probabilites produites sont des estimations
avec une marge d'erreur reelle, pas des pronostics. Les paris sportifs
comportent un risque de perte financiere et d'addiction.
