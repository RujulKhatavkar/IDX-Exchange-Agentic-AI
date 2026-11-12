import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'idx-rag'))
from rag import MODEL, build_index, chunk_text, read_source, retrieve


def test_chunks_overlap_without_trailing_duplicate():
    assert chunk_text('abcdefghij', 6, 2) == ['abcdef', 'efghij']
    assert chunk_text('   ') == []
    with pytest.raises(ValueError):
        chunk_text('text', 10, 10)


def test_build_and_retrieve_cited_source(tmp_path):
    source = tmp_path / 'glossary.txt'
    source.write_text('DOM means days on market.')
    destination = tmp_path / 'index.json'
    calls = []

    def embedding(texts, kind):
        calls.append(kind)
        return [[2.0, 0.0] for _ in texts]

    import json
    build_index([source], destination, embedding)
    result = retrieve('DOM?', json.loads(destination.read_text()), embed_fn=embedding)
    assert calls == ['passage', 'query']
    assert result['evidence'][0]['score'] == 1
    assert result['evidence'][0]['path'] == str(source)
    assert result['evidence'][0]['citation'] == 'S1'
    assert result['evidence'][0]['text'] == source.read_text()


def test_unrelated_query_abstains():
    index = {'model': MODEL, 'chunks': [{'text': 'DOM'}], 'vectors': [[1, 0]]}
    result = retrieve('escrow?', index, embed_fn=lambda *_: [[0, 1]])
    assert result['status'] == 'insufficient_evidence'
    assert result['evidence'] == []


def test_invalid_vectors_rejected():
    index = {'model': MODEL, 'chunks': [{'text': 'DOM'}], 'vectors': [[0, 0]]}
    with pytest.raises(ValueError):
        retrieve('DOM?', index, embed_fn=lambda *_: [[1, 0]])


def test_empty_source_does_not_replace_existing_index(tmp_path):
    source = tmp_path / 'empty.txt'
    source.write_text('')
    destination = tmp_path / 'index.json'
    destination.write_text('existing')
    with pytest.raises(ValueError):
        build_index([source], destination)
    assert destination.read_text() == 'existing'
