import uuid
import datetime
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Text, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base, engine

from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses CHAR(36), storing as stringified hex values.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return value
        else:
            if isinstance(value, uuid.UUID):
                return str(value)
            return value

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                value = uuid.UUID(value)
            return value

# Helper function to get JSON type based on dialect
def get_json_type():
    if engine.url.drivername.startswith("postgresql"):
        return JSONB
    return JSON


class Document(Base):
    __tablename__ = "documents"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")
    selections = relationship("Selection", back_populates="document", cascade="all, delete-orphan")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id = Column(GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    ingested_at = Column(DateTime, default=datetime.datetime.utcnow)
    source_filename = Column(String(255), nullable=False)

    document = relationship("Document", back_populates="versions")
    nodes = relationship("Node", back_populates="version", cascade="all, delete-orphan")
    node_mappings = relationship("NodeMapping", back_populates="version", cascade="all, delete-orphan")
    selection_items = relationship("SelectionItem", back_populates="version", cascade="all, delete-orphan")


class Node(Base):
    __tablename__ = "nodes"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    version_id = Column(GUID(), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    logical_id = Column(String(255), nullable=False)  # Stable cross-version ID
    heading = Column(String(1000), nullable=False)
    level = Column(Integer, nullable=False)  # 0=doc, 1=h1, 2=h2...
    body_text = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)  # SHA-256 hash of content
    path = Column(String(255), nullable=False)  # e.g., "1.2.3"
    parent_id = Column(GUID(), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    order_index = Column(Integer, nullable=False)

    version = relationship("DocumentVersion", back_populates="nodes")
    parent = relationship("Node", remote_side=[id], backref="children")
    mappings = relationship("NodeMapping", back_populates="node", cascade="all, delete-orphan")
    selection_items = relationship("SelectionItem", back_populates="node", cascade="all, delete-orphan")


class NodeMapping(Base):
    __tablename__ = "node_mappings"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    logical_id = Column(String(255), nullable=False)
    version_id = Column(GUID(), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    node_id = Column(GUID(), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    match_strategy = Column(String(50), nullable=False)  # exact_title, fuzzy_title, path_based, etc.

    version = relationship("DocumentVersion", back_populates="node_mappings")
    node = relationship("Node", back_populates="mappings")


class Selection(Base):
    __tablename__ = "selections"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id = Column(GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    document = relationship("Document", back_populates="selections")
    items = relationship("SelectionItem", back_populates="selection", cascade="all, delete-orphan")
    generations = relationship("Generation", back_populates="selection", cascade="all, delete-orphan")


class SelectionItem(Base):
    __tablename__ = "selection_items"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    selection_id = Column(GUID(), ForeignKey("selections.id", ondelete="CASCADE"), nullable=False)
    node_id = Column(GUID(), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    version_id = Column(GUID(), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    content_snapshot = Column(Text, nullable=False)  # frozen body text
    hash_snapshot = Column(String(64), nullable=False)  # frozen content hash

    selection = relationship("Selection", back_populates="items")
    node = relationship("Node", back_populates="selection_items")
    version = relationship("DocumentVersion", back_populates="selection_items")


class Generation(Base):
    __tablename__ = "generations"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    selection_id = Column(GUID(), ForeignKey("selections.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String(50), nullable=False)  # complete, partial, failed
    prompt_used = Column(Text, nullable=True)
    raw_llm_response = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    
    # Store schema-free JSON fields
    test_cases = Column(get_json_type(), nullable=True)
    node_snapshots = Column(get_json_type(), nullable=True)

    selection = relationship("Selection", back_populates="generations")
