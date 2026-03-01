import json
import logging
import math
import re
import threading
from typing import Iterable

from sqlalchemy import inspect
from sqlalchemy.exc import NoInspectionAvailable, OperationalError

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    np = None

try:
    import faiss
except ImportError:  # pragma: no cover - fallback handled at runtime
    faiss = None

EMBEDDING_DIMENSION = 256
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def _normalize_dense_vector(values):
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return values
    return [value / norm for value in values]


def _dot_product(left, right):
    return sum(a * b for a, b in zip(left, right))


def build_book_search_text(book):
    parts = [
        book.title,
        book.short_description,
        book.author,
        book.genre,
        book.tags,
        book.language,
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def compute_embedding(text, dimension=EMBEDDING_DIMENSION):
    vector = [0.0] * dimension
    for token in _TOKEN_PATTERN.findall((text or "").lower()):
        vector[hash(token) % dimension] += 1.0

    normalized = _normalize_dense_vector(vector)
    if np is not None:
        return np.array(normalized, dtype="float32")
    return normalized


def serialize_embedding(vector):
    if np is not None and hasattr(vector, "tolist"):
        vector = vector.tolist()
    return json.dumps([round(float(value), 8) for value in vector])


def deserialize_embedding(raw_value):
    if not raw_value:
        return None

    try:
        values = json.loads(raw_value)
    except (TypeError, ValueError):
        return None

    if not isinstance(values, list) or len(values) != EMBEDDING_DIMENSION:
        return None

    try:
        normalized = _normalize_dense_vector([float(value) for value in values])
    except (TypeError, ValueError):
        return None

    if np is not None:
        return np.array(normalized, dtype="float32")
    return normalized


def build_and_store_book_embedding(book):
    embedding = compute_embedding(build_book_search_text(book))
    book.embedding_vector = serialize_embedding(embedding)
    return embedding


class SemanticBookIndex:
    def __init__(self):
        self._lock = threading.RLock()
        self._faiss_index = None
        self._vectors_by_book_id = {}
        self._book_ids = []

    def _can_use_faiss(self):
        return faiss is not None and np is not None

    def _rebuild_faiss_index(self):
        self._faiss_index = None
        if not self._book_ids:
            return
        if not self._can_use_faiss():
            if faiss is None:
                logging.warning("FAISS non disponibile: uso fallback in-memory per la ricerca semantica.")
            return

        matrix = np.vstack([self._vectors_by_book_id[book_id] for book_id in self._book_ids]).astype("float32")
        self._faiss_index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)
        self._faiss_index.add(matrix)

    def rebuild(self, books: Iterable):
        vectors_by_book_id = {}
        for book in books:
            vector = deserialize_embedding(book.embedding_vector)
            if vector is None:
                vector = build_and_store_book_embedding(book)
            vectors_by_book_id[book.id] = vector

        with self._lock:
            self._vectors_by_book_id = vectors_by_book_id
            self._book_ids = sorted(vectors_by_book_id.keys())
            self._rebuild_faiss_index()

    def upsert(self, book):
        vector = deserialize_embedding(book.embedding_vector)
        if vector is None:
            vector = build_and_store_book_embedding(book)

        with self._lock:
            self._vectors_by_book_id[book.id] = vector
            self._book_ids = sorted(self._vectors_by_book_id.keys())
            self._rebuild_faiss_index()

    def remove(self, book_id):
        with self._lock:
            if book_id not in self._vectors_by_book_id:
                return
            self._vectors_by_book_id.pop(book_id, None)
            self._book_ids = sorted(self._vectors_by_book_id.keys())
            self._rebuild_faiss_index()

    def search(self, query, top_k=20):
        with self._lock:
            if not self._book_ids:
                return []

            query_vector = compute_embedding(query)
            if np is not None:
                if float(np.linalg.norm(query_vector)) == 0:
                    return []
            elif not any(query_vector):
                return []

            limit = min(top_k, len(self._book_ids))

            if self._faiss_index is not None and np is not None:
                scores, indexes = self._faiss_index.search(query_vector.reshape(1, -1).astype("float32"), limit)
                matches = []
                for idx, score in zip(indexes[0], scores[0]):
                    if idx < 0 or score <= 0:
                        continue
                    matches.append(self._book_ids[idx])
                return matches

            scored = []
            for book_id in self._book_ids:
                score = _dot_product(self._vectors_by_book_id[book_id], query_vector)
                if score > 0:
                    scored.append((book_id, score))
            scored.sort(key=lambda item: item[1], reverse=True)
            return [book_id for book_id, _score in scored[:limit]]


semantic_book_index = SemanticBookIndex()


def initialize_semantic_search_index(db_session):
    from models import Book

    bind = db_session.bind
    if bind is None:
        try:
            bind = db_session.get_bind()
        except Exception:  # pragma: no cover - defensive fallback for lazy/sessionless bootstrap
            bind = None

    if bind is None:
        logging.warning(
            "Indicizzazione semantica saltata: nessuna connessione database disponibile durante bootstrap."
        )
        return

    try:
        inspector = inspect(bind)
    except NoInspectionAvailable:
        logging.warning(
            "Indicizzazione semantica saltata: bind database non ispezionabile durante bootstrap."
        )
        return

    column_names = {column["name"] for column in inspector.get_columns(Book.__tablename__)}
    if "embedding_vector" not in column_names:
        logging.warning(
            "Indicizzazione semantica saltata: colonna '%s.embedding_vector' non ancora presente.",
            Book.__tablename__,
        )
        return

    try:
        books = Book.query.all()
    except OperationalError:
        logging.warning(
            "Indicizzazione semantica saltata: schema database non pronto durante bootstrap.",
            exc_info=True,
        )
        db_session.rollback()
        return

    semantic_book_index.rebuild(books)
    db_session.commit()
