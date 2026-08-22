# Recette du bloc 1 — comment la rejouer

`RESULTATS.md` est le compte rendu de la derniere execution ; les PNG sont les
captures prises pendant. `journal_execution.txt` est la sortie brute.

## Rejouer

```bash
cd foot_edge

# un dossier de donnees a part : la recette enregistre un pari, elle ne doit
# pas ecrire dans le vrai journal
export FOOT_EDGE_HOME=/tmp/foot_edge_recette
rm -rf "$FOOT_EDGE_HOME" && cp -r ~/.foot_edge "$FOOT_EDGE_HOME"

export SORTIE=doc/recette
xvfb-run -a --server-args="-screen 0 1280x900x24" python doc/recette/recette_bloc1.py
```

Code de sortie 0 si tout passe, 1 sinon, avec la liste des echecs.

Sans `xvfb`, un vrai serveur graphique fait aussi bien : `export DISPLAY=:0`.
La recette ouvre alors ses fenetres a l'ecran.

## Ce qu'elle coute

Deux a quatre credits The Odds API (l'onglet Opportunites, puis la comparaison
avec le CLI, qui repasse par le cache de 15 minutes). Le reste est hors ligne
si les CSV sont deja en cache.

## Un piege a connaitre

Si tu vois `FT_Render_Glyph ... raster overflow` et plus aucun PNG : matplotlib
embarque sa propre FreeType et tkinter charge celle du systeme ; la premiere
chargee gagne. `app.py` importe `matplotlib.ft2font` avant `tkinter` pour cette
raison — ne pas deplacer ces trois lignes.

## Ce qu'elle ne fait pas

Elle intercepte les boites de dialogue : une modale bloque la boucle
d'evenements et donc tout pilotage automatique. Leur texte est verifie mot pour
mot, leur rendu non. Et elle ne juge pas la mise en page — pour ca, il faut
regarder les captures.
