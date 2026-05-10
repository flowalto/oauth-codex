from __future__ import annotations

import json
from typing import Any

from oauth_codex._models import BaseModel
from oauth_codex.tooling import (
    build_strict_response_format,
    normalize_tool_inputs,
    to_responses_tools,
)

PydanticBaseModel: Any = None
try:
    from pydantic import BaseModel as PydanticBaseModel
except Exception:
    pass


def _is_pydantic_model_type(value: Any) -> bool:
    return bool(
        PydanticBaseModel is not None
        and isinstance(value, type)
        and issubclass(value, PydanticBaseModel)
    )


def _normalize_response_format(response_format: Any) -> Any:
    if _is_pydantic_model_type(response_format):
        return build_strict_response_format(response_format)
    return response_format


def _normalize_tools(tools: Any) -> Any:
    if not isinstance(tools, list):
        return tools
    return to_responses_tools(normalize_tool_inputs(tools))


def _extract_response_output_text(response: Response) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text

    chunks: list[str] = []
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return ""

    for item in output:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            chunks.append(item["text"])

        content = item.get("content")
        if not isinstance(content, list):
            continue

        for entry in content:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            if entry_type in {"text", "output_text"} and isinstance(
                entry.get("text"), str
            ):
                chunks.append(entry["text"])

    return "".join(chunks)


def _parse_text_with_model(response_format: type[Any], text: str) -> Any:
    try:
        return response_format.model_validate_json(text)
    except Exception:
        return response_format.model_validate(json.loads(text))


class Response(BaseModel):
    pass


class Responses:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(self, **json_data: Any) -> Response:
        if "response_format" in json_data:
            json_data["response_format"] = _normalize_response_format(
                json_data["response_format"]
            )
        if "tools" in json_data:
            json_data["tools"] = _normalize_tools(json_data["tools"])
        response = self._client.request("POST", "/responses", json_data=json_data)
        return Response.model_validate(response.json())

    def parse(self, *, response_format: type[Any], **json_data: Any) -> Response:
        if not _is_pydantic_model_type(response_format):
            raise TypeError("response_format must be a Pydantic model type")

        response = self.create(response_format=response_format, **json_data)
        parsed = _parse_text_with_model(
            response_format, _extract_response_output_text(response)
        )
        setattr(response, "parsed", parsed)
        return response


class AsyncResponses:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def create(self, **json_data: Any) -> Response:
        if "response_format" in json_data:
            json_data["response_format"] = _normalize_response_format(
                json_data["response_format"]
            )
        if "tools" in json_data:
            json_data["tools"] = _normalize_tools(json_data["tools"])
        response = await self._client.request("POST", "/responses", json_data=json_data)
        return Response.model_validate(response.json())

    async def aparse(self, *, response_format: type[Any], **json_data: Any) -> Response:
        if not _is_pydantic_model_type(response_format):
            raise TypeError("response_format must be a Pydantic model type")

        response = await self.create(response_format=response_format, **json_data)
        parsed = _parse_text_with_model(
            response_format, _extract_response_output_text(response)
        )
        setattr(response, "parsed", parsed)
        return response
