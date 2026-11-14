from utils.llm_router import get_llm
from utils.mock_llm import MockChatModel
from config.settings import settings

def test_router_returns_mock_in_mock_mode():
    settings.mock_mode = True
    llm = get_llm("planner")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "planner"
