import re
import hashlib
from typing import List, Dict, Any, Optional

class HierarchyBuilder:
    @staticmethod
    def parse_elements_to_tree(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parses raw text elements from OCR/PDF into a structured hierarchical tree.
        Creates a list of node dictionaries containing:
          - heading: str
          - path: str (e.g. "1.1", "2.1.1.1")
          - level: int (0 for root, 1 for chapters, 2 for sections, 3 for deep subsections)
          - body_text: str (concatenated paragraph/table/list text)
          - order_index: int (order among siblings)
          - children: list of nodes
        """
        if not elements:
            return []

        # Heading regex patterns
        # Matches: "1. Device Overview" or "1.1 Intended Use" or "2.1.1.1 Battery Life"
        # Group 1: Number path, Group 2: Title text
        heading_re = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")

        # Reconstruct the document title first
        # Typically the first bold elements of size 22 or similar on page 1 before any numbered section
        doc_title_parts = []
        start_idx = 0
        
        while start_idx < len(elements):
            el = elements[start_idx]
            text = el["text"].strip()
            # If we hit the first numbered heading, stop grouping the title
            if heading_re.match(text) and el["is_bold"]:
                break
            # Add to title if it's large font and bold
            if el["is_bold"] and el["font_size"] and el["font_size"] >= 16.0:
                doc_title_parts.append(text)
            start_idx += 1

        doc_title = " ".join(doc_title_parts).strip() if doc_title_parts else "CT-200 User Manual"
        if not doc_title_parts:
            # reset index if we couldn't find a title
            start_idx = 0

        # Root node (level 0)
        root_node = {
            "heading": doc_title,
            "path": "0",
            "level": 0,
            "body_text": "",
            "order_index": 0,
            "children": []
        }

        # Keep track of active parent nodes at each level.
        # level 0 -> root_node
        active_parents = {0: root_node}
        
        current_node = root_node
        sibling_counters = {} # path_parent -> counter for ordering

        for i in range(start_idx, len(elements)):
            el = elements[i]
            text = el["text"].strip()
            
            # Check if this line is a heading
            match = heading_re.match(text)
            is_heading = False
            
            if match and el["is_bold"]:
                is_heading = True
                path_str, title_str = match.groups()
                
                # Determine level based on the number of dots in path
                # "1" -> level 1
                # "1.1" -> level 2
                # "2.1.1.1" -> level 3 (or deeper, we can count parts)
                parts_count = len(path_str.split("."))
                level = parts_count
                
                # Sibling ordering index
                # Parent path will be level - 1 parent's path
                parent_node = None
                for p_level in range(level - 1, -1, -1):
                    if p_level in active_parents:
                        parent_node = active_parents[p_level]
                        break
                        
                if parent_node is None:
                    parent_node = root_node
                    
                parent_path = parent_node["path"]
                sibling_counters[parent_path] = sibling_counters.get(parent_path, 0) + 1
                order_index = sibling_counters[parent_path]
                
                # Create new node
                new_node = {
                    "heading": f"{path_str} {title_str}",
                    "path": path_str,
                    "level": level,
                    "body_text": "",
                    "order_index": order_index,
                    "children": []
                }
                
                # Append to parent's children
                parent_node["children"].append(new_node)
                
                # Update active parents map
                active_parents[level] = new_node
                # Clear deeper levels
                levels_to_clear = [l for l in active_parents.keys() if l > level]
                for l in levels_to_clear:
                    active_parents.pop(l)
                    
                current_node = new_node
                
            else:
                # This is body text. Append to current_node body_text.
                # If we have a table row, keep it formatted.
                # Avoid combining page headers/footers.
                if current_node["body_text"]:
                    current_node["body_text"] += "\n" + text
                else:
                    current_node["body_text"] = text

        return [root_node]

    @staticmethod
    def flatten_tree(root_node: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Flattens the hierarchical tree into a list of flat nodes.
        Computes the content_hash and logical_id for each node.
        """
        flat_nodes = []
        
        def traverse(node: Dict[str, Any], parent_id: Optional[str] = None):
            # Compute SHA-256 content hash of heading + body text
            content_to_hash = f"{node['heading']}\n{node['body_text']}".encode("utf-8")
            content_hash = hashlib.sha256(content_to_hash).hexdigest()
            
            # Construct a stable logical ID based on section hierarchy/title
            # e.g., "1. Device Overview" -> logical_id = sha256("1. Device Overview")[:16]
            logical_src = node["heading"]
            logical_id = hashlib.sha256(logical_src.encode("utf-8")).hexdigest()[:16]
            
            # Temporary dict representation
            node_copy = {
                "heading": node["heading"],
                "path": node["path"],
                "level": node["level"],
                "body_text": node["body_text"],
                "content_hash": content_hash,
                "logical_id": logical_id,
                "order_index": node["order_index"],
                "parent_path": parent_id, # link by path for flattening
                "children_paths": [child["path"] for child in node["children"]]
            }
            flat_nodes.append(node_copy)
            
            for child in node["children"]:
                traverse(child, node["path"])
                
        traverse(root_node)
        return flat_nodes
