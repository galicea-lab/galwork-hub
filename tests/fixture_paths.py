# Katalog modeli testowych (kopie z galwork-models) - testy nie
# zależą od katalogu obiegów huba (`spiffworkflow/`, lokalnie dowiązanie do
# galwork-models, na serwerze wolumen) ani od sąsiednich repozytoriów.
import os

FIXTURE_MODELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'models')


def fixture_model(*parts: str) -> str:
    return os.path.join(FIXTURE_MODELS, *parts)
