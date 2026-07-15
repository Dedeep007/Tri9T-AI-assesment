from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

# Document Schemas
class DocumentBase(BaseModel):
    name: str

class DocumentCreate(DocumentBase):
    pass

class DocumentResponse(DocumentBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True

# Document Version Schemas
class DocumentVersionResponse(BaseModel):
    id: UUID
    document_id: UUID
    version_number: int
    ingested_at: datetime
    source_filename: str

    class Config:
        from_attributes = True

# Node Schemas
class NodeBase(BaseModel):
    logical_id: str
    heading: str
    level: int
    body_text: str
    content_hash: str
    path: str
    parent_id: Optional[UUID] = None
    order_index: int

class NodeResponse(NodeBase):
    id: UUID
    version_id: UUID

    class Config:
        from_attributes = True

class NodeDetailResponse(NodeResponse):
    children: List[NodeResponse] = []

    class Config:
        from_attributes = True

# Selection Schemas
class SelectionCreate(BaseModel):
    document_id: UUID
    name: str
    node_ids: List[UUID]

class SelectionItemResponse(BaseModel):
    id: UUID
    selection_id: UUID
    node_id: UUID
    version_id: UUID
    content_snapshot: str
    hash_snapshot: str

    class Config:
        from_attributes = True

class SelectionResponse(BaseModel):
    id: UUID
    document_id: UUID
    name: str
    created_at: datetime
    items: List[SelectionItemResponse]

    class Config:
        from_attributes = True

# Test Case Schemas (for LLM generation and validation)
class TestCaseSchema(BaseModel):
    title: str = Field(description="Short, descriptive title of the QA test case.")
    preconditions: str = Field(description="Preconditions required before starting the test.")
    steps: List[str] = Field(description="Sequential, numbered steps to perform the test.")
    expected_result: str = Field(description="The specific expected output or behavior of the device.")
    requirement_ref: str = Field(description="The reference section heading or path from which this was derived.")

class GenerationOutputSchema(BaseModel):
    test_cases: List[TestCaseSchema] = Field(description="List of 3 to 5 generated QA test cases.")

# Generation Database Response
class GenerationResponse(BaseModel):
    id: UUID
    selection_id: UUID
    created_at: datetime
    status: str
    prompt_used: Optional[str] = None
    raw_llm_response: Optional[str] = None
    error: Optional[str] = None
    test_cases: Optional[List[Dict[str, Any]]] = None
    node_snapshots: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True

# Staleness / Impact Schemas
class NodeStalenessDetail(BaseModel):
    node_id: UUID
    heading: str
    path: str
    status: str  # "changed", "deleted", "unchanged"
    hash_at_generation: str
    hash_current: Optional[str] = None
    diff_summary: Optional[str] = None

class TestCaseStalenessResponse(BaseModel):
    test_case_id: str
    title: str
    stale: bool
    status: str # "valid", "stale", "unknown"
    staleness_detail: List[NodeStalenessDetail]

# Diff Schemas
class NodeDiffDetail(BaseModel):
    logical_id: str
    heading: str
    path: str
    status: str # "added", "deleted", "modified", "unchanged"
    v1_body: Optional[str] = None
    v2_body: Optional[str] = None
    diff_summary: Optional[str] = None
