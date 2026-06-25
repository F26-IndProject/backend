import os
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import sys

# Для локального запуска API 
DATABASE_URL = os.environ.get("DATABASE_URL")

# Для docker-compose 
# DATABASE_URL = 'postgresql://lisa:pass@lisa_postgres_quick:5432/lisa_dev'

print(f'Database URL: {DATABASE_URL}')

try:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)  # убрал echo=True для чистоты
    with engine.connect() as conn:
        result = conn.execute(text('SELECT version()'))
        print(f'PostgreSQL connected: {result.fetchone()[0]}')
except Exception as e:
    print(f'PostgreSQL failed: {e}')
    print('Fix: Start PostgreSQL container with: docker-compose up -d lisa_postgres_quick')
    print('Or check if PostgreSQL is accessible on localhost:5432')
    sys.exit(1)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
