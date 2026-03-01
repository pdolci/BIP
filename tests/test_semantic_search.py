from semantic_search import (
    EMBEDDING_DIMENSION,
    SemanticBookIndex,
    build_and_store_book_embedding,
    compute_embedding,
    deserialize_embedding,
)


class DummyBook:
    def __init__(self, book_id, title, short_description=None, author=None, genre=None, tags=None, language=None):
        self.id = book_id
        self.title = title
        self.short_description = short_description
        self.author = author
        self.genre = genre
        self.tags = tags
        self.language = language
        self.embedding_vector = None


def _vector_length(vector):
    return len(vector.tolist() if hasattr(vector, "tolist") else vector)


def test_compute_embedding_has_expected_dimension_and_norm():
    vector = compute_embedding("romanzo classico russo")
    values = vector.tolist() if hasattr(vector, "tolist") else vector
    assert _vector_length(vector) == EMBEDDING_DIMENSION
    assert round(sum(value * value for value in values), 4) == 1.0


def test_embedding_roundtrip_and_search_ranking():
    first = DummyBook(1, "Anna Karenina", genre="Classico", tags="russi")
    second = DummyBook(2, "Saggio moderno", genre="Saggio", tags="breve")

    build_and_store_book_embedding(first)
    parsed = deserialize_embedding(first.embedding_vector)
    assert parsed is not None
    assert _vector_length(parsed) == EMBEDDING_DIMENSION

    build_and_store_book_embedding(second)
    index = SemanticBookIndex()
    index.rebuild([first, second])

    result = index.search("classici russi", top_k=1)
    assert result == [1]
