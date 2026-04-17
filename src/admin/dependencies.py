from src.models import async_session


async def get_db():
    async with async_session() as session:
        yield session
