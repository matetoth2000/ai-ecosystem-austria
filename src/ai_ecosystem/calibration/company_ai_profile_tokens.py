"""Provisional local allocation and exact model-specific input-token verification."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from functools import lru_cache

import tiktoken

from .company_ai_profile_contract import canonical


@lru_cache(maxsize=2)
def _encoding(name: str):
    return tiktoken.get_encoding(name)


def local_tokens(text: str, config: dict) -> int:
    """Count allocation tokens; this encoding is not asserted to be Luna's."""
    name = config["token_counting"]["allocation_encoding"]
    return len(_encoding(name).encode(text))


def count_payload(body: dict) -> dict:
    """Only fields accepted by the count endpoint; no generation is requested."""
    return {key: body[key] for key in ("model", "reasoning", "input", "text") if key in body}


def count_input_tokens(client, body: dict) -> int:
    response = client.responses.input_tokens.count(**count_payload(body))
    value = int(response.input_tokens)
    if value < 0:
        raise ValueError("Token-count endpoint returned a negative count")
    return value


def non_evidence_payload(body: dict) -> dict:
    blank = deepcopy(body)
    blank["input"][1]["content"] = ""
    return blank


def non_evidence_breakdown(client, body: dict) -> dict:
    """Count disjoint prompt, schema, and framing increments for this request shape."""
    blank = non_evidence_payload(body)
    full_baseline = count_input_tokens(client, blank)
    without_schema = deepcopy(blank)
    without_schema.pop("text", None)
    prompt_only = count_input_tokens(client, without_schema)
    without_schema["input"][0]["content"] = ""
    framing = count_input_tokens(client, without_schema)
    if not 0 <= framing <= prompt_only <= full_baseline:
        raise ValueError("Non-evidence token counts were not monotone")
    return {"non_evidence_input_tokens_exact": full_baseline,
            "prompt_input_tokens_exact": prompt_only - framing,
            "schema_input_tokens_exact": full_baseline - prompt_only,
            "framing_input_tokens_exact": framing}


def token_breakdown(client, body: dict, non_evidence_tokens: int) -> dict:
    input_tokens = count_input_tokens(client, body)
    evidence_tokens = input_tokens - non_evidence_tokens
    if evidence_tokens < 0:
        raise ValueError("Request count is below its non-evidence baseline")
    return {"request_input_tokens_exact": input_tokens,
            "website_evidence_tokens_exact": evidence_tokens,
            "non_evidence_input_tokens_exact": non_evidence_tokens,
            "model_count_payload_sha256": hashlib.sha256(
                canonical(count_payload(body)).encode("utf-8")).hexdigest()}


def reservation_usd(config: dict, input_tokens: int) -> float:
    price = config["pricing"]
    factor = price["long_context_input_multiplier"] if input_tokens > price["long_context_input_threshold"] else 1
    output_factor = price["long_context_output_multiplier"] if factor != 1 else 1
    return (input_tokens * price["input_per_million"] * factor +
            config["max_output_tokens"] * price["output_per_million"] * output_factor) / 1_000_000
