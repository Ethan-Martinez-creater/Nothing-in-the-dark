"""M10: lenient structured-output parsing (fences, prose-wrapped JSON)."""

from __future__ import annotations

from app.application.graph_worker import GraphWorker
from app.harness.structured_output import repair_json_content


def test_strict_json_object() -> None:
    assert repair_json_content('{"ok": true, "n": 1}') == {"ok": True, "n": 1}


def test_json_fenced_block() -> None:
    content = '根据分析，结论如下：\n```json\n{"ok": true, "n": 1}\n```\n以上。'
    assert repair_json_content(content) == {"ok": True, "n": 1}


def test_fence_without_language_marker() -> None:
    content = '```\n{"ok": true}\n```'
    assert repair_json_content(content) == {"ok": True}


def test_prose_wrapped_json() -> None:
    content = "结果如下：{'ok': true}"  # single quotes are invalid JSON
    assert repair_json_content(content) is None
    content = '结果如下：{"ok": true}，以上就是全部。'
    assert repair_json_content(content) == {"ok": True}


def test_nested_braces_in_values() -> None:
    content = '前置说明 {"a": {"b": ["x", "y"]}, "c": 1} 尾部文字'
    assert repair_json_content(content) == {"a": {"b": ["x", "y"]}, "c": 1}


def test_braces_inside_strings_are_not_splitters() -> None:
    content = '{"text": "包含 {花括号} 的字符串", "n": 2} 尾部'
    assert repair_json_content(content) == {"text": "包含 {花括号} 的字符串", "n": 2}


def test_multiple_objects_takes_first_complete() -> None:
    content = '{"first": 1} 然后 {"second": 2}'
    assert repair_json_content(content) == {"first": 1}


def test_unparseable_returns_none() -> None:
    assert repair_json_content("") is None
    assert repair_json_content("   ") is None
    assert repair_json_content("模型没有返回任何结构化内容") is None
    assert repair_json_content("[1, 2, 3]") is None  # not an object
    assert repair_json_content("```json\n{broken\n```") is None


def test_graph_worker_integration_repairs_fenced_output() -> None:
    content = "汇总：\n```json\n{\"summary\": \"完成\", \"n\": 3}\n```"
    parsed = GraphWorker._parse_json_content(content)
    assert parsed["summary"] == "完成"
    assert parsed["n"] == 3
    assert "raw_content" not in parsed


def test_graph_worker_integration_falls_back_verbatim() -> None:
    content = "模型未按格式返回"
    parsed = GraphWorker._parse_json_content(content)
    assert parsed == {"raw_content": content, "parsed": False}


def test_graph_worker_integration_repairs_plain_text_wrapped() -> None:
    content = '分析结论：{"opinion": "支持", "count": 5}，其余略。'
    parsed = GraphWorker._parse_json_content(content)
    assert parsed == {"opinion": "支持", "count": 5}
