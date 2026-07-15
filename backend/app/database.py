import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from pymongo import MongoClient
import mongomock

# Load environment variables
load_dotenv()

# --- SQLITE / POSTGRESQL (Relational) ---
DATABASE_URL = os.getenv("SUPABASE_DB_URL")

# If no SUPABASE_DB_URL is provided, fallback to local sqlite for testing convenience
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./ct200_fallback.db"
    connect_args = {"check_same_thread": False}
else:
    connect_args = {}

# Use pool_pre_ping to check connection health (critical for remote Supabase Postgres)
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- MONGODB (NoSQL) ---
MONGODB_URL = os.getenv("MONGODB_URL")

# Use mongomock if no URL is provided, to ensure demo runs without setup
if not MONGODB_URL:
    mongo_client = mongomock.MongoClient()
else:
    mongo_client = MongoClient(MONGODB_URL)

mongo_db = mongo_client["tri9t_db"]

def get_mongo_db():
    return mongo_db
