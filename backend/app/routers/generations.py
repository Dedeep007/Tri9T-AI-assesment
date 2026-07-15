from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid
from datetime import datetime
from app.database import get_db, get_mongo_db
from app.models.sql_models import Selection, SelectionItem, Node
from app.models.pydantic_schemas import GenerationResponse
from app.services.llm_service import LLMService

router = APIRouter()

@router.post("/selections/{sel_id}/generate", response_model=GenerationResponse)
def generate_qa_test_cases(
    sel_id: str,
    force: bool = Query(False, description="Force a new LLM generation instead of returning cached result."),
    db: Session = Depends(get_db),
    mongo_db = Depends(get_mongo_db)
):
    # Verify selection exists in SQLite
    selection = db.query(Selection).filter(Selection.id == sel_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail="Selection not found")
        
    generations_collection = mongo_db["generations"]
        
    # Idempotency check: return existing successful generation from MongoDB if force=False
    if not force:
        existing_gen = generations_collection.find_one(
            {"selection_id": sel_id, "status": "complete"},
            sort=[("created_at", -1)]
        )
        if existing_gen:
            print("Returning cached LLM generation from MongoDB.")
            # Convert _id to string or just pass directly if Pydantic ignores it
            return GenerationResponse(**existing_gen)

    # 1. Reconstruct selected text for LLM prompt
    items = selection.items
    if not items:
        raise HTTPException(status_code=400, detail="Selection has no items/nodes selected.")
        
    reconstructed_texts = []
    node_snapshots = {}
    
    for item in items:
        # Reconstruct the text block
        reconstructed_texts.append(f"Section Heading: {item.node.heading}\nSection Path: {item.node.path}\nContent:\n{item.content_snapshot}\n")
        
        # Save snapshot state at generation time for staleness tracking
        node_snapshots[str(item.node_id)] = {
            "logical_id": item.node.logical_id,
            "heading": item.node.heading,
            "path": item.node.path,
            "hash": item.hash_snapshot,
            "text": item.content_snapshot
        }
        
    reconstructed_text = "\n---\n".join(reconstructed_texts)
    
    # 2. Query LLM to generate test cases
    gen_result = LLMService.generate_test_cases(reconstructed_text)
    
    # 3. Create Generation record in MongoDB
    generation_doc = {
        "id": str(uuid.uuid4()),
        "selection_id": sel_id,
        "created_at": datetime.utcnow().isoformat(),
        "status": gen_result["status"],
        "prompt_used": gen_result["prompt_used"],
        "raw_llm_response": gen_result["raw_llm_response"],
        "error": gen_result["error"],
        "test_cases": gen_result["test_cases"],
        "node_snapshots": node_snapshots
    }
    
    generations_collection.insert_one(generation_doc.copy())
    
    return GenerationResponse(**generation_doc)

@router.get("/selections/{sel_id}/generations", response_model=List[GenerationResponse])
def list_selection_generations(
    sel_id: str, 
    db: Session = Depends(get_db),
    mongo_db = Depends(get_mongo_db)
):
    selection = db.query(Selection).filter(Selection.id == sel_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail="Selection not found")
        
    generations_collection = mongo_db["generations"]
    cursor = generations_collection.find({"selection_id": sel_id}).sort("created_at", -1)
    
    return [GenerationResponse(**doc) for doc in cursor]

