# UTILISATION — guide pratique

Ce fichier repond a une seule question : **qu'est-ce que je tape, et quand ?**

Pour l'installation, voir `README.md`. Pour les formules, voir `doc/METHODE.md`.

Tous les exemples ci-dessous sont des sorties reelles de l'outil, pas des
illustrations inventees.

---

## Sommaire

1. [Verifier que tout est en place](#1-verifier-que-tout-est-en-place)
2. [La routine hebdomadaire, en 4 commandes](#2-la-routine-hebdomadaire-en-4-commandes)
3. [Toutes les commandes, une par une](#3-toutes-les-commandes-une-par-une)
4. [L'interface graphique](#4-linterface-graphique)
4 bis. [L'interface web de consultation](#4-bis-linterface-web-de-consultation)
5. [Budget du quota API](#5-budget-du-quota-api)
6. [Automatiser la capture des cotes de cloture](#6-automatiser-la-capture-des-cotes-de-cloture)
7. [Ajouter un championnat ou une equipe](#7-ajouter-un-championnat-ou-une-equipe)
8. [Que regarder pour savoir si ca marche](#8-que-regarder-pour-savoir-si-ca-marche)
9. [Problemes courants](#9-problemes-courants)

---

## 1. Verifier que tout est en place

```bash
cd foot_edge
python cli.py diagnostic
```

```
Dossier de donnees : /home/toi/.foot_edge
Base SQLite        : /home/toi/.foot_edge/foot_edge.db
Fichier .env       : /chemin/foot_edge/.env (present)
  cle odds                 : configuree
  cle football_data_org    : configuree
  cle api_football         : configuree

Quota The Odds API : 8 credit(s) consomme(s) ce mois-ci sur 500 (4 appel(s), 2 credits chacun) — l'API annonce 492 credit(s) restant(s)
```

Si une cle est `manquante`, le message d'erreur te dira exactement quel fichier
editer et sur quel site obtenir la cle. Aucune cle n'est necessaire pour les
sections 2 (chargement), 3 (analyse) et le backtest.

```bash
python -m pytest        # 232 tests, tournent sans internet
```

---

## 2. La routine hebdomadaire, en 4 commandes

Le samedi matin, avant la journee de championnat :

```bash
# 1. Rafraichir l'historique (gratuit, sans cle API)
python cli.py charger --championnat F1 --saisons 4

# 2. Chercher les ecarts entre ton modele et le marche (2 credits API)
python cli.py opportunites --championnat F1 --bankroll 500

# 3. Enregistrer ce que tu decides de jouer
python cli.py parier --id 1 --mise 5

# 4. AVANT les coups d'envoi : capturer les cotes de cloture
python cli.py clotures
```

Puis, apres les matchs :

```bash
python cli.py regler --id 1 --resultat gagne
python cli.py bilan
```

C'est tout. Le reste de ce fichier detaille chaque commande.

> **L'etape 4 n'est pas optionnelle.** Les cotes de cloture ne sont pas
> recuperables apres coup. Si tu la sautes, tu perds definitivement ton CLV
> pour ces paris — c'est-a-dire le seul indicateur qui te dira si ton systeme
> a un avantage reel.

---

## 3. Toutes les commandes, une par une

Options communes a la plupart des commandes :

| option | defaut | role |
|---|---|---|
| `--championnat` / `-c` | `F1` | code football-data : `E0 E1 E2 SC0 SP1 SP2 I1 I2 D1 D2 F1 F2 N1 B1 P1 T1 G1` |
| `--saisons` / `-s` | `3` (4 pour backtest) | nombre de saisons d'historique |
| `--source` | `buts` | `buts`, `xg` ou `melange` |
| `--verbeux` / `-v` | — | journal detaille. **A placer AVANT la sous-commande** : `python cli.py -v cotes` |

### `charger` — telecharger et resumer

```bash
python cli.py charger --championnat F1 --saisons 4 --source melange
```

```
Ligue 1 : 918 matchs, 25 equipes, saisons 2324, 2425, 2526
xG F1 : 611/612 matchs apparies (99.8%)
Noms d'equipes reconnus par la table : 100%
```

`--source melange` telecharge en plus les xG (Understat) et affiche le taux
d'appariement. Un taux sous 90 % declenche un avertissement avec la liste des
matchs concernes.

Le cache est agressif : une saison passee n'est jamais retelechargee, la saison
en cours se rafraichit toutes les 6 heures. Sans reseau, l'outil utilise le
cache et te le dit.

### `match` — rapport complet sur une affiche

```bash
python cli.py match -c F1 --dom "paris sg" --ext marseille --cote 1.45 4.60 6.50
```

```
=== Paris SG - Marseille ===
Buts attendus : 2.45 - 1.13

1N2        : 1 65.8%  N 18.7%  2 15.5%
+2.5 buts  : 69.3%   -2.5 buts : 30.7%
BTTS       : 62.3%

Scores les plus probables :
   2-1  9.5%
   2-0  8.3%
   1-1  8.1%

Comparaison avec les cotes saisies :
   surcote du book : 6.1%
   1 @ 1.45 : marche 66.4% | modele 65.8% | esperance -4.6%
   N @ 4.60 : marche 19.9% | modele 18.7% | esperance -14.1%
   2 @ 6.50 : marche 13.7% | modele 15.5% | esperance +0.9%

Confrontations directes recentes :
   08/02/2026  Paris SG 5-0 Marseille
```

Les noms sont resolus de facon souple : `"paris sg"`, `"PSG"` et
`"Paris Saint-Germain"` marchent tous. En cas d'echec, l'outil propose les
trois noms les plus proches au lieu de planter.

`--cote 1.45 4.60 6.50` est optionnel : c'est la ou tu tapes les cotes que tu
vois chez ton bookmaker pour les comparer au modele.

### `journee` — toutes les confrontations, triees

```bash
python cli.py journee -c F1 --filtre over25 --top 5 --export /tmp/journee.csv
```

```
      dom         ext    p1    pN    p2  plus25  plus35  moins25  btts  buts_attendus
 Paris SG  St Etienne 0.912 0.062 0.026   0.852   0.698    0.148 0.505          4.750
Marseille  St Etienne 0.806 0.117 0.078   0.816   0.643    0.184 0.632          4.413
```

Filtres disponibles : `favori`, `over25`, `over35`, `under25`, `btts`, `aucun`.

Attention : ce sont **toutes** les confrontations possibles du championnat, pas
la journee reelle. C'est fait pour reperer des profils de match (« quels duels
produisent le plus de buts ? »), pas pour parier directement.

### `forces` — classement attaque / defense

```bash
python cli.py forces -c F1 -s 2
```

```
    equipe  attaque  defense  note
  Paris SG    1.560    0.751 2.077
      Lens    1.435    0.933 1.538
     Lille    1.069    0.888 1.203

Avantage du terrain (gamma) : 0.200  |  correction petits scores (rho) : -0.051
```

`attaque` = buts marques par match, toutes choses egales. `defense` = buts
encaisses : **plus c'est bas, meilleure est la defense**. `note` = attaque/defense.

### `cotes` — matchs a venir et bookmakers

```bash
python cli.py -v cotes --championnat F1
```

```
Quota The Odds API : 2 credit(s) consomme(s) ce mois-ci sur 500 — l'API annonce 498 credit(s) restant(s)
794 cotes historisees (indispensable pour le CLV).
                     date                 dom        ext  nb_bookmakers
2026-08-21 18:45:00+00:00           Marseille Strasbourg             23
2026-08-22 15:15:00+00:00                Lens    Auxerre             23
2026-08-23 18:45:00+00:00 Paris Saint-Germain     Rennes             18
```

Un appel rapporte **tous** les matchs a venir du championnat. Deux appels a
moins de 15 minutes d'intervalle ne consomment rien : le cache repond.
Ajoute `--forcer` pour ignorer le cache (et payer un nouvel appel).

### `opportunites` — la commande centrale

```bash
python cli.py opportunites --championnat F1 --bankroll 500 --seuil 0.04 --saisons 4
```

```
=== Opportunites Ligue 1 (seuil 4%, bankroll 500) ===
[1] Marseille - Strasbourg | 1N2 2 @ 5.00 (LeoVegas (SE))
  modele 28.3% | marche 21.2% | finale 23.2% | esperance +15.9%
  mise conseillee : 4.97
[2] Nice - Lorient | 1N2 2 @ 3.65 (LeoVegas (SE))
  modele 36.0% | marche 28.0% | finale 30.3% | esperance +10.7%
  mise conseillee : 5.04

--- Signaux d'alerte (divergence modele/marche, PAS des conseils) ---
  Angers - Lille plus_moins_2.5 moins : modele 68% vs marche 51%

35 selections evaluees, 4 retenue(s). Les numeros portent sur la liste
complete : ils sautent la ou une selection a ete ecartee.
Pour enregistrer : python cli.py parier --id <numero> --mise <montant>

Exposition en cours : 0.00
```

Comment lire cette sortie :

- **`modele`** : ce que dit ton Dixon-Coles. **`marche`** : la mediane
  inter-bookmakers, marge retiree. **`finale`** : le melange des deux.
- **`mise conseillee`** : Kelly/4, plafonne a 2 % de la bankroll. Sur 500 €, le
  maximum absolu par pari est donc de 10 €.
- **Les numeros sautent** parce qu'ils portent sur la liste complete, rejets
  inclus. `[3]` existe, il a simplement ete ecarte.
- **Les signaux d'alerte ne sont pas des conseils.** Un ecart de plus de
  15 points entre modele et marche signifie presque toujours qu'une information
  manque au modele (blessure, composition, match sans enjeu), pas qu'il a
  raison contre le marche.
- **Zero opportunite est le resultat normal.** Le marche est efficace. Un outil
  qui te trouve 30 « value bets » par journee se trompe.

Options : `--seuil 0.06` pour etre plus exigeant, `--a 0.3` pour changer le
poids du modele dans le melange, `--forcer` pour ignorer le cache de 15 min.

### `parier` — enregistrer au journal

```bash
python cli.py parier --id 1 --mise 5 --championnat F1
```

```
Pari #1 enregistre : Marseille - Strasbourg 1N2 2 @ 5.00, mise 5.00
```

Sans `--mise`, la mise conseillee est utilisee. `--id` correspond au numero
affiche par la derniere commande `opportunites`.

### `clotures` — capturer les cotes de cloture (le CLV)

```bash
python cli.py clotures              # rappelle l'API pour les paris en cours
python cli.py clotures --hors-ligne # utilise seulement les cotes deja en base
```

```
Cotes de cloture capturees pour 1 pari(s).
{
  "n": 1,
  "clv_moyen": 0.0643,
  "part_positive": 1.0,
  "interpretation": "CLV moyen +6.43% sur 1 pari(s), dont 100% a CLV positif. ..."
}
```

**A lancer juste avant les coups d'envoi.** Voir la section 6 pour
l'automatiser.

### `regler` et `bilan`

```bash
python cli.py regler --id 1 --resultat gagne     # ou perdu, rembourse, annule
python cli.py bilan --export /tmp/mes_paris.csv
```

```
=== BILAN DU JOURNAL ===
  paris enregistres : 3 (dont 1 en cours)
  mise totale       : 30.00
  P&L               : +12.00
  ROI               : +40.00%
  exposition en cours : 15.00

--- CLV (l'indicateur qui compte vraiment) ---
  CLV moyen +6.43% sur 1 pari(s), dont 100% a CLV positif. Le CLV compare ta
  cote a la cote de cloture equitable : c'est le seul indicateur qui converge
  assez vite pour dire si ton avantage est reel. Ton P&L sur quelques centaines
  de paris, lui, est domine par la chance.

  Rappel : 2 paris soldes, c'est trop peu pour qu'un P&L positif ou negatif
  signifie quoi que ce soit. Regarde le CLV.

--- Par marche ---
        marche  nb_paris  nb_soldes  mise_totale  pnl  roi
           1N2         2          2         30.0 12.0  0.4
```

### `backtest` — savoir si le systeme vaut quelque chose

```bash
python cli.py backtest --championnat F1 --saisons 4 --frequence 3
```

Compte 30 secondes a 2 minutes selon l'historique. Extrait de la sortie reelle :

```
1. QUALITE DES PROBABILITES (log-loss : plus bas = mieux)
   modele seul        log-loss 1.0125
   modele + marche    log-loss 0.9690
   marche de-vigge    log-loss 0.9690   <= BASELINE

3. SIMULATION DE PARIS
   paris pris        : 5
   ROI               : -100.00%   IC 95 % [-100.00% ; -100.00%] (bootstrap 1000 tirages)
   >>> Seulement 5 pari(s) : a ce volume, ni le ROI ni son intervalle ne sont
       interpretables.

4. COMPARAISON AUX BASELINES DE MARCHE
   parier le favori du marche : ROI -0.56% IC 95 % [-7.73% ; +7.09%] sur 598 paris
   -> ATTENTION : le systeme NE BAT PAS le marche (+0.0000 de log-loss).
```

Lis ce rapport de haut en bas et crois-le. Il est concu pour ne pas te flatter.
Deux PNG sont ecrits dans `~/.foot_edge/rapports/` : courbe de calibration et
courbe de bankroll.

### `calibrer` — choisir xi et w sans les deviner

```bash
python cli.py calibrer --championnat F1 --saisons 4 --source melange
```

Balayage par validation temporelle. Compte 2 a 5 minutes. Le resultat est mis
en cache dans `~/.foot_edge/calibration/hyperparametres_F1.json` et
automatiquement reutilise par toutes les autres commandes. A relancer une ou
deux fois par saison, pas plus.

---

## 4. L'interface graphique

```bash
python app.py
```

Si tkinter manque, le message te dit quoi installer
(`sudo apt install python3-tk` sur Debian/Ubuntu) et rappelle que tout est
disponible en ligne de commande.

Sept onglets :

| Onglet | Ce qu'on y fait |
|---|---|
| **Donnees** | choisir championnat / saisons / source, charger, lire le journal et le taux d'appariement xG |
| **Un match** | selectionner deux equipes, saisir des cotes, tout le detail des marches |
| **Journee** | toutes les confrontations, tableau triable par clic sur les en-tetes, export CSV |
| **Forces** | attaque / defense / note / forme sur 6 matchs |
| **Opportunites** | rafraichir les cotes, tableau trie par esperance, bouton « Enregistrer ce pari » |
| **Mon journal** | regler les paris, bilan, ROI par marche, CLV, capturer les clotures |
| **Ajustements** | corriger une equipe a la main, regler bankroll et seuil, lancer un backtest |

Trois choses a savoir :

- **Une ligne surlignee en orange** dans Opportunites = divergence de plus de
  15 points entre modele et marche. C'est un avertissement, pas une
  recommandation.
- **Le bandeau en bas d'Opportunites** (bankroll, exposition, rappel
  d'incertitude) est permanent et non masquable, par choix de conception.
- **L'interface ne se fige jamais** : tout traitement long part dans un thread
  avec barre de progression. Fermer la fenetre pendant un telechargement ne
  laisse pas de processus fantome.

Onglet **Ajustements**, cas d'usage concret : le titulaire au poste d'avant-centre
est blesse ? Selectionne l'equipe, mets `Attaque % = -15`, applique. Le modele
en tient compte immediatement dans tous les onglets. « Tout reinitialiser »
efface ces corrections.

---

## 4 bis. L'interface web de consultation

L'interface tkinter sert a travailler : charger, ajuster, enregistrer, backtester.
Pour simplement **regarder les matchs a venir**, une page web est plus commode.

```bash
python web.py                 # puis http://127.0.0.1:8000
python web.py --ouvrir        # ouvre le navigateur tout seul
python web.py --port 9000     # si le port 8000 est pris
```

Le serveur n'ecoute que sur `127.0.0.1` : rien n'est expose au reseau. Il
n'utilise que la bibliotheque standard, aucune dependance en plus.

La page montre, pour le championnat choisi :

- les matchs a venir groupes par jour, avec le 1N2 en barre, les +2.5 buts, le
  BTTS et les buts attendus ;
- les matchs **laisses de cote** et pourquoi (un promu n'a pas d'historique dans
  les saisons chargees, le modele ne peut rien en dire) ;
- les selections ou le marche semble decale, avec la mise conseillee, le motif
  de rejet quand il y en a un, et le meme code couleur que l'onglet
  Opportunites : vert retenue, orange divergence ;
- le bandeau permanent : bankroll, exposition, mise maximale, rappel
  d'incertitude.

### Choisir un jour

Le selecteur **Jour** en haut filtre les matchs et les selections sur une seule
journee. « Tous les jours » montre tout ce que le flux de cotes connait, en
general la semaine a venir.

### Toutes les ligues d'un coup

Le premier choix du selecteur, **Toutes les ligues**, fusionne les 17
championnats : les matchs sont tries a l'heure, chaque ligne porte le code de
sa ligue, et le coupon additionne tout.

Par defaut, seules les ligues **qui ont deja des cotes en cache** sont
interrogees. Les autres sont ignorees et comptees dans la ligne de rapport
(« 15 ligue(s) sans cotes en cache »). Aucun credit n'est depense.

**Rafraichir les cotes** dans ce mode interroge les 17 ligues : cela coute
**34 credits** (2 par ligue) sur les 500 du mois. Une confirmation annonce le
chiffre avant de partir, et la progression s'affiche ligue par ligue. Une ligue
qui echoue n'empeche pas les autres : elle est listee avec son erreur.

Un combine peut melanger les championnats — chaque jambe est evaluee avec le
modele de sa ligue.

### Deux avertissements que ce mode fait apparaitre

**L'exposition cumulee.** Le plafond de 2 % est *par pari*, pas par jour. En
additionnant les ligues, une journee peut engager une part importante de la
bankroll sans qu'aucun pari ne depasse son plafond. Au-dela de 10 %
(`SEUIL_ALERTE_EXPOSITION_JOUR`), un avertissement s'affiche dans le coupon.
Il n'empeche rien : etaler ou non est ta decision, mais tu dois voir le total
avant de la prendre.

**Les equipes sans historique.** Un promu qui compte deux matchs dans les
saisons chargees recoit des forces absurdes. Vu en vrai le 20/08/2026 :
Cambuur, deux matchs, defense estimee trois fois pire que la moyenne, et un
modele qui annonce 97.4 % pour Feyenoord la ou le marche dit 75.6 %. Le
garde-fou de divergence rejette bien tous ces paris — mise 0 — mais les
pourcentages restaient affiches sans reserve. Les matchs concernes portent
desormais un bandeau orange qui dit combien de rencontres l'equipe a jouees.

### Le coupon du jour

Sous le titre du tableau, un encadre resume la journee choisie : combien de
selections passent les quatre conditions, la mise totale qu'elles representent,
la part de bankroll engagee, et le gain attendu sur la duree.

Ce sont des paris **separes**. Chacun gagne ou perd de son cote, et c'est la
forme que la methode recommande.

### Le combine, et ce qu'il coute vraiment

Cocher des lignes du tableau construit un combine. Le panneau du bas donne
quatre chiffres : la cote offerte, la **cote equitable** selon le modele, les
chances que **tout** passe, et l'esperance.

Deux points sur lesquels ce calcul differe des calculateurs de combine usuels.

**La correlation est traitee exactement.** « Paris SG gagne » et « plus de 2.5
buts » dans le meme match ne sont pas independants. Multiplier leurs
probabilites donne un chiffre faux : sur ce cas, 48.9 % alors que la vraie
probabilite jointe est 54.5 %. On passe par la matrice de scores du modele, qui
porte la loi jointe. Entre matchs differents, l'independance reste supposee.

**La probabilite est recalee sur le marche**, exactement comme pour un pari
simple. Sans ce recalage le combine paraitrait bien meilleur qu'il n'est : sur
un combine reel a deux jambes, l'esperance passe de +76 % (modele brut) a
+22 % (recale), et la mise conseillee de 11.68 a 3.42.

Au-dela de trois jambes le combine est ecarte d'office, avec son motif : chaque
jambe porte la marge du bookmaker et elles se multiplient.

### Ce qu'elle coute en credits API

**Rien, tant que tu ne cliques pas sur « Rafraichir les cotes ».**

C'est deliberé. Recharger la page lit les cotes du cache disque, quel que soit
son age — l'age est affiche en haut a droite du titre. Passer par le chemin
normal aurait consomme deux credits des que le cache depasse quinze minutes,
sans que personne le demande. Le rafraichissement est un bouton, un geste
explicite, et il coute deux credits.

### Le tableau des cles API

En bas de page, un tableau donne les trois services, leur quota gratuit et ce
qu'il en reste. Il se met a jour apres chaque appel.

| Service | Quota gratuit | Compte |
|---|---|---|
| The Odds API | 500 credits / **mois** | oui, 2 credits par appel |
| football-data.org | 10 requetes / **minute** | — |
| API-Football | 100 requetes / **jour** | — |

Une precision qui compte : **seul The Odds API est appele par cet outil**. Les
deux autres cles sont demandees par `TACHES.txt` et verifiees au demarrage,
mais aucune ligne de code ne les interroge. Leur consommation est donc affichee
comme un tiret et non comme un zero : un zero laisserait croire a une
fonctionnalite qui n'existe pas.

Pour The Odds API, la colonne *Restant* montre une jauge qui se vide au fil du
mois et passe a l'orange sous 20 %. Le chiffre vient du compteur local ; le
pied de page rappelle en plus celui que l'API annonce elle-meme, les deux
doivent rester proches. Le quota gratuit repart a zero le 1er du mois.

### Ce qu'elle ne fait pas

Elle ne sert qu'a consulter. Enregistrer un pari, le solder, capturer les cotes
de cloture, lancer un backtest : c'est `app.py` ou `cli.py`.

## 5. Budget du quota API

Le plan gratuit de The Odds API donne **500 credits par mois**. L'outil demande
2 marches (1N2 et over/under 2.5) sur une region, donc :

**1 appel = 2 credits = 250 appels par mois maximum.**

Ce qui consomme :

| Action | Cout |
|---|---|
| `cotes` ou `opportunites` sur un championnat | 2 credits |
| le meme, moins de 15 min apres | **0** (cache) |
| `clotures` avec des paris en cours | 2 credits par championnat concerne |
| `charger`, `match`, `journee`, `forces`, `backtest`, `calibrer` | **0** (aucune cle requise) |

Un budget confortable : 2 championnats surveilles, 2 rafraichissements par
journee, plus une capture de clotures = 12 credits par week-end, soit environ
50 par mois. Tu as de la marge.

Le compteur est persistant, se remet a zero le 1er du mois, lit les en-tetes
`x-requests-used` / `x-requests-remaining` de l'API (qui font foi) et t'avertit
a l'approche de la limite. `python cli.py diagnostic` l'affiche a tout moment.

---

## 6. Automatiser la capture des cotes de cloture

C'est la seule chose qu'il faut vraiment automatiser, parce qu'une occasion
manquee est perdue pour toujours.

**Linux / macOS** — `crontab -e` :

```cron
# Tous les jours a 18h50, avant les coups d'envoi de 19h00 et 21h00
50 18 * * * cd /chemin/vers/foot_edge && /usr/bin/python3 cli.py clotures >> ~/.foot_edge/clotures.log 2>&1
```

**Windows** — Planificateur de taches, action « Demarrer un programme » :

```
Programme  : C:\Python313\python.exe
Arguments  : cli.py clotures
Repertoire : C:\chemin\vers\foot_edge
```

S'il n'y a aucun pari en cours, la commande ne consomme rien et se termine
immediatement.

---

## 7. Ajouter un championnat ou une equipe

Les 5 grands championnats et l'Eredivisie sont deja couverts (151 equipes en
table). Pour les autres, ou quand un promu arrive :

```bash
# Comparer les noms entre football-data et le flux de cotes (2 credits)
python outils/verifier_noms.py --championnat P1 --source odds_api

# Comparer avec Understat, pour les xG (gratuit mais lent)
python outils/verifier_noms.py --championnat SP1 --source understat

# Valider les propositions une par une, au clavier
python outils/verifier_noms.py --championnat P1 --source odds_api --ajouter
```

La sortie separe clairement quatre categories :

```
  Appariements surs : 14
    OK   Utrecht      = FC Utrecht      -> Utrecht

  A VERIFIER A LA MAIN : 1
    ?    For Sittard  ~ Fortuna Sittard (0.641)

  Deja resolus sans entree en table (3) :
    auto SC Cambuur   -> Cambuur
    (normalisation deterministe : rien a saisir pour ceux-la)

  Sans correspondance cote odds_api : 0

Taux d'appariement : 100.0% (14/14 noms odds_api resolus)
```

**Ne valide jamais une proposition dont tu n'es pas sur.** L'algorithme a
propose un jour `Ath Madrid ~ Real Madrid` (similarite 0.59) : ce sont deux
clubs differents, et accepter cet appariement aurait fausse tout le modele
espagnol sans qu'aucun controle de coherence ne le detecte — les probabilites
auraient continue de sommer a 1. Un trou vaut toujours mieux qu'une fausse
correspondance.

Un promu sans historique en premiere division n'est de toute facon pas predit :
ses matchs sont ignores et signales dans le journal. C'est voulu.

---

## 8. Que regarder pour savoir si ca marche

Dans cet ordre de fiabilite, du plus solide au plus trompeur :

1. **Le CLV moyen** (`cli.py bilan`). Positif de facon repetee sur ~100 paris =
   ton avantage est reel. C'est le seul signal rapide et honnete.
2. **La log-loss du backtest** comparee au marche de-vigge. Si ton modele ne
   descend pas sous celle du marche, il ne sert qu'a reperer des ecarts.
3. **La courbe de calibration** (`~/.foot_edge/rapports/calibration_*.png`).
   Elle doit suivre la diagonale : parmi les matchs annonces a 60 %, environ
   60 % doivent se produire.
4. **Le ROI avec son intervalle de confiance.** Si l'intervalle contient zero,
   il ne dit rien du tout. Sur 200 paris, il contient presque toujours zero.
5. **Le P&L brut.** Ne prouve rien avant plusieurs milliers de paris.

Chiffres reellement mesures sur 4 saisons, pour te situer (log-loss, plus bas
= mieux) :

| championnat | modele sur buts | 0.7·xG + 0.3·buts | marche de-vigge |
|---|---|---|---|
| Ligue 1 | 1.0125 | — | **0.9690** |
| La Liga | 0.9897 | 0.9692 | **0.9558** |
| Serie A | 0.9889 | 0.9797 | **0.9584** |

Autrement dit : les xG comblent environ 60 % de l'ecart au marche, et le marche
gagne quand meme. Le poids optimal du modele dans le melange ressort a **0** sur
ces trois championnats. Utilise cet outil pour reperer des ecarts et mesurer ton
CLV, pas en esperant battre la cote de cloture.

---

## 9. Problemes courants

### « Aucune donnee pour F1 saison 2627 »

En debut de saison, football-data.co.uk ne publie le fichier de la saison qui
commence qu'apres les premieres journees, et pas le meme jour pour tous les
championnats. Le serveur repond alors une page HTML « 300 Multiple Choices »
au lieu du CSV.

Ce n'est pas une panne et le message ne s'affiche plus : l'absence est notee
une fois, redemandee au plus toutes les six heures, et le modele travaille avec
les saisons precedentes. `python web.py --verbeux` ou `python cli.py -v` le
montre si tu veux le verifier.

Si le message apparait pour une **saison passee**, la c'est un vrai probleme :
verifie ta connexion, ou que ce championnat existait bien cette saison-la.



| Symptome | Cause et solution |
|---|---|
| `Aucune donnee pour F1` | pas de reseau et cache vide. Relance quand la connexion revient ; les saisons deja telechargees restent utilisables hors ligne. |
| `Cle API manquante pour The Odds API` | edite `.env` (le message donne le chemin exact et l'URL d'inscription). Les commandes d'analyse fonctionnent sans cle. |
| `tkinter n'est pas disponible` | `sudo apt install python3-tk`, ou reste en ligne de commande. |
| `Equipe introuvable : 'Parris SGG'` | l'outil propose les 3 noms les plus proches ; recopie celui qui convient. |
| `Championnat inconnu : ZZ` | la liste complete des 17 codes est affichee juste en dessous. |
| `Quota epuise` | attends le 1er du mois. L'outil continue de fonctionner sur les dernieres cotes en cache. |
| Aucune opportunite trouvee | **c'est normal et fréquent.** Le marche est efficace. Baisse `--seuil` seulement si tu sais pourquoi. |
| `X equipe(s) du flux de cotes sans correspondance` | voir section 7. Leurs matchs sont ignores, pas devines. |
| `Taux inferieur a 90 %` sur les xG | table de noms incomplete pour ce championnat : `python outils/verifier_noms.py --championnat XX --source understat`. |
| Le backtest ne prend que 5 paris | attendu quand le poids du melange ressort a 0 : les probabilites finales sont celles du marche, donc l'esperance depasse rarement 4 %. |
| `unrecognized arguments: -v` | `--verbeux` est une option globale : elle se place **avant** la sous-commande (`python cli.py -v cotes`). |

Ou trouver les fichiers :

```
~/.foot_edge/
  foot_edge.db          journal de paris + historique des cotes
  cache/                CSV et JSON telecharges
  calibration/          hyperparametres retenus par championnat
  rapports/             graphiques PNG du backtest
  quota_odds_api.json   compteur de credits du mois
```

Tout supprimer et repartir de zero : `rm -rf ~/.foot_edge` (tu perds ton
journal de paris et ton historique de cotes — donc ton CLV).

---

Outil d'analyse statistique. Les probabilites produites sont des estimations
avec une marge d'erreur reelle, pas des pronostics. Les paris sportifs
comportent un risque de perte financiere et d'addiction.
