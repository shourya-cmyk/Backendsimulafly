import base64
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.models.merchant import Merchant
from app.models.merchant_product import MerchantProduct
from app.models.product import Product
from app.services import rag_service
from app.services.rag_service import RAGResult, _strip_directives


@pytest.fixture(autouse=True)
def _stub_chat_llm(monkeypatch):
    """Provide a deterministic chat LLM for the vision /analyze path.

    Returns an AIMessage object (what AzureChatOpenAI.ainvoke returns) so the
    router's `response.content` access works exactly like in production.
    """

    class _StubChatLLM:
        async def ainvoke(self, messages):
            return AIMessage(
                content="This is a minimalist living room with plenty of natural light. "
                "Are you looking to add seating or some decor?"
            )

    from app.services import llm as llm_module

    monkeypatch.setattr(llm_module, "get_chat_llm", lambda **kw: _StubChatLLM())
    # chat.py imports the symbol directly, so patch it there too.
    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "get_chat_llm", lambda **kw: _StubChatLLM())


@pytest.fixture
def stub_run_rag_turn(monkeypatch, db_session):
    """Stubs the whole LangGraph turn so chat tests focus on router + DB wiring."""

    async def _fake(db, *, session_id, user_message, context_summary, design_profile):
        # echo a product carousel when the user mentions 'sofa', otherwise plain text
        products: list = []
        if "sofa" in user_message.lower():
            from app.models.merchant_product import MerchantProduct
            res = await db.execute(
                __import__("sqlalchemy").select(MerchantProduct).where(MerchantProduct.category == "Sofa").limit(2)
            )
            products = list(res.scalars().all())
        return RAGResult(
            products=products,
            assistant_text=f"Great, here are some options for: {user_message[:40]}",
            preview_product_id=None,
            preview_product_ids=None,
            shopping_intent=bool(products),
        )

    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "run_rag_turn", _fake)
    return _fake


@pytest.mark.asyncio
async def test_chat_analyze_persists_context_and_image(auth_client, db_session):
    r = await auth_client.post("/api/v1/sessions/", json={"title": "Analyze test"})
    sid = r.json()["id"]

    img = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9").decode()
    r = await auth_client.post(
        "/api/v1/chat/analyze",
        json={"session_id": sid, "image_base64": img, "media_type": "image/jpeg"},
    )
    assert r.status_code == 200, r.text
    assert "minimalist" in r.json()["content"]


@pytest.mark.asyncio
async def test_chat_turn_returns_carousel_when_sofa_in_catalog(
    auth_client, db_session, stub_run_rag_turn
):
    merchant = Merchant(
        slug=f"ch-{uuid.uuid4().hex[:6]}",
        legal_name="Chat Test Merchant",
        display_name="CTM",
        referral_code=f"CTM-{uuid.uuid4().hex[:6].upper()}",
    )
    db_session.add(merchant)
    await db_session.commit()
    await db_session.refresh(merchant)

    product = MerchantProduct(
        merchant_id=merchant.id,
        sku="C1",
        title="Mid-Century Sofa",
        category="Sofa",
        in_app_price=14999,
        status="published",
    )
    db_session.add(product)
    await db_session.commit()

    r = await auth_client.post("/api/v1/sessions/", json={"title": "Chat test"})
    sid = r.json()["id"]

    r = await auth_client.post(
        "/api/v1/chat/",
        json={"session_id": sid, "content": "I need a comfy sofa under 15000 rupees"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"].startswith("Great, here are some options")
    assert body["ui_payload"]["type"] == "product_carousel"
    assert any(p["sku"] == "C1" for p in body["ui_payload"]["products"])


@pytest.mark.asyncio
async def test_chat_turn_without_catalog_match_returns_text_only(
    auth_client, db_session, stub_run_rag_turn
):
    r = await auth_client.post("/api/v1/sessions/", json={"title": "Chat text"})
    sid = r.json()["id"]

    r = await auth_client.post(
        "/api/v1/chat/",
        json={"session_id": sid, "content": "What do you think of minimalism?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ui_payload"] is None
    assert "minimalism" in body["content"].lower() or body["content"].startswith("Great")


def test_strip_directives_extracts_preview():
    text = (
        "Here is a sofa.\n"
        "PRODUCTS_JSON: [\"abc\"]\n"
        f"PREVIEW_REQUEST: {{\"product_id\": \"{uuid.uuid4()}\"}}"
    )
    cleaned, preview, preview_multi = _strip_directives(text)
    assert "PRODUCTS_JSON" not in cleaned
    assert "PREVIEW_REQUEST" not in cleaned
    assert preview is not None


def test_graph_builds():
    # Graph compiles without running LLMs — pure structural check.
    graph = rag_service._build_graph()
    assert graph is not None
