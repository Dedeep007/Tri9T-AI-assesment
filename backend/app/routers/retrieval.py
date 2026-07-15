from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.database import get_db, get_mongo_db
from app.models.sql_models import Node
from app.models.pydantic_schemas import TestCaseStalenessResponse, GenerationResponse
from app.services.staleness_service import StalenessService

router = APIRouter()

@router.get("/generations/{gen_id}", response_model=Dict[str, Any])
def get_generation_with_staleness(
    gen_id: str, 
    db: Session = Depends(get_db),
    mongo_db = Depends(get_mongo_db)
):
    # 1. Fetch Generation from MongoDB
    gen = mongo_db["generations"].find_one({"id": gen_id})
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
        
    # 2. Evaluate staleness against current document state
    if gen.get("status") == "failed":
        return {
            "generation": GenerationResponse(**gen),
            "staleness_report": []
        }
        
    staleness_report = StalenessService.evaluate_generation_staleness(db, gen)
    
    return {
        "generation": GenerationResponse(**gen),
        "staleness_report": staleness_report
    }

@router.get("/nodes/{node_id}/generations", response_model=List[GenerationResponse])
def get_generations_for_node(
    node_id: str, 
    db: Session = Depends(get_db),
    mongo_db = Depends(get_mongo_db)
):
    # Verify node exists
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
        
    # We query all generations and filter in python (or using MongoDB complex query if we had strict schema)
    all_gens = mongo_db["generations"].find({})
    matching_gens = []
    
    # We match if the logical_id matches this node's logical_id
    target_logical_id = node.logical_id
    
    for gen in all_gens:
        snapshots = gen.get("node_snapshots") or {}
        # Check if this logical ID is in any snapshot
        for snap_node_id, snap in snapshots.items():
            if snap.get("logical_id") == target_logical_id:
                matching_gens.append(gen)
                break
                
    return [GenerationResponse(**gen) for gen in matching_gens]
