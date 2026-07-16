import difflib
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from app.database import get_db
from app.models.sql_models import Document, DocumentVersion, Node, NodeMapping
from app.models.pydantic_schemas import (
    DocumentResponse, DocumentVersionResponse, NodeResponse, 
    NodeDetailResponse, NodeDiffDetail, DocumentCompareResponse, ValidationResponse
)

router = APIRouter()

@router.get("/documents", response_model=List[DocumentResponse])
def get_documents(db: Session = Depends(get_db)):
    return db.query(Document).order_by(Document.created_at.desc()).all()

@router.get("/documents/{doc_id}/versions", response_model=List[DocumentVersionResponse])
def get_document_versions(doc_id: str, db: Session = Depends(get_db)):
    # Verify document exists
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return db.query(DocumentVersion)\
             .filter(DocumentVersion.document_id == doc_id)\
             .order_by(DocumentVersion.version_number.asc())\
             .all()

@router.get("/documents/{doc_id}/sections", response_model=List[NodeResponse])
def get_sections(
    doc_id: str,
    version: Optional[int] = Query(None, description="Version number. Default is latest."),
    db: Session = Depends(get_db)
):
    # Determine target version
    if version is not None:
        target_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == version)\
            .first()
    else:
        target_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id)\
            .order_by(DocumentVersion.version_number.desc())\
            .first()
            
    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get level 1 nodes (top level headings under root)
    # The root node itself is level 0, we return level 1 sections
    return db.query(Node)\
             .filter(Node.version_id == target_version.id, Node.level == 1)\
             .order_by(Node.order_index.asc())\
             .all()

@router.get("/nodes/{node_id}", response_model=NodeDetailResponse)
def get_node(node_id: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
        
    # Children are resolved by parent_id
    children = db.query(Node).filter(Node.parent_id == node.id).order_index.asc() if hasattr(node, 'children') else []
    # Fetch sorted children directly to ensure ordering
    children = db.query(Node).filter(Node.parent_id == node.id).order_by(Node.order_index.asc()).all()
    
    # Map to detail schema
    node_detail = NodeDetailResponse(
        id=node.id,
        version_id=node.version_id,
        logical_id=node.logical_id,
        heading=node.heading,
        level=node.level,
        body_text=node.body_text,
        content_hash=node.content_hash,
        path=node.path,
        parent_id=node.parent_id,
        order_index=node.order_index,
        children=[NodeResponse.model_validate(c) for c in children]
    )
    return node_detail

@router.get("/documents/{doc_id}/search", response_model=List[NodeResponse])
def search_document(
    doc_id: str,
    q: str,
    version: Optional[int] = Query(None, description="Version number. Default is latest."),
    db: Session = Depends(get_db)
):
    # Determine target version
    if version is not None:
        target_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == version)\
            .first()
    else:
        target_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id)\
            .order_by(DocumentVersion.version_number.desc())\
            .first()
            
    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Search in heading or body_text
    search_query = f"%{q}%"
    return db.query(Node)\
             .filter(
                 Node.version_id == target_version.id,
                 or_(Node.heading.ilike(search_query), Node.body_text.ilike(search_query))
             )\
             .order_by(Node.level.asc(), Node.order_index.asc())\
             .all()

@router.get("/nodes/{node_id}/diff", response_model=NodeDiffDetail)
def diff_node(
    node_id: str,
    v1: Optional[int] = Query(None, description="First version number to compare. Defaults to previous version."),
    v2: Optional[int] = Query(None, description="Second version number to compare. Defaults to latest version."),
    db: Session = Depends(get_db)
):
    # Get current node
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    logical_id = node.logical_id
    version_rec = db.query(DocumentVersion).filter(DocumentVersion.id == node.version_id).first()
    doc_id = version_rec.document_id

    # If v1 and v2 are not specified, compare the node's version against the previous version
    if v1 is None and v2 is None:
        # Determine predecessor version
        pred_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number < version_rec.version_number)\
            .order_by(DocumentVersion.version_number.desc())\
            .first()
            
        if not pred_version:
            # This is the initial version, return unchanged
            return NodeDiffDetail(
                logical_id=logical_id,
                heading=node.heading,
                path=node.path,
                status="unchanged",
                v1_body=None,
                v2_body=node.body_text,
                diff_summary="Initial version. No previous baseline available."
            )
            
        v1_version_id = pred_version.id
        v2_version_id = version_rec.id
    else:
        # Resolve v1 and v2 version numbers to version records
        v1_rec = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == v1).first()
        v2_rec = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == v2).first()
        
        if not v1_rec or not v2_rec:
            raise HTTPException(status_code=404, detail="Specified version numbers not found.")
            
        v1_version_id = v1_rec.id
        v2_version_id = v2_rec.id

    # Retrieve nodes in both versions matching the logical ID
    node_v1 = db.query(Node).filter(Node.version_id == v1_version_id, Node.logical_id == logical_id).first()
    node_v2 = db.query(Node).filter(Node.version_id == v2_version_id, Node.logical_id == logical_id).first()

    if not node_v1 and not node_v2:
        raise HTTPException(status_code=404, detail="Logical node not found in either version.")
    
    if not node_v1:
        return NodeDiffDetail(
            logical_id=logical_id,
            heading=node_v2.heading,
            path=node_v2.path,
            status="added",
            v1_body=None,
            v2_body=node_v2.body_text,
            diff_summary="Node added in the newer version."
        )

    if not node_v2:
        return NodeDiffDetail(
            logical_id=logical_id,
            heading=node_v1.heading,
            path=node_v1.path,
            status="deleted",
            v1_body=node_v1.body_text,
            v2_body=None,
            diff_summary="Node deleted in the newer version."
        )

    # Both versions exist, compare hashes
    if node_v1.content_hash == node_v2.content_hash:
        return NodeDiffDetail(
            logical_id=logical_id,
            heading=node_v2.heading,
            path=node_v2.path,
            status="unchanged",
            v1_body=node_v1.body_text,
            v2_body=node_v2.body_text,
            diff_summary="No changes detected."
        )

    # Modified - generate text diff
    diff = list(difflib.ndiff(node_v1.body_text.splitlines(), node_v2.body_text.splitlines()))
    added = sum(1 for line in diff if line.startswith('+ '))
    removed = sum(1 for line in diff if line.startswith('- '))
    
    diff_summary = f"Modified: added {added} lines, removed {removed} lines."
    
    return NodeDiffDetail(
        logical_id=logical_id,
        heading=node_v2.heading,
        path=node_v2.path,
        status="modified",
        v1_body=node_v1.body_text,
        v2_body=node_v2.body_text,
        diff_summary=diff_summary
    )

@router.get("/documents/{doc_id}/compare", response_model=DocumentCompareResponse)
def compare_document_versions(
    doc_id: str,
    v1: int = Query(..., description="First version number"),
    v2: int = Query(..., description="Second version number"),
    db: Session = Depends(get_db)
):
    v1_rec = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == v1).first()
    v2_rec = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == v2).first()
    
    if not v1_rec or not v2_rec:
        raise HTTPException(status_code=404, detail="One or both versions not found")
        
    v1_nodes = db.query(Node).filter(Node.version_id == v1_rec.id).all()
    v2_nodes = db.query(Node).filter(Node.version_id == v2_rec.id).all()
    
    v1_map = {n.logical_id: n for n in v1_nodes}
    v2_map = {n.logical_id: n for n in v2_nodes}
    
    added = 0
    removed = 0
    modified = 0
    unchanged = 0
    
    for logical_id, node_v2 in v2_map.items():
        if logical_id not in v1_map:
            added += 1
        else:
            node_v1 = v1_map[logical_id]
            if node_v1.content_hash == node_v2.content_hash:
                unchanged += 1
            else:
                modified += 1
                
    for logical_id in v1_map:
        if logical_id not in v2_map:
            removed += 1
            
    return DocumentCompareResponse(
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged
    )

@router.get("/documents/{doc_id}/versions/{version_num}/validate", response_model=ValidationResponse)
def validate_document_version(
    doc_id: str,
    version_num: int,
    db: Session = Depends(get_db)
):
    ver = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == version_num).first()
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")
        
    nodes = db.query(Node).filter(Node.version_id == ver.id).all()
    node_map = {n.id: n for n in nodes}
    
    issues = []
    
    for node in nodes:
        if node.level > 0:
            if not node.parent_id:
                issues.append(f"Node '{node.heading}' (Level {node.level}) has no parent.")
            else:
                parent = node_map.get(node.parent_id)
                if not parent:
                    issues.append(f"Node '{node.heading}' has an invalid parent_id.")
                else:
                    if node.level > parent.level + 1:
                        issues.append(f"Skipped heading level from {parent.level} -> {node.level} on Node '{node.heading}'.")
    
    return ValidationResponse(
        valid=len(issues) == 0,
        issues=issues
    )
