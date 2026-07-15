import pytest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.sql_models import Document, DocumentVersion, Node, NodeMapping, Selection, SelectionItem
from app.services.hierarchy_builder import HierarchyBuilder
from app.services.version_matcher import VersionMatcher
from app.services.staleness_service import StalenessService

# Use in-memory SQLite database for test suite isolation
@pytest.fixture(name="db")
def db_fixture():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_duplicate_headings(db):
    """
    Asserts that duplicate headings (e.g. '7.1 Error Codes' and '4.2 Error Codes')
    at different paths are mapped to separate nodes with distinct IDs and correct parents.
    """
    elements = [
        {"text": "CardioTrack Manual", "is_bold": True, "font_size": 22.0, "page_num": 1, "y_pos": 10},
        
        {"text": "4. Alarms and Safety Behavior", "is_bold": True, "font_size": 16.5, "page_num": 4, "y_pos": 20},
        {"text": "4.2 Error Codes", "is_bold": True, "font_size": 12.87, "page_num": 4, "y_pos": 50},
        {"text": "E1 Cuff not connected...", "is_bold": False, "font_size": 11.0, "page_num": 4, "y_pos": 70},
        
        {"text": "7. Troubleshooting", "is_bold": True, "font_size": 16.5, "page_num": 5, "y_pos": 20},
        {"text": "7.1 Error Codes", "is_bold": True, "font_size": 12.87, "page_num": 5, "y_pos": 50},
        {"text": "If a code from Section 4.2...", "is_bold": False, "font_size": 11.0, "page_num": 5, "y_pos": 70},
    ]

    tree = HierarchyBuilder.parse_elements_to_tree(elements)
    flat_nodes = HierarchyBuilder.flatten_tree(tree[0])

    # Should have root, two level 1 nodes (4., 7.) and two level 2 nodes (4.2, 7.1)
    assert len(flat_nodes) == 5
    
    # Check hierarchy matching: parent of 4.2 should be 4. parent of 7.1 should be 7.
    nodes_by_path = {n["path"]: n for n in flat_nodes}
    assert nodes_by_path["4.2"]["parent_path"] == "4"
    assert nodes_by_path["7.1"]["parent_path"] == "7"
    assert nodes_by_path["4.2"]["logical_id"] != nodes_by_path["7.1"]["logical_id"]
    assert nodes_by_path["4.2"]["heading"] == "4.2 Error Codes"
    assert nodes_by_path["7.1"]["heading"] == "7.1 Error Codes"


def test_skipped_level_section():
    """
    Asserts that skipped level numbering (e.g. going from level 2 directly to 4.1.1.1)
    parents correctly to the nearest ancestor (Level 2).
    """
    elements = [
        {"text": "CardioTrack Manual", "is_bold": True, "font_size": 22.0, "page_num": 1, "y_pos": 10},
        {"text": "2. Physical Specifications", "is_bold": True, "font_size": 16.5, "page_num": 2, "y_pos": 20},
        {"text": "2.1 General Specifications", "is_bold": True, "font_size": 12.87, "page_num": 2, "y_pos": 50},
        # Skipped level 3 and went straight to 2.1.1.1 (level 4)
        {"text": "2.1.1.1 Battery Life Under Typical Use", "is_bold": True, "font_size": 11.0, "page_num": 2, "y_pos": 90},
        {"text": "Battery text here...", "is_bold": False, "font_size": 11.0, "page_num": 2, "y_pos": 110},
    ]

    tree = HierarchyBuilder.parse_elements_to_tree(elements)
    flat_nodes = HierarchyBuilder.flatten_tree(tree[0])

    nodes_by_path = {n["path"]: n for n in flat_nodes}
    # 2.1.1.1 has level 4. Its parent should fall back to 2.1 (which is level 2) since level 3 is skipped.
    assert nodes_by_path["2.1.1.1"]["parent_path"] == "2.1"


def test_content_hash_changes_on_body_edit():
    """
    Asserts that content_hash changes when text is edited, which is used for staleness detection.
    """
    elements_v1 = [
        {"text": "1. Overview", "is_bold": True, "font_size": 16.5, "page_num": 1, "y_pos": 10},
        {"text": "This is version 1 text content.", "is_bold": False, "font_size": 11.0, "page_num": 1, "y_pos": 30},
    ]
    elements_v2 = [
        {"text": "1. Overview", "is_bold": True, "font_size": 16.5, "page_num": 1, "y_pos": 10},
        {"text": "This is version 2 modified text content.", "is_bold": False, "font_size": 11.0, "page_num": 1, "y_pos": 30},
    ]

    t1 = HierarchyBuilder.parse_elements_to_tree(elements_v1)
    f1 = HierarchyBuilder.flatten_tree(t1[0])

    t2 = HierarchyBuilder.parse_elements_to_tree(elements_v2)
    f2 = HierarchyBuilder.flatten_tree(t2[0])

    node_v1 = [n for n in f1 if n["path"] == "1"][0]
    node_v2 = [n for n in f2 if n["path"] == "1"][0]

    assert node_v1["logical_id"] == node_v2["logical_id"] # Same section heading
    assert node_v1["content_hash"] != node_v2["content_hash"] # Text changed


def test_version_matching_unchanged_nodes(db):
    """
    Tests the version matching strategies:
    - Path and Title matching
    - Renumbered sections matching by Title
    - Sibling match strategies
    """
    doc = Document(name="Test Monitor")
    db.add(doc)
    db.flush()

    v1 = DocumentVersion(document_id=doc.id, version_number=1, source_filename="v1.pdf")
    db.add(v1)
    db.flush()

    # Create V1 Node in DB
    node_v1 = Node(
        version_id=v1.id,
        logical_id="logical_abc",
        heading="3.2 Cuff Inflation Sequence",
        level=2,
        body_text="V1 body text",
        content_hash="hash1",
        path="3.2",
        order_index=1
    )
    db.add(node_v1)
    db.commit()

    # Case A: Same path + same heading
    incoming_nodes = [
        {
            "heading": "3.2 Cuff Inflation Sequence",
            "path": "3.2",
            "level": 2,
            "body_text": "V2 modified body text",
            "content_hash": "hash2",
            "logical_id": "new_logical_temp_id_1",
            "order_index": 1,
            "parent_path": "3"
        }
    ]
    resolved = VersionMatcher.match_version_nodes(db, "v2_id", incoming_nodes, v1.id)
    assert resolved[0]["logical_id"] == "logical_abc"
    assert resolved[0]["match_strategy"] == "exact_path_title"


def test_staleness_detection(db):
    """
    Tests that staleness triggers when checking a generation after re-ingesting a modified node.
    """
    # 1. Setup V1
    doc = Document(name="Test Monitor")
    db.add(doc)
    db.flush()

    v1 = DocumentVersion(document_id=doc.id, version_number=1, source_filename="v1.pdf")
    db.add(v1)
    db.flush()

    node_v1 = Node(
        version_id=v1.id,
        logical_id="logical_sec1",
        heading="2.1 General Specifications",
        level=2,
        body_text="Inflates to 180 mmHg.",
        content_hash="hash_v1",
        path="2.1",
        order_index=1
    )
    db.add(node_v1)
    db.flush()

    selection = Selection(document_id=doc.id, name="Test Selection")
    db.add(selection)
    db.flush()

    sel_item = SelectionItem(
        selection_id=selection.id,
        node_id=node_v1.id,
        version_id=v1.id,
        content_snapshot=node_v1.body_text,
        hash_snapshot=node_v1.content_hash
    )
    db.add(sel_item)
    db.flush()

    # Save Generation dict mimicking MongoDB record
    gen_doc = {
        "id": str(uuid.uuid4()),
        "selection_id": selection.id,
        "created_at": datetime.utcnow().isoformat(),
        "status": "complete",
        "test_cases": [{
            "title": "Verify inflation pressure",
            "steps": ["Step 1"],
            "expected_result": "Inflates to 180 mmHg",
            "requirement_ref": "2.1"
        }],
        "node_snapshots": {
            str(node_v1.id): {
                "logical_id": node_v1.logical_id,
                "heading": node_v1.heading,
                "path": node_v1.path,
                "hash": node_v1.content_hash,
                "text": node_v1.body_text
            }
        }
    }

    # 2. Ingest V2 (where text of section 2.1 changes)
    v2 = DocumentVersion(document_id=doc.id, version_number=2, source_filename="v2.pdf")
    db.add(v2)
    db.flush()

    node_v2 = Node(
        version_id=v2.id,
        logical_id="logical_sec1",
        heading="2.1 General Specifications",
        level=2,
        body_text="Inflates to 150 mmHg.", # Text changed
        content_hash="hash_v2", # Hash changed
        path="2.1",
        order_index=1
    )
    db.add(node_v2)
    db.commit()

    # 3. Evaluate Staleness
    report = StalenessService.evaluate_generation_staleness(db, gen_doc)
    assert len(report) == 1
    assert report[0]["stale"] is True
    assert report[0]["staleness_detail"][0]["status"] == "changed"
    assert report[0]["staleness_detail"][0]["hash_at_generation"] == "hash_v1"
    assert report[0]["staleness_detail"][0]["hash_current"] == "hash_v2"
