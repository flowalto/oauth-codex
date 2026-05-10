from __future__ import annotations

import copy
import inspect
import json
from types import UnionType
from typing import Any, get_args, get_origin, get_type_hints

from .core_types import ToolInput, ToolResult, ToolSchema
from .errors import SDKRequestError

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover - pydantic is a runtime dependency
    BaseModel = None  # type: ignore[assignment]


def _is_pydantic_model_type(annotation: Any) -> bool:
    return bool(
        BaseModel is not None
        and isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
    )


def _pydantic_model_to_schema(model_type: type[Any]) -> dict[str, Any]:
    if hasattr(model_type, "model_json_schema"):
        schema = model_type.model_json_schema()
        if isinstance(schema, dict):
            return schema
    return {"type": "object"}


def _resolve_json_pointer_ref(*, root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local JSON pointer refs are supported, got: {ref}")

    target: Any = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            raise ValueError(f"Unresolvable JSON pointer ref: {ref}")
        target = target[part]

    if not isinstance(target, dict):
        raise ValueError(f"JSON pointer ref must resolve to an object schema: {ref}")
    return copy.deepcopy(target)


def _ensure_strict_json_schema(
    json_schema: dict[str, Any],
    *,
    path: tuple[str, ...] = (),
    root: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(json_schema, dict):
        raise TypeError(f"Expected dict at path={path!r}, got {type(json_schema).__name__}")

    if root is None:
        root = json_schema

    defs = json_schema.get("$defs")
    if defs is not None:
        if not isinstance(defs, dict):
            raise TypeError(f"Expected $defs to be a dict at path={path!r}")
        for def_name, def_schema in defs.items():
            if not isinstance(def_schema, dict):
                raise TypeError(f"Expected object schema in $defs[{def_name!r}]")
            _ensure_strict_json_schema(
                def_schema,
                path=(*path, "$defs", str(def_name)),
                root=root,
            )

    definitions = json_schema.get("definitions")
    if definitions is not None:
        if not isinstance(definitions, dict):
            raise TypeError(f"Expected definitions to be a dict at path={path!r}")
        for definition_name, definition_schema in definitions.items():
            if not isinstance(definition_schema, dict):
                raise TypeError(f"Expected object schema in definitions[{definition_name!r}]")
            _ensure_strict_json_schema(
                definition_schema,
                path=(*path, "definitions", str(definition_name)),
                root=root,
            )

    if json_schema.get("type") == "object" and "additionalProperties" not in json_schema:
        json_schema["additionalProperties"] = False

    properties = json_schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise TypeError(f"Expected properties to be a dict at path={path!r}")
        json_schema["required"] = list(properties.keys())
        json_schema["properties"] = {
            key: _ensure_strict_json_schema(
                prop_schema,
                path=(*path, "properties", str(key)),
                root=root,
            )
            for key, prop_schema in properties.items()
        }

    items = json_schema.get("items")
    if items is not None:
        if isinstance(items, dict):
            json_schema["items"] = _ensure_strict_json_schema(
                items,
                path=(*path, "items"),
                root=root,
            )
        elif isinstance(items, list):
            json_schema["items"] = [
                _ensure_strict_json_schema(
                    entry,
                    path=(*path, "items", str(i)),
                    root=root,
                )
                for i, entry in enumerate(items)
            ]
        else:
            raise TypeError(f"Expected items to be a dict or list at path={path!r}")

    any_of = json_schema.get("anyOf")
    if any_of is not None:
        if not isinstance(any_of, list):
            raise TypeError(f"Expected anyOf to be a list at path={path!r}")
        json_schema["anyOf"] = [
            _ensure_strict_json_schema(
                variant,
                path=(*path, "anyOf", str(i)),
                root=root,
            )
            for i, variant in enumerate(any_of)
        ]

    all_of = json_schema.get("allOf")
    if all_of is not None:
        if not isinstance(all_of, list):
            raise TypeError(f"Expected allOf to be a list at path={path!r}")
        if len(all_of) == 1:
            entry = _ensure_strict_json_schema(
                all_of[0],
                path=(*path, "allOf", "0"),
                root=root,
            )
            json_schema.update(entry)
            json_schema.pop("allOf", None)
        else:
            json_schema["allOf"] = [
                _ensure_strict_json_schema(
                    entry,
                    path=(*path, "allOf", str(i)),
                    root=root,
                )
                for i, entry in enumerate(all_of)
            ]

    if json_schema.get("default", object()) is None:
        json_schema.pop("default", None)

    ref = json_schema.get("$ref")
    if ref is not None and len(json_schema) > 1:
        if not isinstance(ref, str):
            raise TypeError(f"Expected $ref to be a string at path={path!r}")
        resolved = _resolve_json_pointer_ref(root=root, ref=ref)
        json_schema.update({**resolved, **json_schema})
        json_schema.pop("$ref", None)
        return _ensure_strict_json_schema(json_schema, path=path, root=root)

    return json_schema


def build_strict_response_format(output_schema: type[Any] | dict[str, Any]) -> dict[str, Any]:
    name = "output"
    description: str | None = None

    if _is_pydantic_model_type(output_schema):
        model_type = output_schema
        schema = _pydantic_model_to_schema(model_type)
        name = getattr(model_type, "__name__", "output") or "output"
    elif isinstance(output_schema, dict):
        schema: dict[str, Any]
        if output_schema.get("type") == "json_schema":
            nested = output_schema.get("json_schema")
            if nested is not None:
                if not isinstance(nested, dict):
                    raise TypeError("response format json_schema payload must be a dictionary")
                schema = nested.get("schema")
                if not isinstance(schema, dict):
                    raise TypeError("response format json_schema.schema must be a dictionary")
                name = str(nested.get("name") or output_schema.get("name") or "output")
                nested_description = nested.get("description")
                root_description = output_schema.get("description")
                if isinstance(nested_description, str):
                    description = nested_description
                elif isinstance(root_description, str):
                    description = root_description
            else:
                schema = output_schema.get("schema")
                if not isinstance(schema, dict):
                    raise TypeError("response format schema must be a dictionary")
                name = str(output_schema.get("name") or "output")
                root_description = output_schema.get("description")
                if isinstance(root_description, str):
                    description = root_description
        else:
            schema = output_schema
    else:
        raise TypeError("output_schema must be a pydantic model type or a dictionary")

    strict_schema = _ensure_strict_json_schema(copy.deepcopy(schema))
    if strict_schema.get("type") != "object":
        raise ValueError("structured output schema root must be a JSON object")

    response_format: dict[str, Any] = {
        "type": "json_schema",
        "name": name,
        "schema": strict_schema,
        "strict": True,
    }
    if description:
        response_format["description"] = description
    return response_format


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect._empty:
        return {"type": "string"}

    if isinstance(annotation, str):
        normalized = annotation.strip().lower()
        if normalized in {"str", "string"}:
            return {"type": "string"}
        if normalized in {"int", "integer"}:
            return {"type": "integer"}
        if normalized in {"float", "number"}:
            return {"type": "number"}
        if normalized in {"bool", "boolean"}:
            return {"type": "boolean"}
        if "list" in normalized:
            return {"type": "array"}
        if "dict" in normalized:
            return {"type": "object"}
        return {"type": "string"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is None:
        if _is_pydantic_model_type(annotation):
            return _pydantic_model_to_schema(annotation)
        if annotation is str:
            return {"type": "string"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation in (dict, dict[str, Any]):
            return {"type": "object"}
        if annotation in (list, list[Any]):
            return {"type": "array"}
        return {"type": "string"}

    if str(origin) == "typing.Union" or origin is UnionType:
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1 and len(non_none) != len(args):
            schema = _python_type_to_schema(non_none[0])
            schema["nullable"] = True
            return schema
        return {"type": "string"}

    if origin in (list, tuple):
        item_annotation = args[0] if args else Any
        return {"type": "array", "items": _python_type_to_schema(item_annotation)}

    if origin in (dict,):
        return {"type": "object"}

    return {"type": "string"}


def callable_to_tool_schema(func: Any) -> ToolSchema:
    signature = inspect.signature(func)
    try:
        resolved_hints = get_type_hints(func)
    except Exception:
        resolved_hints = {}

    doc = inspect.getdoc(func) or ""
    description = doc.splitlines()[0] if doc else f"Tool `{getattr(func, '__name__', 'tool')}`"

    params = [
        param
        for param in signature.parameters.values()
        if param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
    ]
    if len(params) == 1:
        single = params[0]
        single_annotation = resolved_hints.get(single.name, single.annotation)
        if _is_pydantic_model_type(single_annotation):
            model_schema = _python_type_to_schema(single_annotation)
            if model_schema.get("type") == "object":
                return {
                    "type": "function",
                    "name": getattr(func, "__name__", "tool"),
                    "description": description,
                    "parameters": model_schema,
                }

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in params:
        name = param.name
        annotation = resolved_hints.get(name, param.annotation)
        properties[name] = _python_type_to_schema(annotation)
        if param.default is inspect._empty:
            required.append(name)

    return {
        "type": "function",
        "name": getattr(func, "__name__", "tool"),
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }



def _normalize_dict_tool(tool: dict[str, Any]) -> ToolSchema:
    # Pass through built-in/non-function tool dicts (e.g. {"type": "web_search"}).
    # A built-in has a non-"function" type string and lacks both a nested "function" key
    # and a top-level "name" key — function tools always have at least one of those.
    tool_type = tool.get("type")
    if tool_type is not None and tool_type != "function" and "function" not in tool and "name" not in tool:
        return tool  # type: ignore[return-value]

    if tool.get("type") == "function" and "function" in tool and isinstance(tool["function"], dict):
        fn = tool["function"]
        return {
            "type": "function",
            "name": fn.get("name", "tool"),
            "description": fn.get("description", f"Tool `{fn.get('name', 'tool')}`"),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        }

    if tool.get("type") == "function" and "name" in tool:
        return {
            "type": "function",
            "name": tool.get("name", "tool"),
            "description": tool.get("description", f"Tool `{tool.get('name', 'tool')}`"),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        }

    if "name" in tool:
        return {
            "type": "function",
            "name": tool.get("name", "tool"),
            "description": tool.get("description", f"Tool `{tool.get('name', 'tool')}`"),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        }

    raise ValueError("Unsupported tool dictionary format")


def normalize_tool_inputs(tools: list[ToolInput] | None) -> list[ToolSchema]:
    if not tools:
        return []

    normalized: list[ToolSchema] = []
    for tool in tools:
        if callable(tool):
            normalized.append(callable_to_tool_schema(tool))
            continue
        if isinstance(tool, dict):
            normalized.append(_normalize_dict_tool(tool))
            continue
        raise TypeError("Tool must be a callable or dict schema")
    return normalized


def to_responses_tools(
    tools: list[ToolSchema],
    *,
    strict_output: bool = False,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        # Pass through built-in/non-function tool dicts (e.g. {"type": "web_search"}).
        if isinstance(tool, dict) and tool.get("type") != "function":
            normalized.append(dict(tool))
            continue
        item: dict[str, Any] = {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", f"Tool `{tool['name']}`"),
            "parameters": dict(tool.get("parameters", {"type": "object", "properties": {}})),
        }
        if strict_output:
            item["parameters"] = _ensure_strict_json_schema(copy.deepcopy(item["parameters"]))
            item["strict"] = True
        normalized.append(item)
    return normalized


def normalize_tool_output(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        return {"output": output}
    try:
        json.dumps(output, ensure_ascii=True)
        return {"output": output}
    except TypeError:
        return {"output": str(output)}


def serialize_tool_output(output: Any) -> str:
    return json.dumps(normalize_tool_output(output), ensure_ascii=True)


def tool_results_to_response_items(tool_results: list[ToolResult] | None) -> list[dict[str, Any]]:
    if not tool_results:
        return []
    items: list[dict[str, Any]] = []
    for result in tool_results:
        call_id = result.tool_call_id.strip()
        if not call_id:
            raise SDKRequestError(
                status_code=None,
                provider_code="invalid_tool_call_id",
                user_message=(
                    "tool_results contains an empty tool_call_id; "
                    "call_id restoration may have failed for a streamed function_call_arguments event"
                ),
                retryable=False,
                raw_error={"tool_name": result.name},
            )
        items.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": serialize_tool_output(result.output),
            }
        )
    return items
