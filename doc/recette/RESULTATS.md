# Recette du bloc 1 — resultats

Executee le 20/08/2026 a 10:08 sous `xvfb-run` (ecran virtuel 1280x900, Tk 8.6).

**97 verifications sur 97 passent.**

## Comment cette recette a ete jouee

- Ecran virtuel `xvfb-run`, Tk 8.6, fenetre 1280x900.
- Les boutons sont **reellement invoques** (`invoke()` sur le widget retrouve par son libelle) et les tableaux **reellement lus**.
- Les boites de dialogue sont interceptees : une modale bloque la boucle d'evenements et donc tout pilotage automatique. Leur texte est verifie mot pour mot ; leur rendu, non.
- `FOOT_EDGE_HOME` pointe sur une copie de `~/.foot_edge` : le vrai journal de paris n'est pas touche par la recette.
- Une reserve honnete : ces mesures disent que les widgets contiennent les bonnes valeurs et que les enchainements fonctionnent. Elles ne disent rien du gout de la mise en page. Les captures sont la pour ca.


## Premier lancement

- [x] L'ecran de rappel apparait au premier lancement — `foot_edge — a lire une fois`
- [x] Il rappelle le plafond de mise 2 %
- [x] Il rappelle le CLV avant le P&L
- [x] Il rappelle la marge d'erreur
- [x] Le marqueur .premier_lancement_vu est ecrit

## Onglet Donnees

- [x] La liste des 17 championnats s'affiche — `17 entrees`
- [x] F1 — Ligue 1 est preselectionne — `F1 — Ligue 1`
- [x] saisons = 4, source = buts — `4 saisons / buts`
- [x] Le clic sur Charger rend la main tout de suite — `108 ms`
- [x] PENDANT le chargement, la fenetre se deplace (critere T14) — `(0, 0) -> (140, 60)`
- [x] PENDANT le chargement, on change d'onglet (critere T14)
- [x] PENDANT le chargement, la boucle d'evenements reste fluide — `169 tours, latence max 170 ms, chargement 2.5 s`
- [x] La barre de progression s'anime pendant le chargement — `91 valeurs distinctes`
- [x] Le bouton Annuler s'active pendant le chargement — `etats vus : {(), ('disabled',)}`
- [x] Le chargement aboutit — `Ligue 1 : 918 matchs charges.`
- [x] La barre de progression s'arrete
- [x] Le bouton Annuler se desactive a la fin
- [x] Le journal affiche matchs, equipes, saisons, gamma, rho — `Modele entraine : gamma 0.189, rho -0.062, convergence oui`
- [x] Le volume de donnees est coherent (306 matchs par saison a 18 clubs) — `918 matchs, 22 clubs distincts sur 4 saisons`
- [x] Verifier les noms : toutes les equipes de F1 sont dans la table — `Toutes les equipes sont dans la table de correspondance.`
- [x] source = melange : le taux d'appariement xG s'affiche — `apparies (99.9%`
- [x] Le taux d'appariement est entre 99.8 % et 100 % — `99.9 %`
- [x] Le rechargement en melange aboutit — `Ligue 1 : 918 matchs charges. en 2.8 s`

## Onglet Un match

- [x] Les deux listes deroulantes sont remplies apres le chargement — `22 equipes (4 saisons de Ligue 1 : 18 clubs par saison, 22 ont joue au moins une des 4)`
- [x] Paris SG - Marseille : le 1N2 s'affiche — `1   65.8%     N   19.0%     2   15.2%`
- [x] BUG #1 — le 1N2 de l'interface est identique a celui du CLI — `interface ('65.8', '19.0', '15.2') | CLI ('65.8', '19.0', '15.2')`
- [x] Cotes 1.45 / 4.60 / 6.50 : la surcote s'affiche — `surcote 6.1%`
- [x] Les trois esperances apparaissent — `-4.6% / -12.5% / -1.1%`
- [x] Cote invalide (0.5) : message lisible, pas de trace — `Cotes saisies inutilisables : Cotes invalides : [0.5, 4.6, 6.5]. Une cote decimale est toujours > 1.0 et ne doit etre ni vide ni manquante.`
- [x] Cote en toutes lettres : pas de trace, l'analyse reste affichee
- [x] Deux fois la meme equipe : message, pas de plantage — `Choisis deux equipes differentes.`

## Onglet Journee

- [x] Le tableau se remplit avec n×(n−1) lignes — `462 lignes pour 22 equipes (n×(n−1) = 462)`
- [x] Clic sur l'en-tete +2.5 : le tableau est trie — `tete : 77.1% -> 25.8%`
- [x] Deuxieme clic : le tri s'inverse — `25.8% / 84.6%`
- [x] Tri par BTTS puis recalcul : le tableau est trie par BTTS decroissant — `de 71.5% a 34.6%`
- [x] Export CSV de la journee : le fichier est ecrit et lisible — `463 lignes, en-tete : dom,ext,1,N,2,+2.5,+3.5,-2.5,BTTS,buts attendus`

## Onglet Forces

- [x] Classement trie par note decroissante — `de 2.24 a 0.35`
- [x] Paris SG est en tete sur F1 — `Paris SG`
- [x] La colonne forme 6 matchs affiche des V, N, D — `Paris SG : VVNVVD`
- [x] MANQUE #2 — les colonnes tirs, tirs cadres et corners sont remplies — `Paris SG : 17.1 tirs, 6.9 cadres, 6.2 corners`
- [x] Cliquer Rafraichir ne casse rien

## Onglet Opportunites

- [x] Le quota s'affiche a cote du bouton — `Quota The Odds API : 10 credit(s) consomme(s) ce mois-ci sur 500 (4 ap`
- [x] Rafraichir les cotes : le tableau se remplit — `35 selections en 1.0 s`
- [x] Le tableau est trie par esperance decroissante — `de +15.9% a -11.7%`
- [x] Au moins une ligne est surlignee en orange (divergence > 15 pts) — `2 ligne(s) en alerte`
- [x]   la premiere alerte — `Angers - Lille — plus_moins_2.5 moins (ecart 16.1%)`
- [x] Les lignes retenues sont surlignees en vert — `4 ligne(s) retenue(s)`
- [x] La couleur d'alerte est bien l'orange annonce — `#ffd8a8`
- [x] Selectionner une ligne : le detail apparait en bas — `Marseille - Strasbourg | 1N2 2 @ 5.00 (LeoVegas (SE))`
- [x] Sur une ligne rejetee, le detail donne le motif de rejet — `statut affiche : esperance +2.9% sous le seui`
- [x] Le bandeau du bas affiche bankroll, exposition et mise maximale — `Bankroll : 1000.00   |   Exposition en cours : 0.00 (0.0%)   |   Mise maximale par pari : 20.00`
- [x] Le bandeau rappelle l'incertitude — `Les probabilites affichees sont des estimations assorties d'une marge `
- [x] Aucun bouton ne permet de masquer le bandeau
- [x] BUG #1 — les lignes retenues sont les memes que celles du CLI — `interface 4 retenues / 35 analysees, CLI 4 / 35`

## Enregistrement d'un pari

- [x] Enregistrer ce pari sur une ligne retenue — `Pari #1 ajoute a ton journal.`
- [x] BLOC 4 — l'interface bascule sur l'onglet Mon journal — `Mon journal`
- [x] Ligne rejetee : une confirmation demande si on veut vraiment enregistrer — `Cette selection ne passe pas les quatre conditions : esperance +2.9% sous le seuil de 4.0%  L'enregi`
- [x]   la confirmation liste les motifs de rejet — `refus pris en compte : 1 pari(s) au journal`

## Onglet Mon journal

- [x] T15 — le pari enregistre apparait immediatement au journal — `1 ligne(s)`
- [x]   avec toutes ses metadonnees — `match=Marseille - Strasbourg | marche=1N2 | selection=2 | cote=5.00 | mise=9.93`
- [x] Capturer les cotes de cloture : la commande aboutit — `Cotes de cloture capturees pour 1 pari(s).`
- [x]   la colonne cote de cloture et la colonne CLV se remplissent — `cote de cloture 4.70, CLV +6.43%`
- [x] Marquer gagne : le gain se met a jour — `statut gagne, gain +39.72`
- [x] Le bilan suit — `=== BILAN DU JOURNAL ===`
- [x] Le bloc CLV affiche la phrase d'interpretation — `--- CLV (l'indicateur qui compte vraiment) ---`
- [x] Courbe de bankroll : le clic aboutit sur un message, jamais sur une trace — `Courbe enregistree :
/home/saif/.claude/jobs/8bc1f004/tmp/foot_edge_home/rapports/bankroll`
- [x] Courbe de bankroll : un PNG est ecrit dans les rapports — `bankroll_journal.png, 34880 octets`
- [x] Export CSV du journal — `2 lignes`
- [x] Bouton de reglage sans selection : message, pas de trace — `Selectionne un pari dans la liste.`

## Onglet Ajustements

- [x] Bankroll a 500 puis Appliquer : le bandeau suit — `Bankroll : 500.00   |   Exposition en cours : 0.00 (0.0%)   |   Mise maximale par pari : 10.00`
- [x] Attaque -15 % sur Paris SG : sa note baisse dans l'onglet Forces — `2.24 -> 1.9`
- [x] Tout reinitialiser : la note revient a sa valeur d'origine — `1.9 -> 2.24`
- [x] Attaque a -150 : message d'erreur, pas de plantage — `Un ajustement doit rester strictement superieur a -100 %.`
- [x] Le clic sur backtest rend la main tout de suite — `107 ms`
- [x] PENDANT le backtest, l'interface reste utilisable — `backtest 30 s, latence max 40 ms, onglets parcourus ['Ajustements', 'Forces', 'Journee']`
- [x] Le rapport de backtest s'affiche — `======================================================================`
- [x] Le rapport survit meme si un graphique echoue — `GRAPHIQUES presents`
- [x] BLOC 4 — les PNG du backtest s'affichent dans l'onglet — `2 image(s) affichee(s)`
- [x] BLOC 4 — l'onglet affiche bien un PNG (Tk 8.6) — `1 image(s), 1 PhotoImage en memoire`

## Bloc 3 — matchs a venir (manque #1)

- [x] Case cochee sans cotes rafraichies : message clair, aucun calcul — `Aucun match a venir connu : utilise d'abord 'Rafraichir les cotes' dans l'onglet Opportuni`
- [x] Seulement les matchs a venir : le tableau ne montre que les vraies affiches — `7 affiches au lieu de 462 confrontations`
- [x]   exemple d'affiche — `Marseille - Strasbourg`
- [x]   aucune equipe ne se rencontre elle-meme

## Bloc 4 — annulation, F5, preferences

- [x] Le bouton Annuler est actif pendant un chargement — `etat ()`
- [x] Annuler rend la main immediatement — `Calcul annule (le telechargement deja lance se termine en fond).`
- [x] Le resultat de la tache annulee est bien jete — `championnat toujours F1`
- [x] Le bouton Annuler est de nouveau desactive
- [x] F5 sur l'onglet Journee relance le calcul — `tache journee relancee`
- [x] F5 sur l'onglet Forces rafraichit sans rien casser
- [x] A la fermeture, championnat et bankroll sont memorises — `{'championnat': 'E0', 'bankroll': 750.0, 'saisons': 4, 'source': 'buts'}`

## Deuxieme lancement

- [x] L'ecran de premier lancement ne reapparait pas — `0 dialogue(s)`
- [x] Le championnat memorise est repris — `E0 — Premier League`
- [x] La bankroll memorisee est reprise — `750.0`

## Fermeture pendant un chargement

- [x] Fermer la fenetre pendant un chargement : le process se termine — `code 0 en 1.2 s`
- [x]   aucune trace au passage — `stderr vide`
- [x]   la fermeture est immediate, elle n'attend pas le telechargement — `1.2 s`
- [x] Aucun processus app.py ne survit (ps aux | grep app.py) — `0 processus`

## Captures d'ecran

- `00_demarrage.png`
- `01_donnees_pendant_chargement.png`
- `02_donnees_charge.png`
- `03_donnees_melange.png`
- `04_un_match.png`
- `05_journee.png`
- `06_forces.png`
- `07_opportunites.png`
- `08_journal.png`
- `09_ajustements.png`
- `10_backtest.png`
- `11_journee_a_venir.png`
- `12_second_lancement.png`
- `apercu_bankroll.png`
