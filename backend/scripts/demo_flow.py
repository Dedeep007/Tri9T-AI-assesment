import os
import sys

# Add backend to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from sqlalchemy.orm import Session
from app.database import engine, Base, SessionLocal
from app.models.sql_models import Document, DocumentVersion, Node, Selection, Generation
from app.services.ocr_pipeline import OCRPipeline
from app.services.hierarchy_builder import HierarchyBuilder
from app.services.version_matcher import VersionMatcher
from app.services.llm_service import LLMService
from app.services.staleness_service import StalenessService

def run_demo():
    print("============================================================")
    print("      CT-200 COMPLIANCE & QA API - E2E FLOW DEMONSTRATION    ")
    print("============================================================\n")

    # Force SQLite local file db for the demo so we don't overwrite Supabase data
    print("[1] Initializing SQLite local database for clean demo run...")
    db_file = "demo_runs.db"
    if os.path.exists(db_file):
        os.remove(db_file)
        
    import app.database as db_module
    db_module.DATABASE_URL = f"sqlite:///{db_file}"
    db_module.engine = db_module.create_engine(db_module.DATABASE_URL, connect_args={"check_same_thread": False})
    db_module.SessionLocal = db_module.sessionmaker(autocommit=False, autoflush=False, bind=db_module.engine)
    db_module.Base.metadata.create_all(bind=db_module.engine)
    
    db: Session = db_module.SessionLocal()

    # Resolve paths dynamically relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
    pdf_v1 = os.path.join(workspace_root, "data", "ct200_manual.pdf")
    pdf_v2 = os.path.join(workspace_root, "data", "ct200_manual_v2.pdf")

    # Step 1: Create Document
    print("\n[2] Creating Document record: 'CardioTrack CT-200'...")
    doc = Document(name="CardioTrack CT-200")
    db.add(doc)
    db.commit()
    db.refresh(doc)
    print(f"Document created with ID: {doc.id}")

    # Step 2: Ingest PDF Version 1
    print(f"\n[3] Ingesting PDF Version 1 from: {pdf_v1}...")
    elements_v1 = OCRPipeline.extract_pdf_elements(pdf_v1)
    tree_v1 = HierarchyBuilder.parse_elements_to_tree(elements_v1)
    flat_nodes_v1 = HierarchyBuilder.flatten_tree(tree_v1[0])

    print(f"Parsed {len(flat_nodes_v1)} nodes from Version 1.")

    # Insert Version 1 record
    v1 = DocumentVersion(
        document_id=doc.id,
        version_number=1,
        source_filename=os.path.basename(pdf_v1)
    )
    db.add(v1)
    db.flush()

    node_objs_v1 = {}
    for nd in flat_nodes_v1:
        node_obj = Node(
            version_id=v1.id,
            logical_id=nd["logical_id"],
            heading=nd["heading"],
            level=nd["level"],
            body_text=nd["body_text"],
            content_hash=nd["content_hash"],
            path=nd["path"],
            order_index=nd["order_index"]
        )
        node_objs_v1[nd["path"]] = node_obj
        db.add(node_obj)
        
    for nd in flat_nodes_v1:
        path = nd["path"]
        parent_path = nd["parent_path"]
        if parent_path and parent_path in node_objs_v1:
            node_objs_v1[path].parent = node_objs_v1[parent_path]
            
    db.commit()
    print("Version 1 successfully committed to database.")

    # Print reconstructed tree hierarchy
    print("\nReconstructed Version 1 Tree Hierarchy:")
    # We sort by path segments
    sorted_v1_nodes = sorted(flat_nodes_v1, key=lambda x: [int(s) if s.isdigit() else s for s in x["path"].split(".")])
    for n in sorted_v1_nodes:
        indent = "  " * n["level"]
        body_prev = (n["body_text"][:60] + "...") if len(n["body_text"]) > 60 else n["body_text"]
        body_prev = body_prev.replace("\n", " ")
        print(f"{indent}- {n['heading']} (Level {n['level']}, Path: {n['path']}) -> {body_prev}")

    # Step 3: Create a Selection
    print("\n[4] Creating selection of 3 key safety and inflation sections...")
    # Let's select:
    # 3.2 Cuff Inflation Sequence
    # 4.1 Overpressure Protection
    # 4.2 Error Codes
    selected_paths = ["3.2", "4.1", "4.2"]
    selected_node_ids = []
    
    # Retrieve DB node IDs for these paths in V1
    v1_db_nodes = db.query(Node).filter(Node.version_id == v1.id, Node.path.in_(selected_paths)).all()
    selected_node_ids = [n.id for n in v1_db_nodes]

    # Create Selection record in DB with snapshots
    selection = Selection(document_id=doc.id, name="Safety & Inflation Suite")
    db.add(selection)
    db.flush()

    from app.models.sql_models import SelectionItem
    for node in v1_db_nodes:
        item = SelectionItem(
            selection_id=selection.id,
            node_id=node.id,
            version_id=v1.id,
            content_snapshot=node.body_text,
            hash_snapshot=node.content_hash
        )
        db.add(item)
    db.commit()
    print(f"Selection '{selection.name}' created with ID: {selection.id}")
    print(f"Pinned text snapshot count: {len(v1_db_nodes)}")

    # Step 4: Generate QA Test Cases
    print("\n[5] Triggering LLM Generation for this selection...")
    reconstructed_texts = []
    node_snapshots = {}
    for item in selection.items:
        reconstructed_texts.append(f"Section: {item.node.heading}\n{item.content_snapshot}\n")
        node_snapshots[str(item.node_id)] = {
            "logical_id": item.node.logical_id,
            "heading": item.node.heading,
            "path": item.node.path,
            "hash": item.hash_snapshot,
            "text": item.content_snapshot
        }
    reconstructed_text = "\n---\n".join(reconstructed_texts)

    # Call LLM Service (mocked if no keys exist)
    gen_result = LLMService.generate_test_cases(reconstructed_text)
    
    generation = Generation(
        selection_id=selection.id,
        status=gen_result["status"],
        prompt_used=gen_result["prompt_used"],
        raw_llm_response=gen_result["raw_llm_response"],
        error=gen_result["error"],
        test_cases=gen_result["test_cases"],
        node_snapshots=node_snapshots
    )
    db.add(generation)
    db.commit()
    db.refresh(generation)

    print(f"Generation complete. Status: {generation.status}")
    print("Generated QA Test Cases:")
    for idx, tc in enumerate(generation.test_cases):
        print(f"  Test Case {idx+1}: {tc['title']}")
        print(f"    Preconditions: {tc['preconditions']}")
        print(f"    Expected: {tc['expected_result']}")
        print(f"    Ref: {tc['requirement_ref']}")

    # Step 5: Ingest PDF Version 2
    print(f"\n[6] Ingesting PDF Version 2 from: {pdf_v2}...")
    elements_v2 = OCRPipeline.extract_pdf_elements(pdf_v2)
    tree_v2 = HierarchyBuilder.parse_elements_to_tree(elements_v2)
    flat_nodes_v2 = HierarchyBuilder.flatten_tree(tree_v2[0])

    print(f"Parsed {len(flat_nodes_v2)} nodes from Version 2.")

    # Match logical IDs against version 1
    matched_nodes_v2 = VersionMatcher.match_version_nodes(db, None, flat_nodes_v2, v1.id)

    # Insert Version 2 record
    v2 = DocumentVersion(
        document_id=doc.id,
        version_number=2,
        source_filename=os.path.basename(pdf_v2)
    )
    db.add(v2)
    db.flush()

    node_objs_v2 = {}
    for nd in matched_nodes_v2:
        node_obj = Node(
            version_id=v2.id,
            logical_id=nd["logical_id"],
            heading=nd["heading"],
            level=nd["level"],
            body_text=nd["body_text"],
            content_hash=nd["content_hash"],
            path=nd["path"],
            order_index=nd["order_index"]
        )
        node_objs_v2[nd["path"]] = node_obj
        db.add(node_obj)
        
    for nd in matched_nodes_v2:
        path = nd["path"]
        parent_path = nd["parent_path"]
        if parent_path and parent_path in node_objs_v2:
            node_objs_v2[path].parent = node_objs_v2[parent_path]
            
    db.commit()
    print("Version 2 successfully committed and mapped against Version 1 logical IDs.")

    # Show version matching results
    print("\nLogical Mapping Strategy Results for Version 2:")
    for nd in matched_nodes_v2:
        print(f"  Path: {nd['path'].ljust(8)} heading: {nd['heading'].ljust(35)} Strategy: {nd['match_strategy']}")

    # Step 6: Evaluate Staleness
    print("\n[7] Querying generated test cases and executing Staleness / Impact Detection against Version 2...")
    staleness_report = StalenessService.evaluate_generation_staleness(db, generation)
    
    print("\nStaleness Report:")
    for tc_report in staleness_report:
        print(f"  Test Case: {tc_report['title']}")
        print(f"    Stale: {tc_report['stale']}")
        print(f"    Status: {tc_report['status']}")
        for detail in tc_report["staleness_detail"]:
            print(f"      Node Section: {detail['heading']} (Path: {detail['path']})")
            print(f"        Status in latest doc: {detail['status']}")
            if detail['diff_summary']:
                print(f"        Diff Summary: {detail['diff_summary']}")

    # Step 7: Show detailed diff for the changed section (3.2 Cuff Inflation Sequence)
    print("\n[8] Showing Detailed Text Diff for Section 3.2 (Cuff Inflation Sequence):")
    node_v1_3_2 = db.query(Node).filter(Node.version_id == v1.id, Node.path == "3.2").first()
    node_v2_3_2 = db.query(Node).filter(Node.version_id == v2.id, Node.path == "3.2").first()
    
    if node_v1_3_2 and node_v2_3_2:
        import difflib
        diff = difflib.unified_diff(
            node_v1_3_2.body_text.splitlines(),
            node_v2_3_2.body_text.splitlines(),
            fromfile="Version 1",
            tofile="Version 2",
            lineterm=""
        )
        print("\n".join(diff))

    print("\n============================================================")
    print("                    DEMO COMPLETED SUCCESSFULLY             ")
    print("============================================================")
    
    db.close()
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except Exception:
            pass

if __name__ == "__main__":
    run_demo()
