import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.sql_models import Document, DocumentVersion, Node, NodeMapping
from app.models.pydantic_schemas import DocumentResponse, DocumentVersionResponse
from app.services.ocr_pipeline import OCRPipeline
from app.services.hierarchy_builder import HierarchyBuilder
from app.services.version_matcher import VersionMatcher

router = APIRouter()

@router.post("/documents", response_model=DocumentResponse)
def create_document(name: str, db: Session = Depends(get_db)):
    doc = Document(name=name)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc

@router.post("/documents/{doc_id}/ingest", response_model=DocumentVersionResponse)
def ingest_document_version(
    doc_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Verify document exists
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Save uploaded file to a temporary file
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, f"upload_{file.filename}")
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # 1. OCR / Text extraction
        elements = OCRPipeline.extract_pdf_elements(temp_path)
        
        # 2. Hierarchy reconstruction
        tree = HierarchyBuilder.parse_elements_to_tree(elements)
        if not tree:
            raise HTTPException(status_code=422, detail="Unable to extract hierarchy from PDF.")
            
        flat_nodes = HierarchyBuilder.flatten_tree(tree[0])
        
        # Determine version number
        last_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id)\
            .order_by(DocumentVersion.version_number.desc())\
            .first()
            
        version_number = 1 if not last_version else last_version.version_number + 1
        prev_version_id = last_version.id if last_version else None
        
        # 3. Version Matching
        matched_nodes = VersionMatcher.match_version_nodes(db, None, flat_nodes, prev_version_id)
        
        # 4. Insert new version
        new_version = DocumentVersion(
            document_id=doc_id,
            version_number=version_number,
            source_filename=file.filename
        )
        db.add(new_version)
        db.flush()  # Populates new_version.id
        
        # 5. Insert Node objects and resolve relationships
        node_objs = {}
        for nd in matched_nodes:
            node_obj = Node(
                version_id=new_version.id,
                logical_id=nd["logical_id"],
                heading=nd["heading"],
                level=nd["level"],
                body_text=nd["body_text"],
                content_hash=nd["content_hash"],
                path=nd["path"],
                order_index=nd["order_index"]
            )
            node_objs[nd["path"]] = node_obj
            db.add(node_obj)
            
        # Link parents
        for nd in matched_nodes:
            path = nd["path"]
            parent_path = nd["parent_path"]
            if parent_path and parent_path in node_objs:
                node_objs[path].parent = node_objs[parent_path]
                
        db.flush() # Populate IDs of node objects
        
        # 6. Insert NodeMapping entries
        for nd in matched_nodes:
            path = nd["path"]
            node_obj = node_objs[path]
            mapping = NodeMapping(
                logical_id=nd["logical_id"],
                version_id=new_version.id,
                node_id=node_obj.id,
                match_strategy=nd["match_strategy"]
            )
            db.add(mapping)
            
        db.commit()
        db.refresh(new_version)
        return new_version
        
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Parsing error: {e}")
        
    finally:
        # Clean up temporary file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
