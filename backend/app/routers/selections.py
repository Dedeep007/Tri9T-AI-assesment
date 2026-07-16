import hashlib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.sql_models import Selection, SelectionItem, Node
from app.models.pydantic_schemas import SelectionCreate, SelectionResponse

router = APIRouter()

@router.post("/selections", response_model=SelectionResponse)
def create_selection(payload: SelectionCreate, db: Session = Depends(get_db)):
    # Generate selection hash based on sorted node ids
    sorted_node_ids = sorted([str(nid) for nid in payload.node_ids])
    hash_input = "".join(sorted_node_ids).encode('utf-8')
    selection_hash = hashlib.sha256(hash_input).hexdigest()
    
    # 1. Create Selection record
    selection = Selection(
        document_id=payload.document_id,
        name=payload.name,
        selection_hash=selection_hash
    )
    db.add(selection)
    db.flush() # Populate selection.id

    # 2. Add version-pinned SelectionItems
    for node_id in payload.node_ids:
        node = db.query(Node).filter(Node.id == node_id).first()
        if not node:
            # Clean up and raise error
            db.rollback()
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
            
        selection_item = SelectionItem(
            selection_id=selection.id,
            node_id=node.id,
            version_id=node.version_id,
            content_snapshot=node.body_text,
            hash_snapshot=node.content_hash
        )
        db.add(selection_item)
        
    db.commit()
    db.refresh(selection)
    return selection

@router.get("/selections/{sel_id}", response_model=SelectionResponse)
def get_selection(sel_id: str, db: Session = Depends(get_db)):
    selection = db.query(Selection).filter(Selection.id == sel_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail="Selection not found")
    return selection
