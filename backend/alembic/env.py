from alembic import context
from sqlalchemy import create_engine, pool
from pydantic_settings import BaseSettings, SettingsConfigDict

class MigrationSettings(BaseSettings):
    database_url: str = "sqlite:///./dev.db"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

url = MigrationSettings().database_url

def run_migrations_offline():
    raise RuntimeError("The baseline inspects existing tables. Run migrations against a backed-up database online.")

def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, transactional_ddl=True if connection.dialect.name == "postgresql" else None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
