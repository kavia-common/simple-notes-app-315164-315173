from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List, Optional

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Health and diagnostics endpoints.",
    },
    {
        "name": "Notes",
        "description": "CRUD operations for notes.",
    },
]


def _utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO8601 string with timezone."""
    return datetime.now(timezone.utc).isoformat()


# SQLite DB location:
# We use the shared database container path described in database/db_connection.txt.
# If you need to change it, set SQLITE_DB env var to an absolute path.
DEFAULT_DB_PATH = "/home/kavia/workspace/code-generation/simple-notes-app-315164-315175/database/myapp.db"
DB_PATH = os.getenv("SQLITE_DB", DEFAULT_DB_PATH)


def _init_db() -> None:
    """Initialize the SQLite schema if it doesn't already exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """.strip()
        )
        conn.commit()


@contextmanager
def _get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()


class NoteBase(BaseModel):
    """Shared fields for note creation and updates."""

    title: str = Field(..., min_length=1, max_length=200, description="Short note title.")
    content: str = Field(..., description="Note body content.")


class NoteCreate(NoteBase):
    """Request body for creating a note."""


class NoteUpdate(BaseModel):
    """Request body for updating a note (partial update supported)."""

    title: Optional[str] = Field(None, min_length=1, max_length=200, description="Updated title.")
    content: Optional[str] = Field(None, description="Updated content.")


class Note(NoteBase):
    """Note resource returned by the API."""

    id: int = Field(..., description="Unique note identifier.")
    created_at: str = Field(..., description="ISO8601 UTC creation timestamp.")
    updated_at: str = Field(..., description="ISO8601 UTC last update timestamp.")


app = FastAPI(
    title="Simple Notes API",
    description="Backend API for a simple notes app (SQLite + FastAPI).",
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
)

# CORS: allow React dev server on port 3000 (plus same-host preview URL patterns via allow_origins wildcard is avoided).
# If your preview domain differs, consider setting an explicit origin list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://localhost:3000",
        "https://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup() -> None:
    """Initialize database schema on service startup."""
    _init_db()


# PUBLIC_INTERFACE
@app.get("/", tags=["Health"], summary="Health check", description="Returns a simple health response.")
def health_check():
    """Health check endpoint.

    Returns:
        JSON object with a 'message' field.
    """
    return {"message": "Healthy"}


def _row_to_note(row: sqlite3.Row) -> Note:
    """Convert a sqlite Row into a Note model."""
    return Note(
        id=int(row["id"]),
        title=str(row["title"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


# PUBLIC_INTERFACE
@app.post(
    "/notes",
    response_model=Note,
    status_code=status.HTTP_201_CREATED,
    tags=["Notes"],
    summary="Create a note",
    description="Create a new note with title and content.",
)
def create_note(payload: NoteCreate) -> Note:
    """Create a new note.

    Args:
        payload: NoteCreate containing title and content.

    Returns:
        The created Note.
    """
    now = _utc_now_iso()
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (payload.title, payload.content, now, now),
        )
        note_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert row is not None
        return _row_to_note(row)


# PUBLIC_INTERFACE
@app.get(
    "/notes",
    response_model=List[Note],
    tags=["Notes"],
    summary="List notes",
    description="Return all notes ordered by updated time (descending).",
)
def list_notes() -> List[Note]:
    """List all notes.

    Returns:
        List of notes.
    """
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC, id DESC").fetchall()
        return [_row_to_note(r) for r in rows]


# PUBLIC_INTERFACE
@app.get(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Get a note by id",
    description="Fetch a single note by its id.",
)
def get_note(note_id: int) -> Note:
    """Get a single note by ID.

    Args:
        note_id: The note identifier.

    Returns:
        The Note.

    Raises:
        HTTPException: 404 if not found.
    """
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
        return _row_to_note(row)


# PUBLIC_INTERFACE
@app.put(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Update a note",
    description="Update a note's title and/or content. Fields not provided remain unchanged.",
)
def update_note(note_id: int, payload: NoteUpdate) -> Note:
    """Update a note by ID.

    Args:
        note_id: The note identifier.
        payload: NoteUpdate with optional title/content.

    Returns:
        The updated Note.

    Raises:
        HTTPException: 404 if not found, 400 if no fields provided.
    """
    if payload.title is None and payload.content is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'title' or 'content' must be provided",
        )

    with _get_conn() as conn:
        existing = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

        new_title = payload.title if payload.title is not None else str(existing["title"])
        new_content = payload.content if payload.content is not None else str(existing["content"])
        now = _utc_now_iso()

        conn.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
            (new_title, new_content, now, note_id),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert row is not None
        return _row_to_note(row)


# PUBLIC_INTERFACE
@app.delete(
    "/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Notes"],
    summary="Delete a note",
    description="Delete a note by id.",
)
def delete_note(note_id: int) -> Response:
    """Delete a note by ID.

    Args:
        note_id: The note identifier.

    Returns:
        An empty 204 response.

    Raises:
        HTTPException: 404 if not found.
    """
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
