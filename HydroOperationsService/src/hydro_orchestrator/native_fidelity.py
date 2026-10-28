"""Accept a source answer only when the completed Foundry reply preserves it."""

import json
import logging
from hashlib import sha256

from .live_sources import SourceFailure

logger = logging.getLogger(__name__)


def source_answer(body: dict, question: str, server_label: str) -> tuple[dict, str]:
    if body.get("status") != "completed" or body.get("error"):
        raise SourceFailure("Foundry native execution did not complete successfully.")
    outputs = body.get("output")
    if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
        raise SourceFailure("Foundry native execution has no inspectable output.")
    filters = body.get("content_filters")
    calls = [item for item in outputs if item.get("type") == "mcp_call"]
    if len(calls) != 1:
        raise SourceFailure("Native fidelity requires exactly one completed selected-source call.")
    call = calls[0]
    if call.get("server_label") != server_label or call.get("status") != "completed" or call.get("error"):
        raise SourceFailure("Native execution did not use the verified selected source.")
    try:
        arguments = json.loads(call["arguments"])
        payload = json.loads(call["output"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise SourceFailure("Native execution returned a malformed source receipt.") from error
    if arguments != {"userQuestion": question}:
        raise SourceFailure("Foundry changed the question before invoking the Data Agent.")
    if not isinstance(payload, dict) or payload.get("isError") or payload.get("error"):
        raise SourceFailure("The Data Agent returned a source error.")
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise SourceFailure("The Data Agent returned no source answer.")
    final = "\n".join(
        block["text"] for item in outputs if item.get("type") == "message"
        and item.get("role") == "assistant"
        for block in item.get("content", []) if block.get("type") == "output_text"
        and isinstance(block.get("text"), str)
    )
    if final.strip() != text.strip():
        raise SourceFailure("Foundry changed the Data Agent answer; no rewritten answer was accepted.")
    if not isinstance(filters, list) or not filters:
        raise SourceFailure("Foundry native execution returned no final safety assessment.")
    completion_checked = False
    for assessment in filters:
        if not isinstance(assessment, dict) or assessment.get("blocked") is not False:
            raise SourceFailure("Foundry native safety assessment is blocked or incomplete.")
        results = assessment.get("content_filter_results")
        if not isinstance(results, dict) or not results:
            raise SourceFailure("Foundry native safety assessment is incomplete.")
        for result in results.values():
            if not isinstance(result, dict) or result.get("filtered") is not False:
                raise SourceFailure("Foundry native output was filtered or lacks a safety decision.")
        completion_checked |= assessment.get("source_type") == "completion"
    if not completion_checked:
        raise SourceFailure("Foundry native execution has no completed output safety assessment.")
    annotations = payload.get("raiPostToolAnnotations", [])
    for annotation in annotations:
        diagnostic = annotation.get("error", {}).get("errorDetails", "") if isinstance(annotation, dict) else ""
        if not isinstance(diagnostic, str) or not diagnostic.startswith("Error during output moderation for tool:"):
            raise SourceFailure("The Data Agent returned an unrecognized tool safety annotation.")
        logger.warning(
            "Foundry returned a post-tool processing diagnostic for response %s; the identical final answer "
            "passed the completed Foundry content-filter assessment.", body.get("id"),
        )
    # Keep annotations in the immutable raw receipt, not in the domain-data parser.
    result = {key: value for key, value in payload.items() if key != "raiPostToolAnnotations"}
    result["source_answer_sha256"] = sha256(text.encode()).hexdigest()
    return result, text
