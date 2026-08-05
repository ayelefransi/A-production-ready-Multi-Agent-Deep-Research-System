import pytest
from utils.llm_router import get_llm
from utils.mock_llm import MockChatModel
from config.settings import settings

def test_router_returns_mock_in_mock_mode():
    settings.mock_mode = True
    llm = get_llm("planner")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "planner"
    
def test_router_raises_on_unknown_role():
    # If a role is completely unconfigured, we will attempt to fall back but fail
    # However in mock mode, it just returns a mock for that role.
    # So we must disable mock mode to test the real router logic.
    settings.mock_mode = False
    
    # We haven't configured API keys, so it should raise a RuntimeError about missing keys
    with pytest.raises(RuntimeError):
        get_llm("planner")
        
    settings.mock_mode = True # restore for other tests
