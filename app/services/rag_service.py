"""Conversational agent pipeline with conditional catalog retrieval.

Flow:
    START → load_history → classify_intent
                              ├─(shopping)──→ retrieve → rerank → dedupe_categories → generate_reply → END
                              └─(chat)──────────────────────────────────────────────→ generate_reply → END

The classifier separates ordinary conversation, catalog shopping, and image
generation. Pure conversation turns skip retrieval/rerank entirely, so the
assistant can answer general questions without forcing products into every
reply. All paths retain the same session history.
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
HISTORY_WINDOW = 30


# ------------------------------ prompts ------------------------------

INTENT_SYSTEM_PROMPT = """You are a routing classifier for a general AI assistant
that also has interior-design shopping and image-generation tools. Classify the
user's CURRENT turn and extract the relevant structured data.

Set image_generation_intent = true only when the user explicitly wants a new
image created, drawn, rendered, illustrated, or generated. It is false when the
user merely asks to see catalog products, discusses an existing image, or asks a
factual question about images. When true, write a self-contained `image_prompt`
that preserves the user's requested subject, style, mood, composition, and text.

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

Image generation takes precedence over shopping: when image_generation_intent
is true, shopping_intent must be false. When shopping_intent is false, `queries`
should be empty.
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
- After the answer/directives, append exactly one line containing three short,
  useful questions the user could ask next:
  SUGGESTIONS_JSON: ["question 1", "question 2", "question 3"]

USER PROFILE: {design_profile}
{room_context_block}
AVAILABLE PRODUCTS (best pick per category):
{product_brief}
"""

DESIGNER_CHAT_PROMPT = """You are Sumi, Simulafly's capable general AI assistant
with special expertise in interiors and visual design. The user may ask about
any topic. Answer the request directly and accurately while naturally retaining
context from the conversation. Do not force the conversation back to design.

Rules:
- Be warm, clear, and specific. Use Markdown when it improves readability.
- Match the depth of the answer to the request; do not artificially cut off a
  useful explanation.
- Do NOT output PRODUCTS_JSON or PREVIEW_REQUEST directives.
- Do NOT mention specific product names, SKUs, or prices.
- If IMAGE GENERATION REQUEST is present below, briefly confirm what will be
  created. The image tool runs after your reply; never claim it already finished.
- After the answer, append exactly one line containing three short, contextual
  questions the user could ask next. They must be written from the user's point
  of view and must not repeat the current request:
  SUGGESTIONS_JSON: ["question 1", "question 2", "question 3"]

USER PROFILE: {design_profile}
{room_context_block}
{image_request_block}"""


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
    image_generation_intent: bool = Field(
        default=False,
        description="True only when the user explicitly requests creation of a new image",
    )
    image_prompt: str | None = Field(
        default=None,
        description="Self-contained generation prompt when image_generation_intent is true",
    )
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
    image_data_url: str | None

    # produced by nodes
    history: list[Message]
    shopping_intent: bool
    image_generation_intent: bool
    image_generation_prompt: str | None
    intents: list[IntentQuery]
    candidates: list[MerchantProduct]
    products: list[MerchantProduct]
    assistant_text: str
    preview_product_id: uuid.UUID | None      # single-product preview
    preview_product_ids: list[uuid.UUID] | None  # multi-product composite preview
    suggested_questions: list[str]


@dataclass
class RAGResult:
    products: list[MerchantProduct]
    assistant_text: str
    preview_product_id: uuid.UUID | None
    preview_product_ids: list[uuid.UUID] | None
    shopping_intent: bool
    image_generation_prompt: str | None = None
    suggested_questions: list[str] | None = None


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
    """Separate general chat, catalog shopping, and image generation."""
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
        image_generation = bool(parsed.image_generation_intent)
        shopping = bool(parsed.shopping_intent) and not image_generation
        queries = parsed.queries if shopping else []
        image_prompt = parsed.image_prompt if image_generation else None
    except Exception as e:
        log.warning("intent_classification_failed", error=str(e))
        image_generation = _heuristic_image_intent(state["user_message"])
        shopping = (
            _heuristic_shopping_intent(state["user_message"])
            and not image_generation
        )
        queries = []
        image_prompt = state["user_message"] if image_generation else None

    # If the classifier flagged shopping but produced no queries, synthesize one
    # from the user message so retrieval still has something to embed.
    if shopping and not queries:
        queries = [
            IntentQuery(category=None, keywords=state["user_message"][:120], max_price=None)
        ]

    log.info(
        "rag.classify_intent",
        shopping=shopping,
        image_generation=image_generation,
        query_count=len(queries),
        preview=state["user_message"][:60],
    )
    return {
        "shopping_intent": shopping,
        "image_generation_intent": image_generation,
        "image_generation_prompt": image_prompt,
        "intents": queries,
    }


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


def _heuristic_image_intent(user_message: str) -> bool:
    """Conservative fallback used only when structured classification fails."""
    m = user_message.lower()
    creation_words = (
        "generate an image", "generate image", "create an image", "create image",
        "make an image", "make me an image", "draw an image", "draw me",
        "illustrate ", "create a picture", "generate a picture", "render an image",
    )
    return any(trigger in m for trigger in creation_words)


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
    image_request_block = ""
    if state.get("image_generation_intent"):
        image_request_block = (
            "IMAGE GENERATION REQUEST: The image tool will create this after "
            f"your response: {state.get('image_generation_prompt') or state['user_message']}"
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
            image_request_block=image_request_block,
        )

    messages: list = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m.role == "user":
            messages.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            messages.append(AIMessage(content=m.content))
    if state.get("image_data_url"):
        messages.append(
            HumanMessage(
                content=[
                    {"type": "text", "text": state["user_message"]},
                    {
                        "type": "image_url",
                        "image_url": {"url": state["image_data_url"]},
                    },
                ]
            )
        )
    else:
        messages.append(HumanMessage(content=state["user_message"]))

    llm = get_chat_llm(temperature=0.6, max_tokens=700)
    response = await llm.ainvoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    cleaned, preview_single, preview_multi = _strip_directives(raw)
    cleaned, suggestions = _extract_suggestions(cleaned)
    if not suggestions:
        suggestions = _fallback_suggestions(
            state["user_message"],
            shopping=shopping,
            image_generation=bool(state.get("image_generation_intent")),
        )
    return {
        "assistant_text": cleaned,
        "preview_product_id": preview_single,
        "preview_product_ids": preview_multi,
        "suggested_questions": suggestions,
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
_SUGGESTIONS_RE = re.compile(r"SUGGESTIONS_JSON:\s*(\[[^\n]*\])")


def _extract_suggestions(text: str) -> tuple[str, list[str]]:
    """Remove the model-only directive and return up to three safe labels."""
    suggestions: list[str] = []
    match = _SUGGESTIONS_RE.search(text)
    if match:
        try:
            values = json.loads(match.group(1))
            if isinstance(values, list):
                for value in values:
                    label = str(value).strip()
                    if label and label not in suggestions:
                        suggestions.append(label[:120])
                    if len(suggestions) == 3:
                        break
        except (TypeError, json.JSONDecodeError):
            pass
    cleaned = _SUGGESTIONS_RE.sub("", text).strip()
    return cleaned, suggestions


def _fallback_suggestions(
    user_message: str, *, shopping: bool, image_generation: bool
) -> list[str]:
    if image_generation:
        return [
            "Create a different style",
            "Make it more detailed",
            "Generate another variation",
        ]
    if shopping:
        return [
            "Show me more options",
            "Which one fits best?",
            "Can I see it in my room?",
        ]
    return [
        "Tell me more about that",
        "Can you give me an example?",
        "What should I explore next?",
    ]


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
    image_data_url: str | None = None,
) -> RAGResult:
    """Execute one RAG turn through the LangGraph StateGraph."""
    state: RAGState = {
        "db": db,
        "session_id": session_id,
        "user_message": user_message,
        "context_summary": context_summary,
        "design_profile": design_profile or {},
        "image_data_url": image_data_url,
    }
    final: RAGState = await _graph().ainvoke(state)  # type: ignore[assignment]
    return RAGResult(
        products=list(final.get("products") or []),
        assistant_text=final.get("assistant_text", ""),
        preview_product_id=final.get("preview_product_id"),
        preview_product_ids=final.get("preview_product_ids"),
        shopping_intent=bool(final.get("shopping_intent")),
        image_generation_prompt=final.get("image_generation_prompt"),
        suggested_questions=list(final.get("suggested_questions") or []),
    )
