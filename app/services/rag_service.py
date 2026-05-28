"""RAG pipeline as a LangGraph StateGraph with conditional routing.

Flow:
    START → load_history → classify_intent
                              ├─(shopping)──→ retrieve → rerank → dedupe_categories → generate_reply → END
                              └─(chat)──────────────────────────────────────────────→ generate_reply → END

The classifier decides per-turn whether the user wants product suggestions. Pure
conversation turns skip retrieval/rerank entirely, so Sumi can have a normal
back-and-forth without forcing products into every reply.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.merchant_product import MerchantProduct
from app.models.message import Message
from app.services.llm import get_chat_llm, get_embeddings

log = get_logger(__name__)

TOP_K = 15
FINAL_K = 5
HISTORY_WINDOW = 10


# ------------------------------ prompts ------------------------------

INTENT_SYSTEM_PROMPT = """You are a routing classifier for an AI interior-design
chat. Decide whether the user's CURRENT turn wants product recommendations, and
if so, extract structured queries describing what they're shopping for.

Set shopping_intent = true when the user:
  - explicitly asks for product suggestions ("show me", "suggest", "find", "recommend")
  - names specific items they want to buy ("I need a sofa under 20k")
  - asks for alternatives to a product ("something smaller", "in blue")
  - asks about prices, availability, or budget-fitting options

Set shopping_intent = false when the user:
  - responds to YOUR (the assistant's) clarifying question with preferences
  - asks design/placement advice without buy intent ("where should the sofa go?")
  - engages in small talk, greetings, or acknowledgements
  - asks questions about what they've been shown so far
  - expresses mood, style preferences, or constraints without a buy request

When shopping_intent = true, populate `queries`:
  - category: short lowercase (e.g. "sofa", "coffee table", "rug"); null if unclear
  - keywords: 3-6 descriptive words grounded in what the user asked for
  - max_price: INR; null if unspecified

When shopping_intent = false, `queries` should be empty.
"""

DESIGNER_SHOPPING_PROMPT = """You are Sumi, a warm and knowledgeable interior designer.
The user is currently shopping. You have a curated list of real products retrieved
from our catalog for this turn — one top pick per furniture category.

Rules:
- Be concise, friendly, and specific. Ask at most one clarifying question.
- Present each product clearly (name, key detail, price if relevant).
- If the user wants to see more options in a particular category, invite them to ask.
- End your reply with a single line:
  PRODUCTS_JSON: [<uuid1>, <uuid2>, ...]
  containing the product UUIDs you recommend in preferred order (may be empty
  if the retrieved catalog genuinely doesn't fit).
- If the user asks to preview a specific product in their room, append:
  PREVIEW_REQUEST: {{"product_id": "<uuid>"}}
- If the user wants to see multiple products in their room at once, append:
  PREVIEW_REQUEST: {{"product_ids": ["<uuid1>", "<uuid2>", ...]}}
- Respect the user's budget and style preferences.
- Never invent products — only recommend from the retrieved list.

USER PROFILE: {design_profile}
{room_context_block}
AVAILABLE PRODUCTS (best pick per category):
{product_brief}
"""

DESIGNER_CHAT_PROMPT = """You are Sumi, a warm and knowledgeable interior designer.
Right now the user is having a conversation — NOT asking you to find products.
Respond naturally: answer their question, give design advice, ask a follow-up,
or acknowledge what they said. Do not recommend specific products in this turn.

Rules:
- Be warm, concise, and specific. At most one clarifying question per turn.
- Do NOT output PRODUCTS_JSON or PREVIEW_REQUEST directives.
- Do NOT mention specific product names, SKUs, or prices.
- If the user's next message implies they want options, invite them to say so
  (e.g. "Want me to pull up some options?") — but don't pre-empt.

USER PROFILE: {design_profile}
{room_context_block}"""


# ------------------------------ structured outputs ------------------------------


class IntentQuery(BaseModel):
    category: str | None = Field(default=None, description="Lowercase furniture category, e.g. 'sofa'")
    keywords: str = Field(description="3-6 descriptive words from the user's request")
    max_price: float | None = Field(default=None, description="Max price in INR; null if unspecified")


class IntentClassification(BaseModel):
    shopping_intent: bool = Field(
        description="True only when the user wants product recommendations this turn"
    )
    queries: list[IntentQuery] = Field(default_factory=list)
    room_style: str | None = None
    budget_total: float | None = None


class RerankResult(BaseModel):
    product_ids: list[str] = Field(description="Ordered list of best-fit product UUIDs, up to 5")


# ------------------------------ state ------------------------------


class RAGState(TypedDict, total=False):
    # inputs
    session_id: uuid.UUID
    user_message: str
    context_summary: str | None
    design_profile: dict[str, Any]
    db: AsyncSession

    # produced by nodes
    history: list[Message]
    shopping_intent: bool
    intents: list[IntentQuery]
    candidates: list[MerchantProduct]
    products: list[MerchantProduct]
    assistant_text: str
    preview_product_id: uuid.UUID | None      # single-product preview
    preview_product_ids: list[uuid.UUID] | None  # multi-product composite preview


@dataclass
class RAGResult:
    products: list[MerchantProduct]
    assistant_text: str
    preview_product_id: uuid.UUID | None
    preview_product_ids: list[uuid.UUID] | None
    shopping_intent: bool


# ------------------------------ nodes ------------------------------


async def _node_load_history(state: RAGState) -> dict:
    db: AsyncSession = state["db"]
    res = await db.execute(
        select(Message)
        .where(Message.session_id == state["session_id"])
        .order_by(Message.created_at.desc())
        .limit(HISTORY_WINDOW)
    )
    history = list(reversed(res.scalars().all()))
    return {"history": history}


async def _node_classify_intent(state: RAGState) -> dict:
    """Decide whether this turn wants products, and extract queries if so."""
    history = state.get("history", [])
    transcript = "\n".join(f"{m.role}: {m.content}" for m in history)
    room_ctx = state.get("context_summary") or ""
    llm = get_chat_llm(temperature=0.0, max_tokens=500).with_structured_output(IntentClassification)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", INTENT_SYSTEM_PROMPT),
            (
                "human",
                "Room context: {room_ctx}\n\nConversation so far:\n{transcript}\n"
                "Current user turn: {user_message}",
            ),
        ]
    )
    chain = prompt | llm
    try:
        parsed: IntentClassification = await chain.ainvoke(
            {
                "room_ctx": room_ctx,
                "transcript": transcript,
                "user_message": state["user_message"],
            }
        )
        shopping = bool(parsed.shopping_intent)
        queries = parsed.queries
    except Exception as e:
        log.warning("intent_classification_failed", error=str(e))
        shopping = _heuristic_shopping_intent(state["user_message"])
        queries = []

    # If the classifier flagged shopping but produced no queries, synthesize one
    # from the user message so retrieval still has something to embed.
    if shopping and not queries:
        queries = [
            IntentQuery(category=None, keywords=state["user_message"][:120], max_price=None)
        ]

    log.info(
        "rag.classify_intent",
        shopping=shopping,
        query_count=len(queries),
        preview=state["user_message"][:60],
    )
    return {"shopping_intent": shopping, "intents": queries}


def _heuristic_shopping_intent(user_message: str) -> bool:
    """Fallback keyword heuristic if the LLM classifier errors out."""
    m = user_message.lower()
    triggers = (
        "show me", "show ", "suggest", "find me", "find ", "recommend",
        "looking for", "need a", "need some", "want a", "want some",
        "buy", "purchase", "options", "pick out", "under ", "budget",
        "cheaper", "alternative", "instead",
    )
    return any(t in m for t in triggers)


async def _node_retrieve(state: RAGState) -> dict:
    db: AsyncSession = state["db"]
    embeddings = get_embeddings()
    seen: dict[uuid.UUID, MerchantProduct] = {}
    intents = state.get("intents", [])
    log.info(
        "rag.retrieve",
        intents=[{"category": q.category, "keywords": q.keywords, "max_price": q.max_price} for q in intents],
    )
    for q in intents:
        vec = await embeddings.aembed_query(q.keywords)
        rows = await _vector_search(db, vec, q.category, q.max_price)
        # If a category filter kills results (LLM named a category not in catalog),
        # retry unfiltered so semantic similarity still finds adjacent products.
        if not rows and q.category:
            log.info("rag.retrieve.fallback_no_category", category=q.category)
            rows = await _vector_search(db, vec, None, q.max_price)
        for p in rows:
            seen.setdefault(p.id, p)
    log.info("rag.retrieve.done", candidates=len(seen))
    return {"candidates": list(seen.values())}


async def _node_rerank(state: RAGState) -> dict:
    candidates = state.get("candidates", []) or []
    if len(candidates) <= FINAL_K:
        return {"products": candidates}
    by_id = {str(p.id): p for p in candidates}
    catalog = "\n".join(
        f"{p.id} | {p.title[:80]} | {p.category} | {p.in_app_price or 0}" for p in candidates
    )
    history_snippet = "\n".join(
        f"{m.role}: {m.content}" for m in state.get("history", [])[-4:]
    )
    llm = get_chat_llm(temperature=0.0, max_tokens=200).with_structured_output(RerankResult)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You rerank furniture products for a user. Pick the best {final_k} "
                "candidates by relevance to the conversation.",
            ),
            (
                "human",
                "Conversation:\n{history}\nuser: {user_message}\n\n"
                "Candidates (id | title | category | price):\n{catalog}",
            ),
        ]
    )
    chain = prompt | llm
    try:
        result: RerankResult = await chain.ainvoke(
            {
                "final_k": FINAL_K,
                "history": history_snippet,
                "user_message": state["user_message"],
                "catalog": catalog,
            }
        )
        ordered = [by_id[pid] for pid in result.product_ids if pid in by_id][:FINAL_K]
        return {"products": ordered or candidates[:FINAL_K]}
    except Exception as e:
        log.warning("rerank_failed", error=str(e))
        return {"products": candidates[:FINAL_K]}


async def _node_dedupe_categories(state: RAGState) -> dict:
    """Keep the top-ranked product per category.

    When the user asks for a single category (e.g. just sofas) we still return
    up to FINAL_K options so they can compare.  When multiple categories are
    present we return the single best match per category so the carousel has
    clean variety and Sumi can offer to show more from any category on request.
    """
    products = state.get("products", []) or []
    if not products:
        return {"products": []}

    # Group by normalised category
    from collections import defaultdict
    by_cat: dict[str, list[MerchantProduct]] = defaultdict(list)
    for p in products:
        cat = (p.category or "uncategorised").lower().strip()
        by_cat[cat].append(p)

    unique_cats = list(by_cat.keys())

    if len(unique_cats) == 1:
        # Single category — keep up to FINAL_K for comparison
        return {"products": by_cat[unique_cats[0]][:FINAL_K]}

    # Multiple categories — one best per category (already in rerank order)
    dedupe = [prods[0] for prods in by_cat.values()]
    log.info(
        "rag.dedupe_categories",
        categories=unique_cats,
        kept=len(dedupe),
    )
    return {"products": dedupe}


async def _node_generate_reply(state: RAGState) -> dict:
    """Compose Sumi's reply using the appropriate persona prompt for the turn."""
    shopping = bool(state.get("shopping_intent"))
    products = state.get("products", []) or []
    room_context_block = (
        f"ROOM CONTEXT: {state.get('context_summary')}\n"
        if state.get("context_summary")
        else ""
    )

    if shopping:
        product_brief = (
            "\n".join(
                f"- {p.id} | {p.title[:80]} | {p.category} | \u20b9{p.in_app_price or 0}"
                for p in products
            )
            or "(none retrieved)"
        )
        system = DESIGNER_SHOPPING_PROMPT.format(
            design_profile=json.dumps(state.get("design_profile") or {}),
            room_context_block=room_context_block,
            product_brief=product_brief,
        )
    else:
        system = DESIGNER_CHAT_PROMPT.format(
            design_profile=json.dumps(state.get("design_profile") or {}),
            room_context_block=room_context_block,
        )

    messages: list = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m.role == "user":
            messages.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            messages.append(AIMessage(content=m.content))
    messages.append(HumanMessage(content=state["user_message"]))

    llm = get_chat_llm(temperature=0.6, max_tokens=700)
    response = await llm.ainvoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    cleaned, preview_single, preview_multi = _strip_directives(raw)
    return {
        "assistant_text": cleaned,
        "preview_product_id": preview_single,
        "preview_product_ids": preview_multi,
    }


# ------------------------------ helpers ------------------------------


async def _vector_search(
    db: AsyncSession,
    embedding: list[float],
    category: str | None,
    max_price: float | None,
) -> list[MerchantProduct]:
    where_clauses = ["embedding IS NOT NULL", "status = 'published'"]
    params: dict[str, Any] = {"q": _vector_literal(embedding), "k": TOP_K}
    if category:
        where_clauses.append("LOWER(category) LIKE LOWER(:cat)")
        params["cat"] = f"%{category}%"
    if max_price:
        where_clauses.append("in_app_price <= :maxp")
        params["maxp"] = max_price
    # halfvec cast matches the HNSW index built in Phase 4 migration so the planner
    # uses it. On plain vector (no halfvec support) Postgres falls back to a seq scan.
    sql = text(
        f"""SELECT id FROM merchant_products WHERE {' AND '.join(where_clauses)}
            ORDER BY embedding::halfvec(3072) <=> CAST(:q AS halfvec(3072)) LIMIT :k"""
    )
    try:
        res = await db.execute(sql, params)
    except Exception as e:
        log.warning("vector_search_unavailable", error=str(e))
        return []
    ids = [row[0] for row in res.fetchall()]
    if not ids:
        return []
    rows = await db.execute(select(MerchantProduct).where(MerchantProduct.id.in_(ids)))
    by_id = {p.id: p for p in rows.scalars().all()}
    return [by_id[i] for i in ids if i in by_id]


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"


_PREVIEW_RE = re.compile(r"PREVIEW_REQUEST:\s*(\{.*?\})", re.DOTALL)


def _strip_directives(
    text: str,
) -> tuple[str, uuid.UUID | None, list[uuid.UUID] | None]:
    """Extract PREVIEW_REQUEST and PRODUCTS_JSON directives from the LLM reply.

    Returns (cleaned_text, single_product_id, multi_product_ids).
    Exactly one of single_product_id / multi_product_ids will be set when a
    PREVIEW_REQUEST is present; both are None otherwise.
    """
    single: uuid.UUID | None = None
    multi: list[uuid.UUID] | None = None
    match = _PREVIEW_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                if data.get("product_ids") and isinstance(data["product_ids"], list):
                    parsed = []
                    for raw_id in data["product_ids"]:
                        try:
                            parsed.append(uuid.UUID(str(raw_id)))
                        except ValueError:
                            pass
                    multi = parsed or None
                elif data.get("product_id"):
                    single = uuid.UUID(str(data["product_id"]))
        except (ValueError, json.JSONDecodeError):
            pass
    cleaned_lines = [
        line
        for line in text.splitlines()
        if not line.strip().startswith(("PRODUCTS_JSON:", "PREVIEW_REQUEST:"))
    ]
    return "\n".join(cleaned_lines).strip(), single, multi


# ------------------------------ graph ------------------------------


def _route_after_classify(state: RAGState) -> str:
    return "retrieve" if state.get("shopping_intent") else "generate_reply"


def _build_graph():
    graph = StateGraph(RAGState)
    graph.add_node("load_history", _node_load_history)
    graph.add_node("classify_intent", _node_classify_intent)
    graph.add_node("retrieve", _node_retrieve)
    graph.add_node("rerank", _node_rerank)
    graph.add_node("dedupe_categories", _node_dedupe_categories)
    graph.add_node("generate_reply", _node_generate_reply)

    graph.add_edge(START, "load_history")
    graph.add_edge("load_history", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        _route_after_classify,
        {"retrieve": "retrieve", "generate_reply": "generate_reply"},
    )
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "dedupe_categories")
    graph.add_edge("dedupe_categories", "generate_reply")
    graph.add_edge("generate_reply", END)
    return graph.compile()


_compiled_graph = None


def _graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()
    return _compiled_graph


# ------------------------------ public entry ------------------------------


async def run_rag_turn(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_message: str,
    context_summary: str | None,
    design_profile: dict[str, Any],
) -> RAGResult:
    """Execute one RAG turn through the LangGraph StateGraph."""
    state: RAGState = {
        "db": db,
        "session_id": session_id,
        "user_message": user_message,
        "context_summary": context_summary,
        "design_profile": design_profile or {},
    }
    final: RAGState = await _graph().ainvoke(state)  # type: ignore[assignment]
    return RAGResult(
        products=list(final.get("products") or []),
        assistant_text=final.get("assistant_text", ""),
        preview_product_id=final.get("preview_product_id"),
        preview_product_ids=final.get("preview_product_ids"),
        shopping_intent=bool(final.get("shopping_intent")),
    )
