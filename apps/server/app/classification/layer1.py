import logging
import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from app.classification.buckets import FINE_BUCKETS
from app.classification.clients import PINECONE_EMBED_MODEL
from app.core.config import settings

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 85.0
SEMANTIC_THRESHOLD = 0.75

KEYWORD_SIGNAL = "keyword"
FUZZY_SIGNAL = "fuzzy"
SEMANTIC_SIGNAL = "semantic"


@dataclass(frozen=True)
class Layer1Match:
    fine_bucket: str
    signal: str


def keyword_or_fuzzy_match(text: str, coarse_bucket: str) -> Layer1Match | None:
    lowered = text.lower()
    for bucket in FINE_BUCKETS[coarse_bucket]:
        for keyword in bucket.keywords:
            if re.search(rf"\b{re.escape(keyword.lower())}\b", lowered):
                return Layer1Match(fine_bucket=bucket.name, signal=KEYWORD_SIGNAL)
        best_fuzzy = max(
            (fuzz.partial_ratio(lowered, keyword.lower()) for keyword in bucket.keywords),
            default=0.0,
        )
        # partial_ratio scores an exact, unanchored substring embed (e.g. "PTO"
        # inside "laptop", "resume" inside "presume") as a perfect 100 - the
        # same false positive the word-boundary fix above targets, just
        # rediscovered through the fuzzy path. A genuine word-boundary hit
        # would already have returned above, so any 100 reaching this point
        # is, by construction, a disguised unanchored substring match, not a
        # real near-miss (typo near-misses score below 100). Excluding the
        # exact-100 case keeps true fuzzy/typo tolerance (e.g. "resum" ~
        # "resume" at ~91) while closing the false-positive gap.
        if FUZZY_THRESHOLD <= best_fuzzy < 100:
            return Layer1Match(fine_bucket=bucket.name, signal=FUZZY_SIGNAL)
    return None


def semantic_match(text: str, coarse_bucket: str, pinecone_client: Any | None) -> Layer1Match | None:
    if pinecone_client is None:
        return None
    try:
        embed_response = pinecone_client.inference.embed(
            model=PINECONE_EMBED_MODEL,
            inputs=[text],
            parameters={"input_type": "query", "truncate": "END"},
        )
        vector = embed_response[0]["values"]
        index = pinecone_client.Index(settings.pinecone_index)
        query_response = index.query(
            vector=vector,
            filter={"coarse_bucket": coarse_bucket},
            top_k=1,
            include_metadata=True,
        )
        matches = query_response["matches"]
        if not matches:
            return None
        top = matches[0]
        if top["score"] >= SEMANTIC_THRESHOLD:
            return Layer1Match(fine_bucket=top["metadata"]["fine_bucket"], signal=SEMANTIC_SIGNAL)
        return None
    except Exception:
        logger.warning(
            "Semantic-similarity signal failed for coarse_bucket=%s", coarse_bucket, exc_info=True
        )
        return None


def match(text: str, coarse_bucket: str, pinecone_client: Any | None) -> Layer1Match | None:
    result = keyword_or_fuzzy_match(text, coarse_bucket)
    if result is not None:
        return result
    return semantic_match(text, coarse_bucket, pinecone_client)
