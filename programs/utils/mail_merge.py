"""Safe rendering and input validation for program mail merge."""

import json
import re
import uuid
from collections import Counter

from django.core.exceptions import ValidationError
from lxml import html as lxml_html

MAX_MERGE_ROWS = 500
MAX_CUSTOM_COLUMNS = 20
MAX_MERGE_VALUE_LENGTH = 1000

FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
MERGE_TAG_RE = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")
MERGE_CANDIDATE_RE = re.compile(r"{{\s*([^{}]*?)\s*}}")

BUILT_IN_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "program_name",
        "team_name",
        "crew_name",
        "project_name",
        "subteam_name",
        "attendance_total_hours",
        "attendance_average_hours_per_week",
        "attendance_sessions_count",
        "attendance_days_attended",
        "attendance_last_attended",
        "outreach_total_hours",
        "outreach_pending_hours",
        "outreach_shifts_attended",
        "outreach_shifts_signed_up",
        "outreach_events_championed",
        "outreach_shifts_unconfirmed",
    }
)


def _string_value(value):
    if value is None:
        return ""
    return str(value)


def substitute_text_fields(template, values):
    """Substitute merge fields in plain text without interpreting their values."""

    return MERGE_TAG_RE.sub(
        lambda match: _string_value(values.get(match.group(1), match.group(0))),
        template,
    )


def substitute_subject(template, values):
    """Substitute subject fields and normalize the result to one line."""

    rendered = substitute_text_fields(template, values)
    return re.sub(r"\s+", " ", rendered.replace("\r", " ").replace("\n", " ")).strip()


def _substitute_html_text(text, values, line_break_token):
    if not text:
        return text

    def replace(match):
        value = _string_value(values.get(match.group(1), match.group(0)))
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return value.replace("\n", line_break_token)

    return MERGE_TAG_RE.sub(replace, text)


def substitute_html_fields(template, values):
    """Substitute merge fields in HTML text nodes, escaping each inserted value.

    Attributes are deliberately left untouched; merge fields in links or other
    attributes are not supported.
    """

    fragment = lxml_html.fragment_fromstring(template or "", create_parent="div")
    line_break_token = f"gos-merge-break-{uuid.uuid4().hex}"
    for element in fragment.iter():
        element.text = _substitute_html_text(element.text, values, line_break_token)
        for child in element:
            child.tail = _substitute_html_text(child.tail, values, line_break_token)

    parts = [fragment.text or ""]
    for child in fragment:
        parts.append(lxml_html.tostring(child, encoding="unicode", with_tail=False))
        parts.append(child.tail or "")
    return "".join(parts).replace(line_break_token, "<br>")


def analyze_merge_fields(template, available_fields):
    """Report unknown fields and fields split across HTML text nodes."""

    fragment = lxml_html.fragment_fromstring(template or "", create_parent="div")
    visible_text = fragment.text_content()
    visible_candidates = [
        match.group(1).strip() for match in MERGE_CANDIDATE_RE.finditer(visible_text)
    ]
    attribute_candidates = [
        match.group(1).strip()
        for element in fragment.iter()
        for attribute in element.attrib.values()
        for match in MERGE_CANDIDATE_RE.finditer(attribute)
    ]
    raw_candidates = [
        match.group(1).strip() for match in MERGE_CANDIDATE_RE.finditer(template or "")
    ]
    available_fields = set(available_fields)

    unknown = list(
        dict.fromkeys(
            [field for field in visible_candidates if field not in available_fields]
            + attribute_candidates
        )
    )
    visible_counts = Counter(visible_candidates)
    raw_counts = Counter(raw_candidates)
    split = list(
        dict.fromkeys(
            field
            for field in visible_candidates
            if field in available_fields and visible_counts[field] > raw_counts[field]
        )
    )
    return {"unknown": unknown, "split": split}


def parse_merge_data(payload):
    """Validate the browser-submitted grid and return normalized columns/rows."""

    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except (json.JSONDecodeError, TypeError):
        raise ValidationError("The mail merge data is not valid JSON.")

    if not isinstance(data, dict):
        raise ValidationError("The mail merge data must contain columns and rows.")
    columns = data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValidationError("The mail merge data must contain columns and rows.")
    if len(columns) > MAX_CUSTOM_COLUMNS:
        raise ValidationError(f"Use no more than {MAX_CUSTOM_COLUMNS} custom columns.")
    if len(rows) > MAX_MERGE_ROWS:
        raise ValidationError(f"Use no more than {MAX_MERGE_ROWS} students at a time.")

    normalized_columns = []
    for column in columns:
        if (
            not isinstance(column, str)
            or not FIELD_NAME_RE.fullmatch(column)
            or column in BUILT_IN_FIELDS
            or column in normalized_columns
        ):
            raise ValidationError("One or more custom column names are invalid.")
        normalized_columns.append(column)

    normalized_rows = []
    seen_student_ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("Each mail merge row must be an object.")
        try:
            raw_student_id = row.get("student_id")
            if isinstance(raw_student_id, bool):
                raise ValueError
            if isinstance(raw_student_id, int):
                student_id = raw_student_id
            elif (
                isinstance(raw_student_id, str)
                and raw_student_id.isascii()
                and raw_student_id.isdecimal()
            ):
                student_id = int(raw_student_id)
            else:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError("Each row must identify a valid student.")
        if student_id <= 0 or student_id in seen_student_ids:
            raise ValidationError("Student rows must have unique valid IDs.")
        seen_student_ids.add(student_id)

        if set(row) - {"student_id", *normalized_columns}:
            raise ValidationError("The mail merge data contains unexpected fields.")

        normalized_row: dict[str, int | str] = {"student_id": student_id}
        for column in normalized_columns:
            value = row.get(column, "")
            if value is None:
                value = ""
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValidationError("Custom column values must be plain text.")
            value = str(value)
            if len(value) > MAX_MERGE_VALUE_LENGTH:
                raise ValidationError(
                    f"Values must be no more than {MAX_MERGE_VALUE_LENGTH} characters."
                )
            normalized_row[column] = value
        normalized_rows.append(normalized_row)

    return {"columns": normalized_columns, "rows": normalized_rows}
