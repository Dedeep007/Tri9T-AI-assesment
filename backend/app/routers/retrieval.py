from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.database import get_db
from app.models.sql_models import Generation, Node
from app.models.pydantic_schemas import TestCaseStalenessResponse, GenerationResponse
from app.services.staleness_service import StalenessService

router = APIRouter()

@router.get("/generations/{gen_id}", response_model=Dict[str, Any])
def get_generation_with_staleness(gen_id: str, db: Session = Depends(get_db)):
    # 1. Fetch Generation
    gen = db.query(Generation).filter(Generation.id == gen_id).first()
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
        
    # 2. Evaluate staleness against current document state
    if gen.status == "failed":
        return {
            "generation": GenerationResponse.model_validate(gen),
            "staleness_report": []
        }
        
    staleness_report = StalenessService.evaluate_generation_staleness(db, gen)
    
    return {
        "generation": GenerationResponse.model_validate(gen),
        "staleness_report": staleness_report
    }

@router.get("/nodes/{node_id}/generations", response_model=List[GenerationResponse])
def get_generations_for_node(node_id: str, db: Session = Depends(get_db)):
    # Verify node exists
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
        
    # Find all generations that referenced this node or its logical equivalent in their snapshots
    # We query all generations and filter in python (or JSON filter in Postgres if supported)
    # Python filtering is highly portable and works seamlessly for both SQLite and Postgres dialects
    all_gens = db.query(Generation).all()
    matching_gens = []
    
    # We match if the logical_id matches this node's logical_id
    target_logical_id = node.logical_id
    
    for gen in all_gens:
        snapshots = gen.node_snapshots or {}
        # Check if this logical ID is in any snapshot
        for snap_node_id, snap in snapshots.items():
            if snap.get("logical_id") == target_logical_id:
                matching_gens.append(gen)
                break
                
    return matching_gens
