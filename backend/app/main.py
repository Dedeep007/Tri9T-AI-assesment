from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.routers import ingest, browse, selections, generations, retrieval

# Create DB Tables on startup (if they don't exist)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="CT-200 Medical Device Compliance & QA API",
    description="API for parsing technical manuals, version-managing requirements, generating QA test cases, and tracking staleness.",
    version="1.0.0"
)

# Enable CORS for convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(ingest.router, prefix="/v1", tags=["Ingestion"])
app.include_router(browse.router, prefix="/v1", tags=["Browse"])
app.include_router(selections.router, prefix="/v1", tags=["Selections"])
app.include_router(generations.router, prefix="/v1", tags=["Generations"])
app.include_router(retrieval.router, prefix="/v1", tags=["Retrieval"])

@app.get("/", tags=["General"])
def read_root():
    return {
        "message": "CT-200 Compliance & QA API is active.",
        "docs_url": "/docs",
        "supported_database": engine.url.drivername
    }
