"""Configuration commune des tests.

Deux garanties, valables pour TOUS les tests du projet :
  - aucun acces reseau : requests est neutralise par defaut ;
  - aucune ecriture dans le vrai dossier ~/.foot_edge : tout est redirige vers
    un dossier temporaire propre a la session de tests.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Doit etre defini AVANT le premier import de config.
_DOSSIER_TEST = Path(tempfile.mkdtemp(prefix="foot_edge_tests_"))
os.environ["FOOT_EDGE_HOME"] = str(_DOSSIER_TEST)

import pytest  # noqa: E402

import config  # noqa: E402


@pytest.fixture(autouse=True)
def sans_reseau(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Toute tentative d'appel HTTP fait echouer le test avec un message clair."""
    if "reseau" in request.keywords:
        return

    def interdit(*_args: object, **_kwargs: object):
        raise AssertionError(
            "Un test a tente un appel reseau. Les tests doivent tourner avec le wifi coupe : "
            "utilise une fixture locale ou monkeypatch la fonction concernee."
        )

    for module in ("data.resultats", "data.cotes"):
        try:
            cible = __import__(module, fromlist=["requests"])
        except ImportError:
            continue
        monkeypatch.setattr(cible.requests, "get", interdit, raising=False)


@pytest.fixture()
def dossier_donnees() -> Path:
    return Path(os.environ["FOOT_EDGE_HOME"])
