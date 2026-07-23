from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*")
_FALLBACK_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*|[가-힣]+|\d+")
logger = logging.getLogger("mnemome.retrieval")


class MecabNltkTokenizer:
    """Noun tokenizer adapted from ai-assistant's MeCab + NLTK extractor."""

    def __init__(self) -> None:
        self._mecab = self._build_mecab()
        self._nltk = self._build_nltk()

    @staticmethod
    def _build_mecab():
        try:
            from konlpy.tag import Mecab

            return Mecab()
        except Exception as error:
            logger.warning(
                "MeCab tokenizer unavailable; falling back to PeCab error_type=%s",
                type(error).__name__,
            )
            try:
                from pecab import PeCab

                return PeCab()
            except Exception as fallback_error:
                logger.warning(
                    "PeCab tokenizer unavailable; falling back to regex error_type=%s",
                    type(fallback_error).__name__,
                )
                return None

    @staticmethod
    def _build_nltk():
        try:
            import nltk

            nltk.data.find("tokenizers/punkt_tab")
            nltk.data.find("taggers/averaged_perceptron_tagger_eng")
            return nltk
        except (ImportError, LookupError, OSError):
            return None

    def _english_nouns(self, text: str) -> list[str]:
        if self._nltk is not None:
            try:
                words = self._nltk.word_tokenize(text)
                return [word for word, tag in self._nltk.pos_tag(words) if tag.startswith("N")]
            except (LookupError, OSError):
                self._nltk = None
        return _EN_TOKEN_RE.findall(text)

    def __call__(self, text: str) -> list[str]:
        if self._mecab is None:
            return [token.casefold() for token in _FALLBACK_TOKEN_RE.findall(text or "")]

        tagged = self._mecab.pos(text or "")
        groups: list[list[tuple[str, str]]] = [[]]
        for token, pos in tagged:
            if groups[-1] and ((groups[-1][-1][1] == "SL") ^ (pos == "SL")):
                groups.append([])
            groups[-1].append((token, pos))

        output: list[str] = []
        for group in groups:
            if not group:
                continue
            if group[0][1] == "SL":
                output.extend(self._english_nouns(" ".join(token for token, _ in group)))
            else:
                output.extend(token for token, pos in group if pos.startswith(("N", "SN")))
        return [token.casefold() for token in output if token.strip()]


@lru_cache(maxsize=1)
def recall_tokenizer() -> MecabNltkTokenizer:
    return MecabNltkTokenizer()


def recall_backend_label() -> str:
    tokenizer = recall_tokenizer()
    korean_backend = type(tokenizer._mecab).__name__ if tokenizer._mecab else "regex"
    english_backend = "NLTK" if tokenizer._nltk else "regex"
    return f"BM25 · {korean_backend} + {english_backend}"


@lru_cache(maxsize=4096)
def tokenize_for_recall(text: str) -> tuple[str, ...]:
    return tuple(recall_tokenizer()(text))


def bm25_scores(
    query: str,
    documents: Sequence[tuple[str, str, float]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    """Return normalized BM25 scores with a small confidence tie-breaker."""

    query_tokens = tokenize_for_recall(query)
    if not query_tokens or not documents:
        return {}

    tokenized = [
        (doc_id, tokenize_for_recall(text), confidence)
        for doc_id, text, confidence in documents
    ]
    document_frequency: Counter[str] = Counter()
    for _, tokens, _ in tokenized:
        document_frequency.update(set(tokens))

    average_length = sum(len(tokens) for _, tokens, _ in tokenized) / max(len(tokenized), 1)
    corpus_size = len(tokenized)
    raw_scores: dict[str, float] = {}
    confidence_by_id: dict[str, float] = {}
    for doc_id, tokens, confidence in tokenized:
        frequencies = Counter(tokens)
        length_ratio = len(tokens) / max(average_length, 1.0)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if frequency == 0:
                continue
            frequency_in_documents = document_frequency[token]
            inverse_document_frequency = math.log(
                1 + (corpus_size - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
            )
            denominator = frequency + k1 * (1 - b + b * length_ratio)
            score += inverse_document_frequency * (frequency * (k1 + 1)) / denominator
        if score > 0:
            raw_scores[doc_id] = score
            confidence_by_id[doc_id] = confidence

    if not raw_scores:
        return {}
    maximum = max(raw_scores.values())
    return {
        doc_id: round((0.9 * (score / maximum)) + (0.1 * confidence_by_id[doc_id]), 6)
        for doc_id, score in raw_scores.items()
    }


def matched_tokens(query: str, text: str) -> tuple[str, ...]:
    """Expose deterministic match evidence for tests and future traces."""

    query_tokens = set(tokenize_for_recall(query))
    return tuple(sorted(query_tokens.intersection(tokenize_for_recall(text))))
