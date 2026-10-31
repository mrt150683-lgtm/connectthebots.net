"""In-memory lexical search adapter for the ``SearchIndex`` port.

Deliberately simple: lower-case word tokens, every query word must prefix-match
a document word, structured filters, and a stable order (more matched words,
then newer, then notice id). It is a stand-in for a future indexed service
(for example PostgreSQL full-text search), not a relevance model.

The index is a derived copy. It is *not* updated on expiry, blocks, brief
edits or membership changes; the service's visibility gate handles those at
read time. Tests rely on this to prove that stale entries cannot leak.
"""
from __future__ import annotations

import re

from ..application.ports import MAX_RESULTS, IndexDocument, SearchHit, SearchQuery

_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.lower()))


class InMemoryLexicalIndex:
    def __init__(self) -> None:
        self._documents: dict[str, tuple[IndexDocument, frozenset[str]]] = {}

    def upsert(self, document: IndexDocument) -> None:
        self._documents[document.notice_id] = (document, frozenset(tokens(document.text)))

    def remove(self, notice_id: str) -> None:
        self._documents.pop(notice_id, None)

    def query(self, query: SearchQuery) -> list[SearchHit]:
        wanted = tokens(query.text)[:8]
        scored = []
        for document, words in self._documents.values():
            if document.board_id not in query.board_ids:
                continue
            if query.mode and document.mode != query.mode:
                continue
            if query.focus_area and query.focus_area not in document.focus_areas:
                continue
            if query.offers and query.offers not in document.offers:
                continue
            if query.basis and document.basis != query.basis:
                continue
            if query.venue and document.venue != query.venue:
                continue
            matched = sum(any(word.startswith(term) for word in words) for term in wanted)
            if matched != len(wanted):
                continue
            scored.append((-matched, -document.published_at.timestamp(), document.notice_id, document.revision))
        scored.sort()
        return [SearchHit(notice_id, revision) for _, _, notice_id, revision in scored[:min(query.limit, MAX_RESULTS)]]
