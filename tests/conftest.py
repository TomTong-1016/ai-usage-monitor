import json
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def claude_data():
    return json.loads((FIXTURES / "claude.json").read_text())


@pytest.fixture
def trae_data():
    return json.loads((FIXTURES / "trae.json").read_text())


@pytest.fixture
def minimax_data():
    return json.loads((FIXTURES / "minimax.json").read_text())


@pytest.fixture
def kimi_data():
    return json.loads((FIXTURES / "kimi.json").read_text())
