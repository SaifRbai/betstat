
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
