# foot_edge — analyse statistique du football et aide a la decision

Application Python locale : historiques de 17 championnats europeens, xG reels,
modele de Dixon-Coles, cotes multi-bookmakers de-viggees, detection d'ecarts,
mise de Kelly fractionnaire et journal de paris avec suivi du CLV.
Interface tkinter (7 onglets) et ligne de commande equivalente.

---

## Ce que cet outil ne fait pas

Cette section est la premiere du README parce que c'est la plus importante.

**Il ne bat pas le bookmaker sur le 1N2 des grands championnats.**
Un modele de Dixon-Coles entraine sur les scores obtient de facon realiste
50 a 55 % de 1N2 corrects, pour un ROI compris entre -5 % et +1 %. La marge du
bookmaker est de 5 a 8 %. Le backtest de ce projet le mesure et l'affiche sans
maquillage. Log-loss hors echantillon reellement mesuree sur 4 saisons
(plus bas = mieux) :

| championnat | modele sur buts | modele sur 0.7*xG+0.3*buts | marche de-vigge |
|---|---|---|---|
| Ligue 1 (F1) | 1.0125 | — | **0.9690** |
| La Liga (SP1) | 0.9897 | 0.9692 | **0.9558** |
| Serie A (I1) | 0.9889 | 0.9797 | **0.9584** |

Les xG comblent environ 60 % de l'ecart au marche, mais **le marche gagne
encore**. Le poids optimal du modele dans le melange (`a`) ressort a **0** sur
les trois championnats : le backtest dit que le modele n'ajoute rien au marche.
C'est ecrit tel quel dans le rapport, pas dissimule.

**Il ne remplace pas le marche.** La cote de cloture est l'estimateur le plus
precis qui existe et bat tous les modeles publics. Ce logiciel sert a reperer
les endroits ou le marche *semble* decale, pas a le corriger.

**Il ne donne aucune certitude.** Aucun ecran n'affiche de "pronostic garanti",
de "match sur" ni de "banker" — ces formulations sont interdites dans le code
et verifiees par recherche automatique. Toute probabilite affichee est une
estimation assortie d'une marge d'erreur reelle.

**Il ne lit pas de donnees d'entrainement de club** (GPS, charge de travail,
etat de forme interne). Ces donnees n'existent pas publiquement. Toute fonction
qui pretendrait les lire serait un bug.

**Il ne scrape aucun site de bookmaker** ni FlashScore, SofaScore, Betclic,
Winamax, Unibet ou Bet365. Sources utilisees : football-data.co.uk (CSV libres),
soccerdata (FBref / Understat), The Odds API.

**Il ne prouve rien avec ton P&L.** Sur quelques centaines de paris, le P&L est
domine par la chance. Le seul indicateur honnete a court terme est le **CLV**
(Closing Line Value) : prends-tu des cotes meilleures que la cote de cloture ?
C'est ce que mesure l'onglet *Mon journal*.

Enfin : les paris sportifs comportent un risque de perte financiere et
d'addiction. La mise conseillee est un Kelly divise par 4, plafonne a 2 % de la
bankroll, et **ce plafond n'est modifiable par aucun parametre**.

---

## Installation

Il faut Python 3.10 ou plus recent.

```bash
cd foot_edge
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

`tkinter` fait partie de la bibliotheque standard mais n'est pas toujours
installe sur Linux :

```bash
sudo apt install python3-tk        # Debian / Ubuntu
sudo dnf install python3-tkinter   # Fedora
```

Sans tkinter, **tout reste accessible en ligne de commande** (`python cli.py --help`).

## Les trois cles API

Aucune cle n'est necessaire pour l'analyse statistique pure (chargement des
resultats, modele, backtest sur cotes historiques). Elles ne servent qu'aux
cotes en temps reel.

| Cle | A quoi elle sert | Ou l'obtenir | Quota gratuit |
|---|---|---|---|
| `ODDS_API_KEY` | cotes multi-bookmakers, CLV | <https://the-odds-api.com/#get-access> | 500 requetes/mois |
| `FOOTBALL_DATA_ORG_KEY` | calendrier et resultats officiels | <https://www.football-data.org/client/register> | gratuit |
| `API_FOOTBALL_KEY` | blessures et compositions | <https://www.api-football.com/pricing> | 100 requetes/jour |

```bash
cp .env.example .env
# puis edite .env et remplace "votre_cle_ici" par tes cles
```

Verifie avec `python cli.py diagnostic` : la commande affiche les chemins, les
cles configurees et le quota consomme ce mois-ci.

## Premiere utilisation en 5 etapes

```bash
# 1. Telecharger l'historique et verifier que tout va bien
python cli.py charger --championnat F1 --saisons 4

# 2. Analyser une affiche, en comparant a des cotes vues chez ton book
python cli.py match --championnat F1 --dom "Paris SG" --ext Marseille --cote 1.45 4.60 6.50

# 3. Savoir si le systeme vaut quelque chose (le plus important)
python cli.py backtest --championnat F1 --saisons 4

# 4. Recuperer les cotes du moment et chercher des ecarts
python cli.py opportunites --championnat F1 --bankroll 500

# 5. Suivre ses paris et surtout son CLV
python cli.py parier --id 1 --mise 10
python cli.py clotures        # a lancer AVANT les coups d'envoi
python cli.py bilan
```

Interface graphique : `python app.py`.
Consultation dans le navigateur : `python web.py`, puis http://127.0.0.1:8000.
Le selecteur « Championnat » y propose deux vues transversales : **Aujourd'hui —
toutes ligues**, qui n'entraine que les championnats qui jouent le jour meme, et
**Toutes les ligues**, qui les parcourt tous. Les championnats y sont regroupes
par pays.

### Matchs de coupe entre deux divisions

Un match comme Arsenal - Coventry City oppose deux divisions differentes :
aucun modele de championnat ne peut comparer ses deux equipes. `modele/
inter_divisions.py` entraine alors un modele commun a toutes les divisions du
pays (E0+E1+E2+E3+EC pour l'Angleterre). Ce sont les equipes qui changent de
division au fil des promotions et relegations qui mettent ces divisions a la
meme echelle ; sans au moins trois d'entre elles entre deux divisions, on
refuse d'estimer plutot que d'inventer. Six pays sont eligibles : Angleterre,
Ecosse, Espagne, Italie, Allemagne, France.

Ces estimations sont marquees « inter-divisions » dans la page : **ni la
calibration ni le ROI mesures sur un championnat ne s'y appliquent**, et les
comptes annexes (corners, cartons) y sont volontairement absents.

**Guide pratique complet : [`doc/UTILISATION.md`](doc/UTILISATION.md)** — chaque
commande avec sa sortie reelle, la routine hebdomadaire, le budget du quota API,
l'automatisation des cotes de cloture et les problemes courants.

## Sur le telephone (Android)

Les calculs restent sur le PC ; le telephone affiche l'interface web.

```bash
python web.py --reseau            # affiche l'adresse a ouvrir sur le telephone
./android/construire.sh           # une fois : construit android/build/RAB3OUNSTAT.apk
```

Sur le telephone (meme Wi-Fi), ouvrir `http://<ip-du-pc>:8000/RAB3OUNSTAT.apk`
dans Chrome, installer (autoriser les « sources inconnues » pour Chrome),
puis lancer RAB3OUNSTAT et entrer l'adresse du PC. `--reseau` ouvre la page a
tout le reseau local, rafraichissement des cotes compris : a eviter sur un
Wi-Fi public. La construction demande le SDK Android dans `~/Android/Sdk`
(platforms;android-35, build-tools;35.0.0) et Java 17.

## Les 7 onglets

| Onglet | Contenu |
|---|---|
| Donnees | championnat, nombre de saisons, source (buts / xG / melange), journal de chargement, taux d'appariement des xG |
| Un match | rapport complet, comparaison a des cotes saisies, confrontations directes, scores probables |
| Journee | toutes les confrontations, tableau triable, filtres, export CSV |
| Forces | attaque / defense / note, forme sur 6 matchs |
| Opportunites | matchs a venir, p modele vs p marche, ecart, meilleure cote, esperance, mise conseillee |
| Mon journal | paris, reglage des resultats, P&L, ROI par marche, courbe de bankroll, **CLV** |
| Ajustements | corrections manuelles par equipe, bankroll, seuil d'esperance, backtest |

Tout traitement long tourne dans un thread daemon : l'interface ne se fige
jamais, et fermer la fenetre pendant un telechargement ne laisse pas de thread
zombie.

## Structure du projet

```
foot_edge/
  app.py                interface tkinter (7 onglets)
  web.py                interface web de consultation (bibliotheque standard)
  web/page.html         la page servie par web.py
  cli.py                meme chose en ligne de commande
  config.py             chemins, constantes, lecture des cles API
  data/
    resultats.py        football-data.co.uk : telechargement, cache, nettoyage
    xg.py               soccerdata : xG FBref / Understat, appariement
    cotes.py            The Odds API : quota, cache, historisation SQLite
    noms.py             correspondance des noms d'equipes entre sources
    noms_equipes.json   la table elle-meme (Ligue 1 et Premier League pre-remplies)
  modele/
    dixon_coles.py      modele, cible buts / xg / melange
    calibration.py      hyperparametres, de-vig, melange modele/marche
    backtest.py         walk-forward, bootstrap, baselines, graphiques
  paris/
    valeur.py           detection de valeur, Kelly fractionnaire plafonne
    combine.py          combine : probabilite jointe exacte, recalage marche
    journal.py          journal SQLite, P&L, CLV
  outils/
    verifier_noms.py    aide au remplissage de la table de noms
  tests/                232 tests, tous hors ligne
  doc/UTILISATION.md    guide pratique : quoi taper, et quand
  doc/METHODE.md        les formules
```

Donnees : `~/.foot_edge/` (base SQLite `foot_edge.db`, caches, rapports PNG).
La variable d'environnement `FOOT_EDGE_HOME` permet de deplacer ce dossier.

## Tests

```bash
python -m pytest
```

Les 133 tests tournent **avec le reseau coupe**, sur des fixtures locales.
Aucun n'appelle une API ni ne touche a `~/.foot_edge` : un dossier temporaire
est utilise a la place.

## Limites connues

- **xG** : disponibles uniquement pour les 5 grands championnats, et depuis
  2017 environ. Ailleurs, `data/xg.py` renvoie un tableau vide et le signale.
  **Understat est la source primaire** : FBref n'expose pas les xG par match via
  soccerdata pour tous les championnats, et limite fortement le debit.
- **Table des noms** : 151 equipes, couvrant les **5 grands championnats**
  (tous ceux qui ont des xG) plus l'Eredivisie. Taux d'appariement xG mesure :
  99.8 % a 100 % selon le championnat. Pour les 11 championnats restants, la
  resolution deterministe de `data/noms.py` couvre deja les variantes de
  prefixe (`FC Utrecht` -> `Utrecht`) ; les noms qui resistent sont
  **journalises**, jamais devines, et se completent avec
  `python outils/verifier_noms.py --championnat XX`.
  Exemple reel du danger : l'algorithme a propose `Ath Madrid ~ Real Madrid`
  (similarite 0.59). Accepte en silence, cela aurait fausse tout le modele
  espagnol sans qu'aucun test de coherence ne le detecte.
- **Quota Odds API** : 500 requetes par mois. Une requete couvre tous les matchs
  d'un championnat, le cache dure 15 minutes, et un compteur local avertit a 400.
- **CLV** : ne peut etre calcule que si les cotes ont ete historisees **avant**
  le coup d'envoi. Elles ne sont pas recuperables retroactivement. Lance
  `python cli.py clotures` (ou une tache planifiee) avant les matchs.
- **Backtest** : les CSV historiques ne contiennent pas le detail par bookmaker.
  Les conditions "au moins 3 books" et "cote au-dessus de la mediane" de la
  detection de valeur ne peuvent donc pas y etre verifiees : le backtest est
  volontairement plus pessimiste que la realite sur ce point.
- **Nombre de paris declenches** : tres faible, par construction. C'est normal,
  et c'est meme rassurant. Un outil qui trouve 30 "value bets" par journee se
  trompe.

---

Outil d'analyse statistique. Les probabilites produites sont des estimations
avec une marge d'erreur reelle, pas des pronostics. Les paris sportifs
comportent un risque de perte financiere et d'addiction.
