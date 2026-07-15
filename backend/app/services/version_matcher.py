import difflib
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models.sql_models import Node, NodeMapping, DocumentVersion

class VersionMatcher:
    @staticmethod
    def match_version_nodes(
        db: Session,
        new_version_id: str,
        incoming_nodes: List[Dict[str, Any]],
        prev_version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Maps incoming nodes in a new version to existing logical IDs of the previous version.
        
        Args:
            db: SQL database session
            new_version_id: ID of the new DocumentVersion
            incoming_nodes: List of flattened incoming node dicts
            prev_version_id: Optional ID of the previous DocumentVersion
            
        Returns:
            List of node dicts with their logical_id and match_strategy resolved.
        """
        if not prev_version_id:
            # First version of the document - every node gets its own default logical_id
            for node in incoming_nodes:
                node["match_strategy"] = "initial"
            return incoming_nodes

        # Fetch all nodes from the previous version
        prev_nodes = db.query(Node).filter(Node.version_id == prev_version_id).all()
        prev_nodes_by_path = {n.path: n for n in prev_nodes}
        prev_nodes_by_title = {n.heading.lower().strip(): n for n in prev_nodes}

        for node in incoming_nodes:
            new_heading = node["heading"].strip()
            new_heading_lower = new_heading.lower()
            new_path = node["path"]

            # Strategy 1: Exact Path + Title Match
            if new_path in prev_nodes_by_path:
                prev_node = prev_nodes_by_path[new_path]
                if prev_node.heading.strip().lower() == new_heading_lower:
                    node["logical_id"] = prev_node.logical_id
                    node["match_strategy"] = "exact_path_title"
                    continue

            # Strategy 2: Exact Title Match (in case section got renumbered/moved)
            if new_heading_lower in prev_nodes_by_title:
                prev_node = prev_nodes_by_title[new_heading_lower]
                node["logical_id"] = prev_node.logical_id
                node["match_strategy"] = "exact_title"
                continue

            # Strategy 3: Fuzzy Title Match (same path, but title edited slightly)
            matched = False
            if new_path in prev_nodes_by_path:
                prev_node = prev_nodes_by_path[new_path]
                # Compare headings without the numbered prefix if possible, or direct comparison
                sim = difflib.SequenceMatcher(None, prev_node.heading, new_heading).ratio()
                if sim >= 0.85:
                    node["logical_id"] = prev_node.logical_id
                    node["match_strategy"] = "fuzzy_title"
                    matched = True
                    continue

            if not matched:
                # Strategy 4: Sibling Position Fallback Match
                # Check if there is a node with the same path structure
                # if so, and similarity is at least 0.5, we fall back to it
                if new_path in prev_nodes_by_path:
                    prev_node = prev_nodes_by_path[new_path]
                    sim = difflib.SequenceMatcher(None, prev_node.heading, new_heading).ratio()
                    if sim >= 0.5:
                        node["logical_id"] = prev_node.logical_id
                        node["match_strategy"] = "position_fallback"
                        continue

            # If no strategies matched, it's a completely new node
            # Keeps its generated default logical_id
            node["match_strategy"] = "new_node"

        return incoming_nodes
