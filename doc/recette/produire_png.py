"""Produit un PNG hors du process graphique (matplotlib seul, sans Tk)."""
import sys
from pathlib import Path

sys.path.insert(0, "/home/saif/bet216/foot_edge")
import pandas as pd
from modele import backtest as B

paris = pd.DataFrame({"bankroll": [500.0, 540.0, 515.0, 590.0, 602.5]})
print(B.tracer_bankroll(paris, 500.0, Path(sys.argv[1]), "(apercu)"))
