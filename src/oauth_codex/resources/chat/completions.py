from __future__ import annotations

import json
import inspect
import time
import uuid
from typing import Any

from oauth_codex._exceptions import BadRequestError
from oauth_codex.tooling import (
    build_strict_response_format,
    normalize_tool_inputs,
    to_responses_tools,
)
from oauth_codex.types.chat.completions import ChatCompletion

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


def _normalize_tool_call_item(tool_call: Any) -> dict[str, Any] | None:
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return None
        call_id = tool_call.get("id") or tool_call.get("call_id")
        name = function.get("name")
        arguments = function.get("arguments")
    else:
        function = getattr(tool_call, "function", None)
        call_id = getattr(tool_call, "id", None) or getattr(tool_call, "call_id", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)

    if not isinstance(call_id, str) or not call_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments)
    if not isinstance(arguments, str):
        arguments = "{}"

    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _translate_content_parts(content: Any) -> Any:
    """Translate Chat Completions content part types to Responses API types.

    Chat Completions uses ``"text"`` and ``"image_url"`` part types, while the
    Responses API requires ``"input_text"`` and ``"input_image"``.  Plain string
    content is returned unchanged.
    """
    if not isinstance(content, list):
        return content
    translated: list[Any] = []
    for part in content:
        if not isinstance(part, dict):
            translated.append(part)
            continue
        part_type = part.get("type")
        if part_type == "text":
            translated.append({**part, "type": "input_text"})
        elif part_type == "image_url":
            image_url_value = part.get("image_url")
            url = (
                image_url_value.get("url")
                if isinstance(image_url_value, dict)
                else image_url_value
            )
            translated.append({"type": "input_image", "image_url": url})
        else:
            translated.append(part)
    return translated


def _messages_to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response_items: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        item_type = message.get("type")
        if item_type in {"function_call", "function_call_output"}:
            response_items.append(dict(message))
            continue

        role = message.get("role")

        if role == "tool":
            call_id = message.get("tool_call_id") or message.get("call_id")
            if isinstance(call_id, str) and call_id.strip():
                response_items.append(
                    _build_function_call_output_item(
                        tool_call_id=call_id,
                        output=message.get("content", ""),
                    )
                )
            continue

        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                content = message.get("content")
                if isinstance(content, str) and content:
                    response_items.append({"role": "assistant", "content": content})
                elif isinstance(content, list) and content:
                    response_items.append(
                        {"role": "assistant", "content": _translate_content_parts(content)}
                    )

                for tool_call in tool_calls:
                    normalized_tool_call = _normalize_tool_call_item(tool_call)
                    if normalized_tool_call is not None:
                        response_items.append(normalized_tool_call)
                continue

        content = message.get("content")
        if content is None:
            continue
        response_items.append({"role": role, "content": _translate_content_parts(content)})

    return response_items


def _parse_text_with_model(response_format: type[Any], text: str) -> Any:
    try:
        return response_format.model_validate_json(text)
    except Exception:
        return response_format.model_validate(json.loads(text))


def _extract_output_text(response_data: dict[str, Any]) -> str:
    output_text = response_data.get("output_text")
    if isinstance(output_text, str):
        return output_text

    chunks: list[str] = []
    output = response_data.get("output")
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


def _extract_tool_calls(response_data: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    output = response_data.get("output")
    if not isinstance(output, list):
        return tool_calls

    for item in output:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type not in {"function_call", "tool_call"}:
            continue

        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue

        # In Codex, arguments are often in `arguments_json` or `arguments` as a dict
        arguments = item.get("arguments_json")
        if not isinstance(arguments, str):
            args_dict = item.get("arguments")
            if isinstance(args_dict, dict):
                arguments = json.dumps(args_dict)
            elif isinstance(args_dict, str):
                arguments = args_dict
            else:
                arguments = "{}"

        call_id = item.get("call_id") or item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{uuid.uuid4().hex}"

        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )

    return tool_calls


def _build_assistant_message(completion: ChatCompletion) -> dict[str, Any]:
    message = completion.choices[0].message
    out: dict[str, Any] = {"role": "assistant"}
    if message.content is not None:
        out["content"] = message.content
    if message.tool_calls:
        out["tool_calls"] = [tool_call.to_dict() for tool_call in message.tool_calls]
    return out


def _coerce_tool_output_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True)
    except TypeError:
        return str(value)


def _build_function_call_output_item(
    *, tool_call_id: str, output: Any
) -> dict[str, Any]:
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("Tool call id must be a non-empty string")
    return {
        "type": "function_call_output",
        "call_id": tool_call_id,
        "output": _coerce_tool_output_content(output),
    }


def _build_tool_function_map(tools: list[Any]) -> dict[str, Any]:
    tool_map = {}
    for tool in tools:
        if callable(tool):
            name = getattr(tool, "__name__", "tool")
            tool_map[name] = tool
        elif isinstance(tool, dict) and "function" in tool:
            # Not a callable directly but maybe user wants it?
            # OpenAI SDK run_tools specifically requires callables.
            pass
    return tool_map


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str) or not arguments.strip():
        raise ValueError(
            f"Tool arguments must be a valid JSON object string, got: {arguments!r}"
        )

    try:
        parsed = json.loads(arguments)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("Parsed arguments is not a dictionary")
    except Exception as e:
        raise ValueError(f"Failed to parse tool arguments: {arguments!r}") from e


def _run_tool_function(*, fn: Any, arguments: Any) -> Any:
    if isinstance(arguments, dict):
        return fn(**arguments)
    return fn(arguments)


def _to_chat_completion(
    *,
    response_data: dict[str, Any],
    requested_model: str,
) -> ChatCompletion:
    tool_calls = _extract_tool_calls(response_data)
    output_text = _extract_output_text(response_data)

    content: str | None = output_text
    if not content and tool_calls:
        content = None

    finish_reason = response_data.get("finish_reason")
    if not isinstance(finish_reason, str):
        finish_reason = "tool_calls" if tool_calls else "stop"

    usage_data = response_data.get("usage")
    prompt_tokens = 0
    completion_tokens = 0

    if isinstance(usage_data, dict):
        prompt_tokens = int(
            usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0
        )
        completion_tokens = int(
            usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0
        )
    total_tokens = prompt_tokens + completion_tokens

    return ChatCompletion.model_validate(
        {
            "id": response_data.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
            "created": int(response_data.get("created") or time.time()),
            "model": response_data.get("model") or requested_model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls or None,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": int(usage_data.get("total_tokens") or total_tokens)
                if isinstance(usage_data, dict)
                else total_tokens,
            },
            "system_fingerprint": response_data.get("system_fingerprint"),
            "raw_response": response_data,
        }
    )


def _to_chat_completion_from_stream_events(
    *, events: list[Any], requested_model: str
) -> ChatCompletion:
    response_data: dict[str, Any] | None = None
    output_chunks: list[str] = []
    output_items: dict[str, dict[str, Any]] = {}
    output_item_order: list[str] = []
    function_argument_chunks: dict[str, list[str]] = {}

    for event in events:
        raw_event = getattr(event, "raw", None)
        if not isinstance(raw_event, dict):
            continue

        event_type = raw_event.get("type")
        if event_type == "response.output_text.delta":
            delta = raw_event.get("delta")
            if isinstance(delta, str):
                output_chunks.append(delta)
        elif event_type in {"response.output_item.added", "response.output_item.done"}:
            item = raw_event.get("item")
            if isinstance(item, dict):
                item_id = item.get("id")
                if isinstance(item_id, str) and item_id:
                    existing = output_items.get(item_id, {})
                    merged_item = {**existing, **item}
                    if item_id in function_argument_chunks and not merged_item.get("arguments"):
                        merged_item["arguments"] = "".join(function_argument_chunks[item_id])
                    output_items[item_id] = merged_item
                    if item_id not in output_item_order:
                        output_item_order.append(item_id)
        elif event_type == "response.function_call_arguments.delta":
            item_id = raw_event.get("item_id")
            delta = raw_event.get("delta")
            if isinstance(item_id, str) and item_id and isinstance(delta, str):
                function_argument_chunks.setdefault(item_id, []).append(delta)
        elif event_type == "response.function_call_arguments.done":
            item_id = raw_event.get("item_id")
            arguments = raw_event.get("arguments")
            if isinstance(item_id, str) and item_id:
                if not isinstance(arguments, str):
                    arguments = "".join(function_argument_chunks.get(item_id, []))
                existing = output_items.get(item_id, {"id": item_id, "type": "function_call"})
                existing["arguments"] = arguments
                output_items[item_id] = existing
                if item_id not in output_item_order:
                    output_item_order.append(item_id)
        elif event_type == "response.completed":
            response_payload = raw_event.get("response")
            if isinstance(response_payload, dict):
                response_data = response_payload

    if response_data is None:
        raise ValueError("No response.completed event found in streamed response")

    response_data = dict(response_data)
    response_data.setdefault("output_text", "".join(output_chunks))
    if not response_data.get("output") and output_item_order:
        response_data["output"] = [
            output_items[item_id]
            for item_id in output_item_order
            if item_id in output_items
        ]
    return _to_chat_completion(response_data=response_data, requested_model=requested_model)


class Completions:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(
        self, *, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> ChatCompletion:
        payload = dict(kwargs)
        if "response_format" in payload:
            payload["response_format"] = _normalize_response_format(
                payload["response_format"]
            )
        if "tools" in payload:
            payload["tools"] = _normalize_tools(payload["tools"])
        payload["model"] = model
        payload["input"] = _messages_to_responses_input(messages)

        try:
            response = self._client.responses.create(**payload)
            response_data = response.to_dict(exclude_unset=False, exclude_none=False)
            return _to_chat_completion(response_data=response_data, requested_model=model)
        except BadRequestError as exc:
            response = getattr(exc, "response", None)
            detail = None
            if response is not None:
                try:
                    detail = response.text
                except Exception:
                    detail = None

            if not detail or "Stream must be set to true" not in detail:
                raise

            events = list(self._client.responses.stream(**payload))
            return _to_chat_completion_from_stream_events(
                events=events,
                requested_model=model,
            )

    def parse(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: type[Any],
        **kwargs: Any,
    ) -> ChatCompletion:
        if not _is_pydantic_model_type(response_format):
            raise TypeError("response_format must be a Pydantic model type")

        completion = self.create(
            model=model,
            messages=messages,
            response_format=response_format,
            **kwargs,
        )

        text = ""
        if completion.choices:
            text = completion.choices[0].message.content or ""

        parsed = _parse_text_with_model(response_format, text)
        setattr(completion, "parsed", parsed)
        for choice in completion.choices:
            setattr(choice.message, "parsed", parsed)
        return completion


class BetaCompletions(Completions):
    def run_tools(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
        max_rounds: int = 10,
        **kwargs: Any,
    ) -> ChatCompletion:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")

        if kwargs.get("stream"):
            raise ValueError("run_tools does not support stream=True")

        if not isinstance(tools, list) or not tools:
            raise ValueError("tools must be a non-empty list")

        tool_functions = _build_tool_function_map(tools)
        if not tool_functions:
            raise ValueError("run_tools requires callable tools")

        round_input = [dict(message) for message in messages]
        request_kwargs = dict(kwargs)
        previous_response_id = request_kwargs.pop("previous_response_id", None)

        for _ in range(max_rounds):
            create_kwargs = {
                "model": model,
                "messages": round_input,
                "tools": tools,
                **request_kwargs,
            }
            if previous_response_id is not None:
                create_kwargs["previous_response_id"] = previous_response_id
            completion = self.create(**create_kwargs)

            if not completion.choices:
                return completion

            assistant_message = completion.choices[0].message
            tool_calls = assistant_message.tool_calls or []
            if not tool_calls:
                return completion

            previous_response_id = completion.id
            next_input: list[dict[str, Any]] = []

            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                fn = tool_functions.get(fn_name)
                if fn is None:
                    raise ValueError(f"No callable tool provided for '{fn_name}'")

                arguments = _parse_tool_arguments(tool_call.function.arguments)
                output = _run_tool_function(fn=fn, arguments=arguments)
                if inspect.isawaitable(output):
                    raise TypeError(
                        f"Tool '{fn_name}' returned an awaitable; use arun_tools for async tools"
                    )

                next_input.append(
                    _build_function_call_output_item(
                        tool_call_id=tool_call.id,
                        output=output,
                    )
                )

            round_input = next_input

        raise RuntimeError(
            "run_tools exceeded max_rounds without reaching a final response"
        )


class AsyncCompletions:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def create(
        self, *, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> ChatCompletion:
        payload = dict(kwargs)
        if "response_format" in payload:
            payload["response_format"] = _normalize_response_format(
                payload["response_format"]
            )
        if "tools" in payload:
            payload["tools"] = _normalize_tools(payload["tools"])
        payload["model"] = model
        payload["input"] = _messages_to_responses_input(messages)

        response = await self._client.responses.create(**payload)
        response_data = response.to_dict(exclude_unset=False, exclude_none=False)
        return _to_chat_completion(response_data=response_data, requested_model=model)

    async def aparse(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: type[Any],
        **kwargs: Any,
    ) -> ChatCompletion:
        if not _is_pydantic_model_type(response_format):
            raise TypeError("response_format must be a Pydantic model type")

        completion = await self.create(
            model=model,
            messages=messages,
            response_format=response_format,
            **kwargs,
        )

        text = ""
        if completion.choices:
            text = completion.choices[0].message.content or ""

        parsed = _parse_text_with_model(response_format, text)
        setattr(completion, "parsed", parsed)
        for choice in completion.choices:
            setattr(choice.message, "parsed", parsed)
        return completion


class AsyncBetaCompletions(AsyncCompletions):
    async def arun_tools(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
        max_rounds: int = 10,
        **kwargs: Any,
    ) -> ChatCompletion:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")

        if kwargs.get("stream"):
            raise ValueError("arun_tools does not support stream=True")

        if not isinstance(tools, list) or not tools:
            raise ValueError("tools must be a non-empty list")

        tool_functions = _build_tool_function_map(tools)
        if not tool_functions:
            raise ValueError("arun_tools requires callable tools")

        round_input = [dict(message) for message in messages]
        request_kwargs = dict(kwargs)
        previous_response_id = request_kwargs.pop("previous_response_id", None)

        for _ in range(max_rounds):
            create_kwargs = {
                "model": model,
                "messages": round_input,
                "tools": tools,
                **request_kwargs,
            }
            if previous_response_id is not None:
                create_kwargs["previous_response_id"] = previous_response_id
            completion = await self.create(**create_kwargs)

            if not completion.choices:
                return completion

            assistant_message = completion.choices[0].message
            tool_calls = assistant_message.tool_calls or []
            if not tool_calls:
                return completion

            previous_response_id = completion.id
            next_input: list[dict[str, Any]] = []

            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                fn = tool_functions.get(fn_name)
                if fn is None:
                    raise ValueError(f"No callable tool provided for '{fn_name}'")

                arguments = _parse_tool_arguments(tool_call.function.arguments)
                output = _run_tool_function(fn=fn, arguments=arguments)
                if inspect.isawaitable(output):
                    output = await output

                next_input.append(
                    _build_function_call_output_item(
                        tool_call_id=tool_call.id,
                        output=output,
                    )
                )

            round_input = next_input

        raise RuntimeError(
            "arun_tools exceeded max_rounds without reaching a final response"
        )


class Chat:
    def __init__(self, client: Any) -> None:
        self.completions = Completions(client)


class AsyncChat:
    def __init__(self, client: Any) -> None:
        self.completions = AsyncCompletions(client)


class BetaChat:
    def __init__(self, client: Any) -> None:
        self.completions = BetaCompletions(client)


class AsyncBetaChat:
    def __init__(self, client: Any) -> None:
        self.completions = AsyncBetaCompletions(client)
