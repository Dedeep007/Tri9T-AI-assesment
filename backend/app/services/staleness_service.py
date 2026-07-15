import difflib
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models.sql_models import Selection, DocumentVersion, Node
from app.models.pydantic_schemas import TestCaseStalenessResponse, NodeStalenessDetail

class StalenessService:
    @staticmethod
    def evaluate_generation_staleness(db: Session, gen: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Evaluates the staleness of all test cases in a generation against the latest version of the document.
        
        Args:
            db: Database session (SQLite)
            gen: The Generation record (from MongoDB)
            
        Returns:
            List of TestCaseStalenessResponse representations.
        """
        # Get selection and document
        selection_id = gen.get("selection_id")
        selection = db.query(Selection).filter(Selection.id == selection_id).first()
        if not selection:
            return []
            
        doc_id = selection.document_id
        
        # Get latest version of the document
        latest_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id)\
            .order_by(DocumentVersion.version_number.desc())\
            .first()
            
        if not latest_version:
            # No version found, default to not-stale
            return []

        # Find latest nodes grouped by logical_id
        latest_nodes = db.query(Node).filter(Node.version_id == latest_version.id).all()
        latest_nodes_by_logical = {n.logical_id: n for n in latest_nodes}

        # Snapshot of nodes at generation time (from the JSON column/Mongo field)
        snapshots = gen.get("node_snapshots", {})

        # Evaluate each test case
        results = []
        test_cases = gen.get("test_cases", [])
        
        for idx, tc in enumerate(test_cases):
            tc_id = f"{gen.get('id')}_tc_{idx}"
            tc_title = tc.get("title", "Unnamed Test Case")
            
            # The test case references source nodes
            # We look at the selection items of this generation
            # Let's map staleness for each node
            staleness_details = []
            is_stale = False
            overall_status = "valid"

            # In the prompt, the LLM associates test cases with headings or paths.
            # To be robust, we look at all nodes that were in the original selection!
            # The generation covers the entire selection.
            for node_id_str, snap in snapshots.items():
                logical_id = snap.get("logical_id")
                hash_at_generation = snap.get("hash")
                heading = snap.get("heading", "Unknown Section")
                path = snap.get("path", "")
                
                if not logical_id:
                    # Fallback to look up the node
                    node_obj = db.query(Node).filter(Node.id == node_id_str).first()
                    if node_obj:
                        logical_id = node_obj.logical_id
                    else:
                        continue
                
                # Check current status in latest version
                if logical_id not in latest_nodes_by_logical:
                    # Node was deleted in latest version!
                    is_stale = True
                    overall_status = "stale"
                    staleness_details.append({
                        "node_id": node_id_str,
                        "heading": heading,
                        "path": path,
                        "status": "deleted",
                        "hash_at_generation": hash_at_generation,
                        "hash_current": None,
                        "diff_summary": "Section was deleted from the document manual."
                    })
                else:
                    curr_node = latest_nodes_by_logical[logical_id]
                    if curr_node.content_hash != hash_at_generation:
                        # Node changed!
                        is_stale = True
                        overall_status = "stale"
                        
                        # Generate diff summary
                        old_text = snap.get("text", "")
                        new_text = curr_node.body_text
                        diff_sum = StalenessService._generate_diff_summary(old_text, new_text)
                        
                        staleness_details.append({
                            "node_id": node_id_str,
                            "heading": curr_node.heading,
                            "path": curr_node.path,
                            "status": "changed",
                            "hash_at_generation": hash_at_generation,
                            "hash_current": curr_node.content_hash,
                            "diff_summary": diff_sum
                        })
                    else:
                        staleness_details.append({
                            "node_id": node_id_str,
                            "heading": curr_node.heading,
                            "path": curr_node.path,
                            "status": "unchanged",
                            "hash_at_generation": hash_at_generation,
                            "hash_current": curr_node.content_hash,
                            "diff_summary": "No changes detected."
                        })
            
            results.append({
                "test_case_id": tc_id,
                "title": tc_title,
                "stale": is_stale,
                "status": overall_status,
                "staleness_detail": staleness_details
            })
            
        return results

    @staticmethod
    def _generate_diff_summary(old_text: str, new_text: str) -> str:
        """
        Creates a brief textual summary of differences between old and new text.
        """
        # Let's count character diffs and perform a line diff
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        
        diff = list(difflib.ndiff(old_lines, new_lines))
        added = sum(1 for line in diff if line.startswith('+ '))
        removed = sum(1 for line in diff if line.startswith('- '))
        
        summary_parts = []
        if added:
            summary_parts.append(f"added {added} line(s)")
        if removed:
            summary_parts.append(f"removed {removed} line(s)")
            
        if not summary_parts:
            # Typo or single character edit
            return f"Minor textual modifications (length: {len(old_text)} -> {len(new_text)} chars)."
            
        return f"Body modified: {', '.join(summary_parts)}."
