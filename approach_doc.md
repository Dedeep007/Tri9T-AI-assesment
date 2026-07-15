# CardioTrack CT-200 Technical Compliance & QA API — Technical Approach Document

This document details the engineering decisions, data architectures, parsing heuristics, and verification strategies implemented for the Tri9T AI Engineering assignment.

---

## 💾 Unified Storage & Data Model

The assignment expected a combination of SQLite (for relational tree data) and a NoSQL store like MongoDB (for LLM-generated output). We strictly adhered to this architecture.

**Justification for Tech Stack:** 
We used **SQLite (via SQLAlchemy)** to maintain ACID compliance, referential integrity, and cascading deletes for the hierarchical Tree, Versions, and Selections. 
We used **MongoDB** (accessed via `pymongo`) as the NoSQL store for LLM-generated output. This allows schema-free storage of unpredictable LLM outputs (test cases and text snapshots) without polluting the relational schema. During local testing, we fall back to `mongomock` to ensure the E2E flow runs immediately without requiring you to stand up a MongoDB cluster.

### Database Schema (SQLAlchemy Models)

The entity relationships are designed to manage hierarchy and version history robustly:

```
  ┌──────────────┐          ┌────────────────────┐          ┌──────────────┐
  │   Document   │◄─────────┤  DocumentVersion   │◄─────────┤     Node     │
  └──────┬───────┘          └─────────▲──────────┘          └──────┬───────┘
         │                            │                            │
         │                            │ (version pin)              │
         │                            │                            │
  ┌──────▼───────┐          ┌─────────┴──────────┐          ┌──────▼───────┐
  │  Selection   │◄─────────┤   SelectionItem    ├─────────►│ NodeMapping  │
  └──────┬───────┘          └────────────────────┘          └──────────────┘
         │
  ┌──────▼───────┐
  │  Generation  │
  └──────────────┘
```

1. **`documents`**: Parent document metadata (e.g., "CardioTrack CT-200").
2. **`document_versions`**: Records of individual ingestions (v1, v2, etc.).
3. **`nodes`**: Bounded content segments (representing headings and associated body texts) with hierarchical links (`parent_id`) and stable logical IDs (`logical_id`).
4. **`node_mappings`**: Tracks mapping strategies and links nodes in a version to their logical identifiers.
5. **`selections`**: Version-pinned collections named by the user.
6. **`selection_items`**: Junction table pinning selected nodes to their specific versions and freezing content text and hashes (`content_snapshot`, `hash_snapshot`) to prevent future modifications from corrupting historical selections.
7. **`generations`**: LLM test case output records. Generates a Pydantic-validated list of test cases (`test_cases` JSONB column) and maps node snapshots (`node_snapshots` JSONB column) at generation time to enable dynamic staleness calculation at query time.

---

## 🔍 Document Extraction & OCR Pipeline

### PDF Extraction Strategy (`pdfplumber` + OCR fallback)
Rather than a generic layout engine, we tailored a pipeline to leverage PDF text streams natively when possible to capture visual attributes:
1. **Character Stream Grouping**: We retrieve characters from `pdfplumber` and group them into lines by clustering their `top` vertical coordinates.
2. **Font Weight & Size Classification**: For each line, we compute the dominant font size and boldness.
3. **Header/Footer Stripping**: Running headers and footers are identified by scanning for recurring lines at vertical extremes. These lines and pure page number sequences are stripped out.
4. **Scanned PDF Fallback**: If `pdfplumber` extracts fewer than 50 characters from a page, the page is converted to an image and processed via `pytesseract`.

### Structural Inconsistencies Discovered
During inspection of the CT-200 manual, several structural quirks and inconsistencies were discovered that broke naive parsers:
- Duplicate headings (identical text appearing in different sections).
- Inconsistent heading numbering (some sections relied more on numbering than font size).
- Pages where font sizes of paragraphs were identical to headings.
- Tables interrupting normal paragraph flow.
- Wrapped headings extending across multiple lines.

### Initial Implementation Failure
My first implementation classified headings using only font size. This incorrectly treated some bold paragraphs and warning callouts as section headings, heavily fracturing the document tree. I later added numbering and whitespace heuristics, which significantly improved hierarchy reconstruction. The parser was updated to use multiple heading signals instead of relying solely on typography.

### Hierarchical Tree Reconstruction
To reconstruct the parent-child outline hierarchy:
1. Numbered sections are identified using a regex pattern: `^(\d+(?:\.\d+)*)\.?\s+(.+)$`.
2. A line is classified as a section heading **only** if it matches this pattern **and** is marked as bold.
3. We scan the document sequentially, computing the level of each heading based on the number of dot-separators (e.g., `2.1` -> Level 2).
4. An active parent lookup stack is updated sequentially. A new node is parented to the nearest active ancestor of a lower level.
5. All non-heading lines are accumulated into the active section's `body_text` property, preserving line formatting and table rows.

### Content Hash Normalization
Before hashing a node to create its `content_hash`, the text is **normalized by trimming whitespace and collapsing repeated spaces** so insignificant formatting differences do not trigger false positive staleness alerts.

---

## 🔄 Document Versioning & Logical ID Matching

When a document version is re-ingested, we map new nodes to existing ones using a **4-tier matching cascade**:
- **Strategy 1: Exact Path + Title Match**
- **Strategy 2: Exact Title Match** (captures sections renumbered or moved).
- **Strategy 3: Fuzzy Title Match** (captures minor title edits using `difflib.SequenceMatcher` similarity >= 0.85).
- **Strategy 4: Sibling Position Fallback** (same sibling order index under mapped parent).

### Matcher Failure Modes
This approach works well for incremental document revisions but may incorrectly classify sections as new if both the heading and surrounding hierarchy are substantially reorganized. If a section's title is completely rewritten and its path is renumbered simultaneously, it will fail all cascades and be treated as a new node, causing historical selection links to break.

---

## 🤖 LLM Generation & Structured Validation

### Prompt Design & JSON Formatting
The generator constructs a strict JSON formatting prompt specifying field descriptions and uses a JSON response configuration format on the LLM client.

### Defensive Retry & Self-Correction Loop
LLM outputs are inherently variable. To defend against malformed responses:
1. The raw output is cleaned and parsed into JSON.
2. The JSON is validated against a Pydantic schema `GenerationOutputSchema`.
3. If validation fails, we enter a self-correcting retry loop (passing the raw invalid output and exact validation error message back to the LLM).
4. **After retries fail**, the generation is marked as failed (`status="failed"`) and the raw model response is stored for debugging rather than silently generating incomplete or corrupt test cases.

### Duplicate Selection Policy
If the same version-pinned selection already has a successful generation, the existing generation is returned instead of regenerating identical test cases (idempotent caching). A `force=true` query parameter option can override this behavior to trigger a fresh LLM call.

---

## 🔔 Staleness / Impact Detection

We evaluate test case staleness deterministically at retrieval time:
1. We identify the latest version of the parent document.
2. Trace each source node to its current version counterpart via its `logical_id`.
3. Compare the current `content_hash` against the snapshotted hash.

### Staleness Classification
The current implementation treats any content hash change as stale (a binary `Stale / Not Stale` classification). This is intentionally conservative because determining semantic equivalence automatically is unreliable for regulatory documents. A one-word wording change might completely alter a safety parameter, so we flag the change and output a line-by-line diff, empowering the human QA engineer to make the final determination.

---

## 🛡️ Validation Strategy

We care deeply about correctness and debugging methodology. The pipeline was validated using a multi-step engineering approach:

**Parser Validation Pipeline:**
`Manual PDF comparison` ➔ `Unit Tests` ➔ `Tree consistency checks` ➔ `Version matching tests` ➔ `LLM schema validation` ➔ `End-to-end API tests`

### OCR/Parsing Validation
After extraction, I manually compared the reconstructed tree against the original PDF and wrote validation scripts to ensure section counts, heading hierarchy, and parent-child relationships matched the source document exactly. I also created targeted unit tests for structural edge cases discovered during inspection.

### Explicit Unit Tests
The following explicit unit tests were written to target known parser irregularities and matching logic:
1. **Test 1**: Duplicate heading produces distinct node IDs with correct parents.
2. **Test 2**: Skipped heading level maintains correct parent assignment.
3. **Test 3**: Table extraction preserves row grouping without interrupting paragraphs.
4. **Test 4**: Version matcher retains `logical_id` after body text changes.
5. **Test 5**: Content hash changes dynamically on body edits.

---

## 📝 Decision Log

### 1. What's the one part of this system most likely to silently give wrong results without erroring? How would you catch it?
- **The PDF Parser/Hierarchy Builder**. If a layout element or table is parsed incorrectly, it might merge section headings into the body text of a previous section. The database ingestion succeeds without throwing any error, but the section tree hierarchy becomes corrupt, and subsequent test case generations will miss entire requirements.
- **How to catch it**: Implement structure assertion checks (e.g., verifying that the total number of extracted chapters matches expected counts, or running a validator asserting that headings don't exceed a maximum character length, which catches paragraph texts accidentally parsed as headings).

### 2. Where did you choose simplicity over correctness because of time, and what would break first if this went to production as-is?
- **Fuzzy Version Matching**. The current string similarity matching uses Python's standard `difflib.SequenceMatcher`. While it handles minor title changes, it doesn't understand semantic updates (e.g., "Safety Warnings" and "Precautionary Guidelines" are semantically equivalent but would fail the fuzzy threshold match).
- **What would break first**: If a regulatory officer makes major structural changes to the manual (such as moving and renaming multiple sections simultaneously), many sections will be incorrectly treated as new, breaking traceability for previously generated test cases.

### 3. Name one input (to your parser, your versioning matcher, or your LLM call) that you did *not* handle, and what your system does when it sees it?
- **Scanned Multi-Column Layouts / Floating Diagrams**. The OCR parser handles vertical line streams page-by-page but does not calculate multi-column reading patterns (e.g. newspaper-style layouts).
- **System behavior**: If presented with a multi-column PDF layout, the text grouping logic will read horizontally across the columns, producing jumbled paragraphs. The system will ingest and create nodes from this garbled text, calculate hashes, and pass it to the LLM, which will generate low-quality test cases due to the scrambled input.
