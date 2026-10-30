"""Offline AI provider used when AI_PROVIDER=local.

It mimics the small part of the google-genai client that DocuMind uses
(`client.models.embed_content` and `client.models.generate_content`) so the
rest of the pipeline is unchanged. Nothing here makes a network call.

- Embeddings: hashed bag of word unigrams and bigrams (768 dims, L2-normalised).
  This is lexical, not semantic; it exists for tests, offline development and
  as an evaluation baseline. It is not a substitute for a real embedding model.
- Generation: extractive. It picks the source sentence with the highest word
  overlap with the question, or returns the standard refusal. It is not an LLM.
"""

import hashlib
import math
import re
from dataclasses import dataclass, field

from app.models.document_chunk import EMBEDDING_DIMENSIONS

NOT_FOUND_MESSAGE = "I couldn't find this in your documents."

_TOKEN = re.compile(r"[a-z0-9]+")
# Sentences end at punctuation or a blank line; single newlines are PDF line wraps.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n\s*\n")
_SOURCE_BLOCK = re.compile(
    r'<source id="(S\d+)"[^>]*>\n?(.*?)\n?</source>', re.DOTALL
)
_QUESTION_BLOCK = re.compile(r"<question>\n?(.*?)\n?</question>", re.DOTALL)

_STOPWORDS = frozenset(
    """a an and are as at be by can do does for from has have how i in is it
    its my of on or our that the their this to was we what when where which
    who whom why will with you your me am being been did there these those
    any all about into than then so if not no yes should would could may
    must per each""".split()
)

# Minimum fraction of question keywords a sentence must share to be used.
MIN_OVERLAP = 0.5


def _stem(token: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def keywords(text: str) -> list[str]:
    return [
        _stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS
    ]


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % EMBEDDING_DIMENSIONS, 1.0 if (value >> 63) & 1 else -1.0


def embed(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = keywords(text)
    counts: dict[str, int] = {}

    for feature in tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]:
        counts[feature] = counts.get(feature, 0) + 1

    for feature, count in counts.items():
        index, sign = _bucket(feature)
        vector[index] += sign * (1.0 + math.log(count))

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        # Empty text: a fixed unit vector keeps cosine distance defined.
        vector[0] = 1.0
        return vector

    return [v / norm for v in vector]


def extractive_answer(question: str, sources: list[tuple[str, str]]) -> str:
    wanted = set(keywords(question))
    if not wanted:
        return NOT_FOUND_MESSAGE

    best: tuple[float, str, str] | None = None

    for source_id, content in sources:
        for sentence in _SENTENCE_SPLIT.split(content):
            sentence = " ".join(sentence.split())
            if len(sentence) < 15:
                continue
            overlap = len(wanted & set(keywords(sentence))) / len(wanted)
            if best is None or overlap > best[0]:
                best = (overlap, sentence, source_id)

    if best is None or best[0] < MIN_OVERLAP:
        return NOT_FOUND_MESSAGE

    return f"{best[1]} [{best[2]}]"


@dataclass
class _Embedding:
    values: list[float]


@dataclass
class _EmbedResponse:
    embeddings: list[_Embedding]


@dataclass
class _UsageMetadata:
    prompt_token_count: int
    candidates_token_count: int
    thoughts_token_count: int = 0


@dataclass
class _GenerateResponse:
    text: str
    usage_metadata: _UsageMetadata = field(default=None)  # type: ignore[assignment]


class _LocalModels:
    def embed_content(self, *, model=None, contents, config=None):
        items = [contents] if isinstance(contents, str) else list(contents)
        return _EmbedResponse([_Embedding(embed(text)) for text in items])

    def generate_content(self, *, model=None, contents, config=None):
        prompt = contents if isinstance(contents, str) else str(contents)
        question_match = _QUESTION_BLOCK.search(prompt)
        question = question_match.group(1) if question_match else ""
        sources = _SOURCE_BLOCK.findall(prompt)
        text = extractive_answer(question, sources)

        return _GenerateResponse(
            text=text,
            # Rough 4-characters-per-token estimate; there is no real tokenizer.
            usage_metadata=_UsageMetadata(
                prompt_token_count=max(1, len(prompt) // 4),
                candidates_token_count=max(1, len(text) // 4),
            ),
        )


class LocalClient:
    def __init__(self) -> None:
        self.models = _LocalModels()
