import pytest


@pytest.mark.asyncio
async def test_dedup_first_event_passes(db_session):
    from app.services.dedup import check_and_record
    import uuid

    product_id = uuid.uuid4()
    is_first = await check_and_record(
        db_session, event_type="click", session_id="sess_1", product_id=product_id
    )
    assert is_first is True


@pytest.mark.asyncio
async def test_dedup_second_event_same_hour_blocked(db_session):
    from app.services.dedup import check_and_record
    import uuid

    product_id = uuid.uuid4()
    await check_and_record(
        db_session, event_type="click", session_id="sess_2", product_id=product_id
    )
    is_first = await check_and_record(
        db_session, event_type="click", session_id="sess_2", product_id=product_id
    )
    assert is_first is False


@pytest.mark.asyncio
async def test_dedup_different_session_not_blocked(db_session):
    from app.services.dedup import check_and_record
    import uuid

    product_id = uuid.uuid4()
    await check_and_record(
        db_session, event_type="click", session_id="sess_A", product_id=product_id
    )
    is_first = await check_and_record(
        db_session, event_type="click", session_id="sess_B", product_id=product_id
    )
    assert is_first is True


@pytest.mark.asyncio
async def test_dedup_only_applies_to_designated_event_types(db_session):
    from app.services.dedup import is_dedupable

    assert is_dedupable("click") is True
    assert is_dedupable("ai_image_generation") is True
    assert is_dedupable("impression") is False
    assert is_dedupable("ai_rag_mention") is False
    assert is_dedupable("external_redirect") is False
