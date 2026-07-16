# CardioTrack CT-200 Medical Device Compliance & QA API

This is a FastAPI backend application designed to support medical device software compliance and Quality Assurance (QA). It parses the CardioTrack CT-200 technical manual PDF, recovers its hierarchical section tree structure, maps content versions logically, generates QA test-case ideas using an LLM, and evaluates requirements traceability staleness when a new document version is uploaded.

The backend uses **SQLite (via SQLAlchemy)** for relational storage of document trees and versions, and **MongoDB** for schema-free NoSQL storage of LLM-generated QA test cases and staleness snapshots.

---

## 🛠️ Technology Stack

- **Core**: FastAPI (Python 3.10+)
- **Relational DB**: SQLAlchemy + SQLite
- **NoSQL DB**: MongoDB (local or Atlas) for LLM generations
- **LLM Integration**: Groq API (`llama-3.3-70b-versatile`) or Gemini API (`gemini-1.5-flash`)
- **Testing**: Pytest

---

## 🚀 Setup & Run Instructions

### 1. Prerequisites
Ensure you have the following installed:
- Python 3.10+
- Tesseract OCR (Optional, only needed if you are testing scanned/image-only PDFs)

### 2. Install Dependencies
Clone the repository and install the required dependencies:
```bash
cd backend
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the `backend/` directory by copying the template:
```bash
cp .env.example .env
```
Open `.env` and fill in your Supabase database and LLM API keys:
```env
# MongoDB Connection String
MONGODB_URL=mongodb+srv://<username>:<password>@cluster0.mongodb.net/?retryWrites=true&w=majority

# API keys for LLM Generation (Groq is preferred; Gemini is also supported)
# If both are left blank, the system runs in an offline mock generator mode for safety
GROQ_API_KEY=your-groq-api-key
GEMINI_API_KEY=your-gemini-api-key
```

### 4. Run the Server
Launch the development server using Uvicorn:
```bash
uvicorn app.main:app --reload --port 8000
```
Once running, you can access:
- **Interactive Swagger Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Alternative Redoc**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## 🧪 Running Automated Tests

Run the test suite covering duplicate heading handling, skipped hierarchy level parsing, content hashing, logical version mapping, and dynamic staleness checks:
```bash
python -m pytest tests/ -v
```

---

## 🎬 Triggering the E2E Flow (Version Ingestion & Staleness Demo)

We have provided a self-contained demonstration script that simulates the entire lifecycle of the application:
1. Creating a document record.
2. Ingesting **Version 1** (`data/ct200_manual.pdf`).
3. Constructing and printing the hierarchical section tree.
4. Selecting safety-critical sections and generating QA test cases.
5. Ingesting **Version 2** (`data/ct200_manual_v2.pdf`) and matching sections to logical IDs.
6. Evaluating staleness against the new version (identifying changed text in inflation cycles and showing a unified text diff).

To run this demo:
```bash
python scripts/demo_flow.py
```
This runs locally in a dedicated sandbox database and outputs the full ingestion and evaluation sequence directly to the console.