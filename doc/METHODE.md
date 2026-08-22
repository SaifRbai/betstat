# METHODE — les formules, pour se relire dans 6 mois

Ce document existe pour qu'on puisse reprendre le projet sans relire le code.
Chaque section donne la formule, l'endroit ou elle est implementee, et la raison
du choix.

---

## 1. Modele de Dixon-Coles

Fichier : `modele/dixon_coles.py`

### Intensites

Pour un match entre l'equipe `i` a domicile et `j` a l'exterieur :

```
lambda_dom = exp( attaque_i + defense_j + gamma )
lambda_ext = exp( attaque_j + defense_i )
```

- `attaque_k` : capacite offensive de l'equipe k (en log-echelle)
- `defense_k` : **propension a encaisser** de l'equipe k. Un `defense` eleve =
  une mauvaise defense. La convention inverse existe ailleurs : attention.
- `gamma` : avantage du terrain, commun a tout le championnat. Sur la Ligue 1
  recente on trouve `gamma ~ 0.20`, soit **+22 % de buts attendus a domicile**.

### Vraisemblance

Les deux scores suivent des Poisson independantes, corrigees sur les petits
scores :

```
L = produit_matchs  P(X = x ; lambda_dom) * P(Y = y ; lambda_ext) * tau(x, y)
```

avec la correction de Dixon-Coles :

```
tau(0,0) = 1 - lambda_dom * lambda_ext * rho
tau(0,1) = 1 + lambda_dom * rho
tau(1,0) = 1 + lambda_ext * rho
tau(1,1) = 1 - rho
tau(x,y) = 1                       pour tous les autres scores
```

`rho` capture le fait que les 0-0 et 1-1 sont plus frequents que ne le
prevoient deux Poisson independantes (les equipes ajustent leur jeu selon le
score). On trouve typiquement `rho` entre -0.10 et 0. Il est borne a +/- 0.35
pour garantir `tau > 0`.

### Ponderation temporelle

Chaque match est pondere par :

```
w_t = exp( -xi * nombre_de_jours_ecoules )
```

`xi = 0` donne des poids uniformes. `xi = 0.002` correspond a une demi-vie
d'environ **347 jours** (`ln 2 / 0.002`). Le choix de `xi` n'est pas fait a la
main : voir section 3.

### Cible d'entrainement : buts, xG, ou melange

`cible = "buts" | "xg" | "melange"`. En mode melange :

```
y = w * xg + (1 - w) * buts        (w = 0.7 par defaut)
```

Les xG ne sont pas entiers. On utilise donc une **vraisemblance de Poisson
continue**, ou `log(k!)` est remplace par `lgamma(k+1)` :

```
log p(k | lambda) = k * log(lambda) - lambda - lgamma(k + 1)
```

C'est une densite valide (a une constante de normalisation pres) et elle
coincide exactement avec la Poisson discrete quand `k` est entier.

Pourquoi les xG : un but est un evenement rare et tres bruite (une frappe
deviee change un resultat). Les xG mesurent la qualite des occasions creees.
Un modele entraine sur les xG converge avec 2 a 3 fois moins de matchs.

La correction `tau` reste calculee sur les **buts entiers reellement marques**,
meme quand la partie Poisson est entrainee sur les xG : `rho` decrit la
dependance des scores observes, pas celle des xG.

### Estimation

Maximisation par `scipy.optimize.minimize(method="L-BFGS-B")`. Le modele est
invariant par translation (`attaque_k + c`, `defense_k - c` donne les memes
intensites) : on ne pose donc aucune contrainte pendant l'optimisation, et on
**recentre les attaques a posteriori** sur une moyenne nulle.

Option `force_domicile_separee` : 4 parametres par equipe au lieu de 2
(`att_dom`, `att_ext`, `def_dom`, `def_ext`). Desactivee par defaut — doubler
le nombre de parametres n'est utile qu'avec beaucoup de donnees.

### Prediction

On construit une matrice 12x12 de probabilites de score exact, on applique
`tau` sur les quatre cases concernees, on renormalise, puis **tout en est
derive** : 1N2 (sommes des triangles), double chance, over/under 0.5 a 4.5,
BTTS, clean sheet, score exact, handicap asiatique de -2 a +2, buts attendus.

### Ajustement manuel

`ajuster(equipe, attaque_pct, defense_pct)` ajoute `log(1 + pct/100)` au
parametre concerne : `attaque_pct = +20` multiplie bien `lambda` par 1.20.

---

## 2. De-vig : extraire la probabilite vraie du marche

Fichier : `modele/calibration.py`

Une cote decimale `c` correspond a une probabilite implicite brute `1/c`. Comme
le bookmaker prend une marge, `somme(1/c_i) > 1`. L'exces est la **surcote** :

```
surcote = somme(1/c_i) - 1
```

Sur un 1N2 grand public elle vaut 4 a 8 %. Il faut la retirer avant toute
comparaison avec le modele. Trois methodes sont implementees.

### Multiplicative

```
p_i = (1/c_i) / somme_j(1/c_j)
```

Simple, mais elle repartit la marge proportionnellement alors que les
bookmakers en chargent davantage sur les outsiders (biais favori-outsider).
Elle **surestime donc les outsiders et sous-estime le favori**.

### Puissance

On cherche `k` tel que :

```
somme_i (1/c_i)^k = 1
```

Comme `somme(1/c_i) > 1`, on a `k > 1`. Elever a une puissance `k > 1` reduit
davantage les petites probabilites que les grandes : le favori recupere une
partie de ce que le multiplicatif lui avait retire. Resolution par `brentq`.

### Shin

Modele ou la marge s'explique par une proportion `z` de parieurs mieux
informes. En notant `pi_i = 1/c_i` et `S = somme(pi_i)` :

```
p_i(z) = ( sqrt( z^2 + 4 (1-z) * pi_i^2 / S ) - z ) / ( 2 (1-z) )
```

On cherche par bissection le `z` tel que `somme_i p_i(z) = 1`. C'est la methode
la plus juste sur les favoris extremes. **C'est la methode par defaut**, avec
repli automatique sur la methode puissance si la resolution ne converge pas.

### Ordre des trois methodes

Sur l'exemple 2.10 / 3.40 / 3.30 (surcote 7.33 %), la probabilite attribuee au
favori vaut :

| methode | p(favori) |
|---|---|
| multiplicative | 44.37 % |
| Shin | 44.96 % |
| puissance | 45.18 % |

soit **multiplicative < Shin < puissance**. Le critere d'acceptation de
`TACHES.txt` annonce l'inverse pour Shin ; c'est incompatible avec sa propre
description du biais multiplicatif ("surestime les outsiders"). Le test
`tests/test_calibration.py::test_ordre_des_methodes_sur_le_favori` verifie la
relation mathematique reelle.

### Consensus inter-bookmakers

`consensus()` applique le de-vig **bookmaker par bookmaker** (chacun a sa propre
marge), prend la **mediane** des probabilites obtenues, puis renormalise.

C'est l'estimateur de reference du projet : la mediane inter-books bat en
pratique n'importe quel modele public. Elle sert aussi de baseline dans le
backtest et de reference pour le CLV.

---

## 3. Choix des hyperparametres

Fichier : `modele/calibration.py`, fonction `optimiser_hyperparametres`

Balayage de `xi` dans {0, 0.0005, 0.001, 0.002, 0.003, 0.005} et de `w` dans
{0, 0.3, 0.5, 0.7, 1.0}, par **validation temporelle glissante** : on entraine
sur tout ce qui precede strictement la journee testee, on predit cette journee,
on avance. Jamais de decoupage aleatoire — cela reviendrait a s'entrainer sur
le futur.

Critere minimise : la **log-loss** sur le 1N2.

```
log_loss = - (1/n) * somme_t log( p_t(issue reellement observee) )
```

Pas la precision : la precision ignore la confiance. Annoncer un vainqueur a
51 % ou a 99 % donne la meme precision, alors que ce n'est pas du tout la meme
information. La log-loss punit la confiance mal placee.

Resultat mis en cache par championnat dans
`~/.foot_edge/calibration/hyperparametres_XX.json`.

Ordre de grandeur mesure sur 4 saisons, `xi = 0.002`, walk-forward avec
reentrainement toutes les 5 journees :

| championnat | buts | 0.7*xG + 0.3*buts | xG pur | marche de-vigge |
|---|---|---|---|---|
| Ligue 1 | 1.0125 | — | — | 0.9690 |
| La Liga | 0.9897 | **0.9692** | 0.9701 | 0.9558 |
| Serie A | 0.9889 | **0.9797** | 0.9809 | 0.9584 |

Deux enseignements concrets :

1. **Les xG apportent vraiment quelque chose** : -0.0205 de log-loss sur la
   Liga, -0.0092 sur la Serie A. C'est le seul changement du projet qui
   ameliore nettement les predictions, comme annonce.
2. **Le melange w = 0.7 bat les xG purs** sur les deux championnats. Garder
   30 % de buts reels est utile : les xG ignorent la finition, qui est une
   competence reelle meme si elle est bruitee.

Et malgre cela, le marche reste devant. C'est le resultat attendu.

---

## 4. Melange modele + marche

Fichier : `modele/calibration.py`

```
p_finale = normaliser( p_modele^a * p_marche^(1-a) )
```

Melange **geometrique** et non arithmetique : plus stable sur des probabilites,
et il ne cree pas de masse la ou les deux sources s'accordent pour dire
"improbable".

`a` n'est pas choisi a la main : il est calibre par backtest sur la meme grille
walk-forward. La valeur attendue est 0.2 a 0.4 — autrement dit **le marche pese
plus que ton modele**, ce qui est normal. Sur la Ligue 1 recente, le backtest de
ce projet retient souvent `a = 0` : le modele n'apporte rien de plus que le
marche. La fonction l'affiche explicitement au lieu de le cacher.

Les trois probabilites (`p_modele`, `p_marche`, `p_finale`) sont **toujours
renvoyees separement** : l'ecart entre modele et marche est precisement
l'information recherchee, et le melange l'effacerait.

Une calibration isotonique optionnelle (algorithme PAVA, implemente sans
sklearn) corrige la sur-confiance residuelle : elle apprend une fonction
croissante `f` telle que `f(p_annonce)` colle a la frequence observee.

---

## 5. Backtest

Fichier : `modele/backtest.py`

### Walk-forward strict

Entrainement sur tout ce qui precede la journee J, prediction de J, avance.
Re-entrainement toutes les N journees (parametrable). Aucune fuite du futur.
Une equipe absente du modele (promu sans historique) n'est pas predite : on ne
devine pas.

### ROI et son intervalle de confiance

```
ROI = somme(gains) / somme(mises)
```

Un ROI sans intervalle ne veut rien dire. On le complete par un **bootstrap** de
1000 re-echantillonnages avec remise des paris, dont on prend les percentiles
2.5 et 97.5.

**Si l'intervalle contient zero, le rapport ecrit noir sur blanc que le
resultat n'est pas distinguable du hasard.** Un avertissement supplementaire
apparait en dessous de 30 paris.

### Drawdown maximum

```
drawdown = max_t ( (sommet_t - bankroll_t) / sommet_t )
```

ou `sommet_t` est le maximum courant de la bankroll.

### Courbe de calibration

Les probabilites sont rangees en 10 buckets ; pour chaque bucket on compare la
probabilite moyenne annoncee a la frequence reellement observee. Une bonne
calibration suit la diagonale. Sortie PNG dans `~/.foot_edge/rapports/`.

### Deux baselines obligatoires

1. **Parier le favori du marche** a mise constante — ROI avec intervalle.
2. **La probabilite du marche de-viggee** — log-loss et Brier.

Si le systeme ne bat pas ces baselines, le rapport le dit en toutes lettres.

---

## 6. Detection de valeur et mise

Fichier : `paris/valeur.py`

### Esperance

```
esperance = p_finale * cote - 1
```

### Quatre conditions cumulatives

Une opportunite n'est signalee que si **toutes** sont reunies :

1. `esperance > seuil` (4 % par defaut)
2. la cote proposee est **strictement superieure a la mediane inter-books**
3. au moins **3 bookmakers** cotent ce marche (sinon pas de consensus)
4. `|p_modele - p_marche| <= 15 points de pourcentage`

La condition 4 merite une explication : un ecart superieur a 15 points n'est
presque jamais une opportunite, c'est le signe qu'une information manque au
modele (blessure, composition, match sans enjeu). Le systeme leve donc une
**alerte**, pas une recommandation, et la ligne est surlignee en orange dans
l'interface.

Quand une opportunite est rejetee, la fonction renvoie toujours **la raison du
rejet** : on veut comprendre, pas obtenir un booleen.

### Kelly fractionnaire

Fraction de Kelly complete :

```
f = ( p * (c - 1) - (1 - p) ) / (c - 1)
```

Elle maximise la croissance logarithmique de la bankroll **si p est exact**.
Comme p ne l'est jamais tout a fait, le Kelly complet ruine statistiquement le
joueur. On applique donc :

```
mise = min( f / 4 , 0.02 ) * bankroll
```

Exemple : `p = 0.55`, `c = 2.10`

- esperance = `0.55 * 2.10 - 1` = **+15.5 %**
- Kelly complet = `(0.55*1.10 - 0.45) / 1.10` = **14.09 %**
- Kelly/4 = **3.52 %**
- plafonne a 2 % → sur une bankroll de 1000, la mise est de **20.00**

Le plafond de 2 % est lu dans `config.PLAFOND_MISE_BANKROLL` et n'est expose par
**aucun parametre de fonction** : il n'est pas contournable.
(Le critere d'acceptation de `TACHES.txt` annonce 4.8 % pour le Kelly/4 de cet
exemple ; le calcul donne 3.52 %. Dans les deux cas le plafond de 2 % s'applique
et la mise finale est identique.)

---

## 7. CLV — Closing Line Value

Fichier : `paris/journal.py`

```
clv = cote_prise / cote_de_cloture_devigguee - 1
```

La cote de cloture est la derniere disponible avant le coup d'envoi. On ne
compare **pas** a la cote brute : elle contient la marge du bookmaker, et tout
le monde afficherait un CLV negatif par construction. On compare a la **cote
equitable** :

```
cote_cloture_devigguee = 1 / mediane_inter_books( p_devigguee )
```

### Pourquoi c'est la metrique centrale

La cote de cloture est l'estimation la plus precise disponible : elle integre
toute l'information du marche jusqu'a la derniere minute. Si tu prends
regulierement des cotes meilleures qu'elle, ton avantage est reel — meme si ton
P&L est negatif sur la periode, ce qui arrive tres souvent par simple variance.

Ordre de grandeur : il faut **plusieurs milliers de paris** pour qu'un P&L
devienne concluant, contre **une centaine** pour un CLV. C'est la difference
entre savoir si le systeme marche et esperer qu'il marche.

### Contrainte pratique

Le CLV n'est calculable que si les cotes ont ete **historisees avant le coup
d'envoi**. Elles ne sont pas recuperables apres coup. D'ou :

- `data/cotes.py` historise systematiquement tout ce qu'il recupere dans la
  table SQLite `cotes_historique` ;
- `capturer_clotures()` doit etre lancee juste avant les matchs, manuellement ou
  par tache planifiee :

```bash
# crontab -e : tous les jours a 18h50
50 18 * * * cd /chemin/vers/foot_edge && python cli.py clotures
```

Le bilan affiche la moyenne, l'ecart-type et le pourcentage de paris a CLV
positif, avec une phrase d'interpretation destinee a l'utilisateur.

---

## 8. Correspondance des noms d'equipes

Fichier : `data/noms.py`

Regle unique et non negociable : **on n'apparie jamais deux noms sur la seule
foi d'un score de similarite**. Sont acceptes :

- l'egalite exacte,
- l'egalite apres normalisation typographique (accents, casse, ponctuation),
- une entree explicite de `data/noms_equipes.json`.

Tout le reste part dans une liste `a_verifier` destinee a un humain. Le score de
similarite combine `difflib.SequenceMatcher` sur le "noyau" du nom (mots
generiques de club retires) et le recouvrement des mots :

```
score = max( 0.6 * ratio_chaine + 0.4 * recouvrement_mots , 0.9 * inclusion )
```

Il ne sert qu'a **classer des suggestions**, jamais a valider. Une fausse
correspondance pollue silencieusement tout le modele ; un trou, lui, se voit.

### Resolution vers les equipes d'un modele

`resoudre_vers(nom, candidats)` fait le pont entre le nom qu'emploie un
bookmaker et celui qu'emploie football-data.co.uk, avec quatre niveaux
**tous deterministes** :

1. egalite exacte ;
2. meme nom canonique dans `noms_equipes.json` — c'est ainsi que le
   `ADO Den Haag` d'un book devient le `Den Haag` des CSV ;
3. egalite apres normalisation typographique ;
4. egalite apres retrait des mots generiques de club : `FC Utrecht` et
   `Utrecht` se ramenent tous deux a `utrecht`.

Le niveau 4 n'est accepte que s'il designe **un seul** candidat. Des que deux
candidats se ramenent a la meme forme, la fonction renvoie `None` : une
ambiguite doit remonter, pas se resoudre au hasard. Aucun score de similarite
n'intervient a aucun niveau.

Exemple concret de ce que cela change : sur l'Eredivisie, le flux de cotes
nomme trois clubs `ADO Den Haag`, `NEC Nijmegen` et `Fortuna Sittard` la ou
football-data ecrit `Den Haag`, `Nijmegen` et `For Sittard`. Sans ce mecanisme,
leurs matchs etaient purement ignores (20 selections evaluees au lieu de 35).
Les cas non resolus sont journalises, jamais avales en silence.

### Un piege reel

En remplissant la table pour La Liga, l'algorithme a propose
`Ath Madrid ~ Real Madrid` avec une similarite de 0.59. Ce sont deux clubs
differents. Accepte en silence, cet appariement aurait fausse l'integralite du
modele espagnol sans qu'aucun test de coherence ne le detecte : les
probabilites auraient continue de sommer a 1. C'est la raison d'etre de la
regle. Un test de non-regression garde ce cas
(`tests/test_noms.py::test_ath_madrid_n_est_pas_real_madrid`).

---

Outil d'analyse statistique. Les probabilites produites sont des estimations
avec une marge d'erreur reelle, pas des pronostics. Les paris sportifs
comportent un risque de perte financiere et d'addiction.
