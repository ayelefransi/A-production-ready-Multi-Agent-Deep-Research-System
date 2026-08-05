import pytest
import os

# Ensure mock mode is on for tests
os.environ["MOCK_MODE"] = "true"

@pytest.fixture(autouse=True)
def setup_mock_mode():
    from config.settings import settings
    settings.mock_mode = True
