"""Seed a booted Open Wearables instance with one heart-rate sample + API key.

Run from the OW checkout with OW's interpreter:
    cd ow/backend && uv run python <jhe>/tests/contract/seed_ow.py

It imports OW's own models/factories, so it must run inside OW's environment
(not JHE's). It commits directly to OW's DB (the running app reads it over a
separate connection), then prints the user id and API key for the contract test:

    OW_USER_ID=<uuid>
    OW_API_KEY=sk-...

A failure here means OW's internals changed (models/factories), i.e. the harness
needs updating - it is not itself a JHE-contract regression.
"""

from datetime import UTC, datetime

from app.database import SessionLocal
from tests.factories import (
    ApiKeyFactory,
    DataPointSeriesFactory,
    DataSourceFactory,
    DeveloperFactory,
    SeriesTypeDefinitionFactory,
    UserFactory,
)

# Fixed value the contract test asserts against.
HEART_RATE_VALUE = 72.5


def main() -> None:
    session = SessionLocal()
    factories = (
        UserFactory,
        DataSourceFactory,
        DataPointSeriesFactory,
        DeveloperFactory,
        ApiKeyFactory,
        SeriesTypeDefinitionFactory,
    )
    for f in factories:
        f._meta.sqlalchemy_session = session
        f._meta.sqlalchemy_session_persistence = "commit"

    user = UserFactory()
    data_source = DataSourceFactory(user=user)
    DataPointSeriesFactory(
        data_source=data_source,
        value=HEART_RATE_VALUE,
        recorded_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
    )
    api_key = ApiKeyFactory()
    session.commit()

    print(f"OW_USER_ID={user.id}")
    print(f"OW_API_KEY={api_key.id}")


if __name__ == "__main__":
    main()
