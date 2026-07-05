#!/usr/bin/env python3
"""Telemetry config code generator and validator.

Usage:
  python telemetry_codegen.py validate <config.json>
  python telemetry_codegen.py generate <config.json> [--out-dir <dir>]

The script validates config JSON against a lightweight rule set and emits
telemetry_config.h / telemetry_config.c with reusable sensor/command metadata.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

TYPE_MAP = {
    "float32": ("TELEMETRY_TYPE_FLOAT32", 0x01),
    "float64": ("TELEMETRY_TYPE_FLOAT64", 0x02),
    "int8":   ("TELEMETRY_TYPE_INT8", 0x03),
    "uint8":  ("TELEMETRY_TYPE_UINT8", 0x04),
    "int16":  ("TELEMETRY_TYPE_INT16", 0x05),
    "uint16": ("TELEMETRY_TYPE_UINT16", 0x06),
    "int32":  ("TELEMETRY_TYPE_INT32", 0x07),
    "uint32": ("TELEMETRY_TYPE_UINT32", 0x08),
    "string": ("TELEMETRY_TYPE_STRING", 0x09),
    "bytes":  ("TELEMETRY_TYPE_BYTES", 0x0A),
    "struct": ("TELEMETRY_TYPE_STRUCT", 0x10),
}

ALLOWED_ALIGNMENT = {"packed", "arm", "default"}
ALLOWED_ENDIANNESS = {"little", "big"}
BASE_FIELDS = {"id", "name", "description", "data_type", "units", "scale", "offset", "display_format", "count"}
COMMAND_FIELDS = BASE_FIELDS.union({"ack_required", "struct_fields", "tx_buffer"})
SENSOR_FIELDS = BASE_FIELDS.union({"default_value", "struct_fields"})
STRUCT_FIELD_FIELDS = {"name", "data_type", "count", "units", "scale", "offset", "display_format"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_identifier(value):
    name = re.sub(r"[^0-9A-Za-z]+", "_", value.strip())
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    if not name:
        return "unnamed"
    if name[0].isdigit():
        name = "_" + name
    return name


def escape_c_string(value):
    return value.replace("\\", "\\\\").replace("\"", "\\\"")


def validate_item(item, allowed_fields, is_struct_owner=False):
    errors = []
    if not isinstance(item, dict):
        return ["item must be an object"]
    unknown = set(item) - allowed_fields
    if unknown:
        errors.append(f"unknown keys: {sorted(unknown)}")

    if "id" not in item:
        errors.append("missing required key 'id'")
    elif not isinstance(item["id"], int) or item["id"] < 0 or item["id"] > 255:
        errors.append("'id' must be an integer between 0 and 255")

    if "name" not in item:
        errors.append("missing required key 'name'")
    elif not isinstance(item["name"], str) or not item["name"].strip():
        errors.append("'name' must be a non-empty string")

    if "data_type" not in item:
        errors.append("missing required key 'data_type'")
    elif item["data_type"] not in TYPE_MAP:
        errors.append(f"invalid data_type '{item.get('data_type')}'")

    for key in ["units", "display_format", "tx_buffer"]:
        if key in item and not isinstance(item[key], str):
            errors.append(f"'{key}' must be a string")

    if "count" in item and (not isinstance(item["count"], int) or item["count"] < 1):
        errors.append("'count' must be an integer >= 1")

    for key in ["scale", "offset"]:
        if key in item and not isinstance(item[key], (int, float)):
            errors.append(f"'{key}' must be a number")

    if item.get("data_type") == "struct":
        if "struct_fields" not in item:
            errors.append("struct data_type requires 'struct_fields'")
        elif not isinstance(item["struct_fields"], list) or not item["struct_fields"]:
            errors.append("'struct_fields' must be a non-empty array")
        else:
            seen_names = set()
            for idx, field in enumerate(item["struct_fields"]):
                if not isinstance(field, dict):
                    errors.append(f"struct_fields[{idx}] must be an object")
                    continue
                unknown_field = set(field) - STRUCT_FIELD_FIELDS
                if unknown_field:
                    errors.append(f"struct_fields[{idx}] unknown keys: {sorted(unknown_field)}")
                if "name" not in field or not isinstance(field["name"], str) or not field["name"].strip():
                    errors.append(f"struct_fields[{idx}] missing or invalid 'name'")
                else:
                    if field["name"] in seen_names:
                        errors.append(f"struct_fields[{idx}] duplicate field name '{field["name"]}'")
                    seen_names.add(field["name"])
                if "data_type" not in field or field["data_type"] not in TYPE_MAP or field["data_type"] == "struct":
                    errors.append(f"struct_fields[{idx}] invalid data_type '{field.get('data_type')}'")
                if "count" in field and (not isinstance(field["count"], int) or field["count"] < 1):
                    errors.append(f"struct_fields[{idx}] count must be an integer >= 1")
                if "units" in field and not isinstance(field["units"], str):
                    errors.append(f"struct_fields[{idx}] units must be a string")
                for numeric in ["scale", "offset"]:
                    if numeric in field and not isinstance(field[numeric], (int, float)):
                        errors.append(f"struct_fields[{idx}] {numeric} must be a number")
    else:
        if "struct_fields" in item:
            errors.append("'struct_fields' is only allowed when data_type is 'struct'")

    if is_struct_owner and "default_value" in item and item["data_type"] == "struct":
        errors.append("'default_value' is not supported for struct fields")

    return errors


def validate_config(config):
    errors = []
    if not isinstance(config, dict):
        return ["root config must be an object"]
    if config.get("version") is None or not isinstance(config["version"], str):
        errors.append("top-level 'version' string required")
    if config.get("protocol_version") is None or not isinstance(config["protocol_version"], str):
        errors.append("top-level 'protocol_version' string required")
    if config.get("endianness") not in ALLOWED_ENDIANNESS:
        errors.append("'endianness' must be one of: little, big")
    if config.get("alignment") not in ALLOWED_ALIGNMENT:
        errors.append("'alignment' must be one of: packed, arm, default")

    if "sensors" not in config:
        errors.append("missing required top-level 'sensors' array")
    elif not isinstance(config["sensors"], list):
        errors.append("'sensors' must be an array")

    if "commands" in config and not isinstance(config["commands"], list):
        errors.append("'commands' must be an array")

    if errors:
        return errors

    for section in ("sensors", "commands"):
        if section not in config:
            continue
        item_ids = set()
        if not isinstance(config[section], list):
            continue
        for idx, item in enumerate(config[section]):
            allowed = SENSOR_FIELDS if section == "sensors" else COMMAND_FIELDS
            item_errors = validate_item(item, allowed, is_struct_owner=(section == "sensors"))
            for err in item_errors:
                errors.append(f"{section}[{idx}]: {err}")
            if isinstance(item, dict) and "id" in item:
                if item["id"] in item_ids:
                    errors.append(f"{section}[{idx}]: duplicate id {item['id']}")
                item_ids.add(item["id"])

    sensors = config.get("sensors", [])
    if sensors:
        sensor_ids = sorted(item["id"] for item in sensors if isinstance(item.get("id"), int))
        if sensor_ids != list(range(1, sensor_ids[-1] + 1)):
            errors.append("sensor ids must be contiguous from 1 to max id without gaps")
    return errors


def c_float_literal(value):
    return f"{float(value):.6f}f"


def telemtry_type_macro(data_type):
    mapping = {
        "float32": "TELEMTRY_TYPE_FLOAT",
        "float64": "TELEMTRY_TYPE_DOUBLE",
        "int8": "TELEMTRY_TYPE_INT8",
        "uint8": "TELEMTRY_TYPE_INT8",
        "int16": "TELEMTRY_TYPE_INT16",
        "uint16": "TELEMTRY_TYPE_INT16",
        "int32": "TELEMTRY_TYPE_INT32",
        "uint32": "TELEMTRY_TYPE_INT32",
        "string": "TELEMTRY_TYPE_STRING",
        "bytes": "TELEMTRY_TYPE_BYTES",
        "struct": "TELEMTRY_TYPE_STRUCT",
    }
    return mapping.get(data_type, "TELEMTRY_TYPE_INVALID")


def c_type_name(data_type):
    if data_type == "float32":
        return "float"
    if data_type == "float64":
        return "double"
    if data_type == "int8":
        return "int8_t"
    if data_type == "uint8":
        return "uint8_t"
    if data_type == "int16":
        return "int16_t"
    if data_type == "uint16":
        return "uint16_t"
    if data_type == "int32":
        return "int32_t"
    if data_type == "uint32":
        return "uint32_t"
    if data_type == "string":
        return "char"
    if data_type == "bytes":
        return "uint8_t"
    return "uint8_t"


def render_info_header(config, header_name="info_config.h"):
    ids = []
    used_names = set()
    for item in config.get("sensors", []):
        identifier = normalize_identifier(item["name"]).upper()
        if identifier in used_names:
            identifier = f"{identifier}_SENSOR"
        used_names.add(identifier)
        ids.append((identifier, item["id"]))
    ids.sort(key=lambda x: x[1])
    enum_lines = []
    if ids:
        # Generate ID lines if sensors exist
        enum_lines = [f"    TELEMTRY_ID_{name} = 0x{value:02X}," for name, value in ids]
        enum_lines.append("    /* Add only above this line */")
        enum_lines.append("    TOTAL_TELEMTRY_ID,")
    else:
        # Empty case
        enum_lines.append("    /* Add only above this line */")
        enum_lines.append("    TOTAL_TELEMTRY_ID = 1,")
    struct_defs = []
    for section in ("sensors", "commands"):
        for item in config.get(section, []):
            if item.get("data_type") == "struct":
                struct_name = f"{normalize_identifier(item['name'])}_t"
                struct_defs.append(f"typedef struct {{")
                for field in item["struct_fields"]:
                    ctype = c_type_name(field["data_type"])
                    count = field.get("count", 1)
                    field_name = normalize_identifier(field["name"])
                    if field["data_type"] == "string":
                        struct_defs.append(f"    {ctype} {field_name}[{count}];")
                    elif field["data_type"] == "bytes":
                        struct_defs.append(f"    {ctype} {field_name}[{count}];")
                    else:
                        if count > 1:
                            struct_defs.append(f"    {ctype} {field_name}[{count}];")
                        else:
                            struct_defs.append(f"    {ctype} {field_name};")
                struct_defs.append(f"}} {struct_name};\n")

    return f"""/* Auto-generated telemetry config header. */
#ifndef INFO_CONFIG_H
#define INFO_CONFIG_H

#include <stdint.h>
#include <stdbool.h>
#include "telemtrycustom.h"

/* Generated telemetry IDs for sensors and commands. */
enum
{{
{os.linesep.join(enum_lines)}
}};

{os.linesep.join(struct_defs)}

extern tel_cmd_t *sensor_array[TOTAL_TELEMTRY_ID-1];
extern tel_information_t *buffers_array[TOTAL_TELEMTRY_ID-1];

#endif /* INFO_CONFIG_H */
"""


def render_info_source(config, header_name="info_config.h"):
    parts = [f"#include \"../Inc/{header_name}\"", "#include \"telemtrycustom.h\"", "#include <stdint.h>\n"]
    sensors = config.get("sensors", [])

    def type_code(data_type, count=1):
        if data_type == "float32":
            return "f"
        if data_type == "float64":
            return "d"
        if data_type == "int8":
            return "b"
        if data_type == "uint8":
            return "B"
        if data_type == "int16":
            return "h"
        if data_type == "uint16":
            return "H"
        if data_type == "int32":
            return "i"
        if data_type == "uint32":
            return "I"
        if data_type == "string":
            return f"{count}S"
        if data_type == "bytes":
            return f"{count}s"
        return "B"

    def make_struct_format(item):
        if item["data_type"] != "struct":
            return None
        codes = []
        for field in item["struct_fields"]:
            count = field.get("count", 1)
            codes.append(type_code(field["data_type"], count))
        return "".join(codes)

    def make_payload_name(item):
        return f"{normalize_identifier(item['name'])}_data"

    def make_item_name(item, prefix):
        return f"{prefix}_{normalize_identifier(item['name'])}"

    def render_struct_payload(item, name):
        struct_name = f"{normalize_identifier(item['name'])}_t"
        initializers = []
        for field in item["struct_fields"]:
            field_type = field["data_type"]
            if field_type == "string":
                default_value = field.get("default_value", "")
                escaped = escape_c_string(str(default_value))
                initializers.append(f'"{escaped}"')
            elif field_type == "bytes":
                initializers.append("{0}")
            else:
                initializers.append("0")
        initializer_list = ", ".join(initializers)
        return f"{struct_name} {name} = {{ {initializer_list} }};\n"

    for item in sensors:
        name = make_payload_name(item)
        if item["data_type"] == "struct":
            parts.append(render_struct_payload(item, name))
        elif item["data_type"] == "string":
            default_value = item.get("default_value", "")
            escaped = escape_c_string(str(default_value))
            length = max(1, len(default_value) + 1)
            parts.append(f"static char {name}[{length}] = \"{escaped}\";\n")
        elif item["data_type"] == "bytes":
            count = item.get("count", 1)
            parts.append(f"static uint8_t {name}[{count}] = {{0}};\n")
        else:
            ctype = c_type_name(item["data_type"])
            default_value = item.get("default_value", 0)
            if isinstance(default_value, str):
                default_value = 0
            parts.append(f"static {ctype} {name} = {default_value};\n")

    for item in sensors:
        item_name = make_item_name(item, "sensor")
        payload = make_payload_name(item)
        info_type = telemtry_type_macro(item["data_type"])
        item_id = normalize_identifier(item["name"]).upper()
        if item["data_type"] == "string":
            length = f"sizeof({payload}) - 1"
            buffer = payload
        elif item["data_type"] == "bytes":
            length = f"sizeof({payload})"
            buffer = payload
        elif item["data_type"] == "struct":
            length = f"sizeof({payload})"
            buffer = f"&{payload}"
        else:
            length = f"sizeof({payload})"
            buffer = f"&{payload}"
        parts.append(
            f"tel_information_t {item_name} = {{\n"
            f"    .data_synch = TELEMTRY_ID_SYNCH,\n"
            f"    .information_type = {info_type},\n"
            f"    .information_id = TELEMTRY_ID_{item_id},\n"
            f"    .information_len = {length},\n"
            f"    .information_buffer = {buffer}\n"
            f"}};\n"
        )

    for item in sensors:
        item_name = make_item_name(item, "cmd")
        item_id = normalize_identifier(item["name"]).upper()
        if item["data_type"] == "struct":
            struct_fmt = make_struct_format(item)
            handshake = f"{item['name']}:@{struct_fmt}"
        else:
            handshake = item["name"]
        escaped = escape_c_string(handshake)
        parts.append(
            f"tel_cmd_t {item_name} = {{\n"
            f"    .cmd_synch = TELEMTRY_ID_CMD,\n"
            f"    .cmd_id = TELEMTRY_ID_{item_id},\n"
            f"    .tx_buffer = (uint8_t *)\"{escaped}\",\n"
            f"    .crc = 0xFFFF\n"
            f"}};\n"
        )

    parts.append("tel_cmd_t *sensor_array[TOTAL_TELEMTRY_ID-1] = {\n")
    for item in sorted(sensors, key=lambda item: item["id"]):
        item_name = make_item_name(item, "cmd")
        parts.append(f"    &{item_name},")
    parts.append("};\n")

    parts.append("tel_information_t *buffers_array[TOTAL_TELEMTRY_ID-1] = {\n")
    for item in sorted(sensors, key=lambda item: item["id"]):
        item_name = make_item_name(item, "sensor")
        parts.append(f"    &{item_name},")
    parts.append("};\n")

    return "\n".join(parts)

    return f"""/* Auto-generated telemetry config header. */
#ifndef TELEMETRY_CONFIG_H
#define TELEMETRY_CONFIG_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern \"C\" {{
#endif

typedef enum
{{
{os.linesep.join(type_entries)}
}} telemetry_type_t;

typedef struct
{{
    const char *name;
    telemetry_type_t type;
    uint8_t count;
    const char *units;
    float scale;
    float offset;
    const char *display_format;
}} telemetry_field_config_t;

typedef struct
{{
    uint8_t id;
    const char *name;
    telemetry_type_t type;
    const telemetry_field_config_t *fields;
    uint8_t field_count;
    const char *units;
    float scale;
    float offset;
    const char *display_format;
    bool ack_required;
}} telemetry_item_config_t;

extern const telemetry_item_config_t telemetry_sensors[];
extern const size_t telemetry_sensor_count;
extern const telemetry_item_config_t telemetry_commands[];
extern const size_t telemetry_command_count;

const telemetry_item_config_t *telemetry_config_find_sensor(uint8_t id);
const telemetry_item_config_t *telemetry_config_find_command(uint8_t id);

#ifdef __cplusplus
}}
#endif

#endif /* TELEMETRY_CONFIG_H */
"""


def item_has_fields(item):
    return item.get("data_type") == "struct"


def render_field_value(field):
    name = escape_c_string(field["name"])
    type_name = TYPE_MAP[field["data_type"]][0]
    count = field.get("count", 1)
    units = f"\"{escape_c_string(field['units'])}\"" if field.get("units") else "NULL"
    scale = c_float_literal(field.get("scale", 1.0))
    offset = c_float_literal(field.get("offset", 0.0))
    display_format = f"\"{escape_c_string(field['display_format'])}\"" if field.get("display_format") else "NULL"
    return f"    {{ \"{name}\", {type_name}, {count}, {units}, {scale}, {offset}, {display_format} }}"


def render_item_value(item, field_array_name):
    type_name = TYPE_MAP[item["data_type"]][0]
    units = f"\"{escape_c_string(item['units'])}\"" if item.get("units") else "NULL"
    scale = c_float_literal(item.get("scale", 1.0))
    offset = c_float_literal(item.get("offset", 0.0))
    display_format = f"\"{escape_c_string(item['display_format'])}\"" if item.get("display_format") else "NULL"
    ack_required = "true" if item.get("ack_required", False) else "false"
    if item_has_fields(item):
        field_count = len(item["struct_fields"])
        fields_ptr = field_array_name
    else:
        field_count = 0
        fields_ptr = "NULL"

    return (
        f"    {{ {item['id']}, \"{escape_c_string(item['name'])}\", {type_name}, {fields_ptr}, "
        f"{field_count}, {units}, {scale}, {offset}, {display_format}, {ack_required} }}"
    )


def render_source(config, header_name="telemetry_config.h"):
    parts = [f"#include \"{header_name}\"\n"]
    sensors = config.get("sensors", [])
    commands = config.get("commands", [])
    sensor_commands = [item for item in sensors if item.get("handshake_format") is not None]

    def render_field_array(owner, item):
        if not item_has_fields(item):
            return ""
        array_name = f"{normalize_identifier(owner)}_{normalize_identifier(item['name'])}_fields"
        lines = [f"static const telemetry_field_config_t {array_name}[] = {{"]
        for field in item["struct_fields"]:
            lines.append(render_field_value(field))
        lines.append("};\n")
        return "\n".join(lines)

    for item in sensors:
        if item_has_fields(item):
            parts.append(render_field_array("sensor", item))
    for item in commands:
        if item_has_fields(item):
            parts.append(render_field_array("command", item))

    parts.append("const telemetry_item_config_t telemetry_sensors[] = {\n")
    for item in sensors:
        array_name = f"sensor_{normalize_identifier(item['name'])}_fields" if item_has_fields(item) else "NULL"
        parts.append(render_item_value(item, array_name) + ",")
    parts.append("};\n")
    parts.append("const size_t telemetry_sensor_count = sizeof(telemetry_sensors) / sizeof(telemetry_sensors[0]);\n")

    parts.append("const telemetry_item_config_t telemetry_commands[] = {\n")
    for item in commands:
        array_name = f"command_{normalize_identifier(item['name'])}_fields" if item_has_fields(item) else "NULL"
        parts.append(render_item_value(item, array_name) + ",")
    parts.append("};\n")
    parts.append("const size_t telemetry_command_count = sizeof(telemetry_commands) / sizeof(telemetry_commands[0]);\n")

    parts.append("const telemetry_item_config_t *telemetry_config_find_sensor(uint8_t id) {\n")
    parts.append("    for (size_t i = 0; i < telemetry_sensor_count; ++i) {\n")
    parts.append("        if (telemetry_sensors[i].id == id) return &telemetry_sensors[i];\n")
    parts.append("    }\n    return NULL;\n}\n")

    parts.append("const telemetry_item_config_t *telemetry_config_find_command(uint8_t id) {\n")
    parts.append("    for (size_t i = 0; i < telemetry_command_count; ++i) {\n")
    parts.append("        if (telemetry_commands[i].id == id) return &telemetry_commands[i];\n")
    parts.append("    }\n    return NULL;\n}\n")

    return "\n".join(parts)


def write_file(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def generate_files(config, out_dir, header_name="info_config.h", source_name="info_config.c"):
    os.makedirs(out_dir, exist_ok=True)
    header_path = os.path.join(out_dir, header_name)
    source_path = os.path.join(out_dir, source_name)
    write_file(header_path, render_info_header(config, header_name))
    write_file(source_path, render_info_source(config, header_name))
    return header_path, source_path


def parse_args():
    parser = argparse.ArgumentParser(description="Telemetry config generator and validator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a telemetry config JSON file")
    validate.add_argument("config", help="Path to telemetry config JSON")

    generate = subparsers.add_parser("generate", help="Generate C header and source from telemetry config")
    generate.add_argument("config", help="Path to telemetry config JSON")
    generate.add_argument("--out-dir", default="generatedconfigs", help="Output directory for generated files")
    generate.add_argument("--header-name", default="info_config.h", help="Generated header file name")
    generate.add_argument("--source-name", default="info_config.c", help="Generated source file name")

    return parser.parse_args()


def main():
    args = parse_args()
    try:
        config = load_json(args.config)
    except Exception as exc:
        print(f"ERROR: Unable to read '{args.config}': {exc}")
        return 2

    errors = validate_config(config)
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if args.command == "validate":
        print(f"Validation succeeded: {args.config}")
        return 0

    header_path, source_path = generate_files(config, args.out_dir, args.header_name, args.source_name)
    print(f"Generated: {header_path}")
    print(f"Generated: {source_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
