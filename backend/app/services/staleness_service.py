import difflib
import re
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from app.models.sql_models import Selection, SelectionItem, DocumentVersion, Node
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
        
        # Get generated version
        selection_item = db.query(SelectionItem).filter(SelectionItem.selection_id == selection_id).first()
        gen_version_number = None
        if selection_item:
            gen_version = db.query(DocumentVersion).filter(DocumentVersion.id == selection_item.version_id).first()
            if gen_version:
                gen_version_number = gen_version.version_number
        
        # Get latest version of the document
        latest_version = db.query(DocumentVersion)\
            .filter(DocumentVersion.document_id == doc_id)\
            .order_by(DocumentVersion.version_number.desc())\
            .first()
            
        current_version_number = latest_version.version_number if latest_version else None
            
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
            tc_severity = "LOW"
            
            def update_severity(current: str, new_val: str) -> str:
                levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
                if levels.get(new_val, 1) > levels.get(current, 1):
                    return new_val
                return current

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
                    tc_severity = update_severity(tc_severity, "MEDIUM")
                    staleness_details.append({
                        "node_id": node_id_str,
                        "heading": heading,
                        "path": path,
                        "status": "deleted",
                        "hash_at_generation": hash_at_generation,
                        "hash_current": None,
                        "diff_summary": "Section was deleted from the document manual.",
                        "changes": None,
                        "generated_from_version": gen_version_number,
                        "current_version": current_version_number,
                        "severity": "MEDIUM"
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
                        diff_sum, changes, node_sev = StalenessService._generate_structured_diff(old_text, new_text)
                        
                        tc_severity = update_severity(tc_severity, node_sev)
                        
                        staleness_details.append({
                            "node_id": node_id_str,
                            "heading": curr_node.heading,
                            "path": curr_node.path,
                            "status": "changed",
                            "hash_at_generation": hash_at_generation,
                            "hash_current": curr_node.content_hash,
                            "diff_summary": diff_sum,
                            "changes": changes,
                            "generated_from_version": gen_version_number,
                            "current_version": current_version_number,
                            "severity": node_sev
                        })
                    else:
                        staleness_details.append({
                            "node_id": node_id_str,
                            "heading": curr_node.heading,
                            "path": curr_node.path,
                            "status": "unchanged",
                            "hash_at_generation": hash_at_generation,
                            "hash_current": curr_node.content_hash,
                            "diff_summary": "No changes detected.",
                            "changes": None,
                            "generated_from_version": gen_version_number,
                            "current_version": current_version_number
                        })
            
            results.append({
                "test_case_id": tc_id,
                "title": tc_title,
                "stale": is_stale,
                "status": overall_status,
                "severity": tc_severity if is_stale else None,
                "staleness_detail": staleness_details
            })
            
        return results

    @staticmethod
    def _generate_structured_diff(old_text: str, new_text: str) -> Tuple[str, List[Dict[str, str]], str]:
        """
        Creates a unified diff summary, a structured list of changes, and calculates severity.
        """
        diff = difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            lineterm=""
        )
        diff_text = "\n".join(diff)
        
        if not diff_text.strip():
            diff_text = "Minor textual modifications detected, but lines remained identical."
            
        ndiff = list(difflib.ndiff(old_text.splitlines(), new_text.splitlines()))
        changes = []
        i = 0
        while i < len(ndiff):
            line = ndiff[i]
            if line.startswith('- '):
                old_line = line[2:]
                
                if i + 1 < len(ndiff) and ndiff[i+1].startswith('? '):
                    i += 1
                    
                if i + 1 < len(ndiff) and ndiff[i+1].startswith('+ '):
                    new_line = ndiff[i+1][2:]
                    i += 1
                    if i + 1 < len(ndiff) and ndiff[i+1].startswith('? '):
                        i += 1
                    changes.append({"type": "modified", "old": old_line, "new": new_line})
                else:
                    changes.append({"type": "deleted", "old": old_line})
                    
            elif line.startswith('+ '):
                changes.append({"type": "added", "new": line[2:]})
            i += 1
            
        node_severity = "LOW"
        for change in changes:
            if change["type"] in ["added", "deleted"]:
                node_severity = "MEDIUM" if node_severity == "LOW" else node_severity
            elif change["type"] == "modified":
                old_nums = re.findall(r'\d+', change.get("old", ""))
                new_nums = re.findall(r'\d+', change.get("new", ""))
                if old_nums != new_nums:
                    node_severity = "HIGH"
                else:
                    node_severity = "MEDIUM" if node_severity == "LOW" else node_severity
                    
        return diff_text, changes, node_severity
