#!/usr/bin/env python3
"""Build Django-compatible PO and MO files from the reviewed JSON catalog."""

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Lithuanian is the upstream translation. The English catalogue is a family
# override that rewords "points" as Australian dollars (AUD).
PLURAL_FORMS = {
    "lt": (
        "nplurals=4; plural=(n%10==1 && (n%100>19 || n%100<11) ? 0 : "
        "(n%10>=2 && n%10<=9) && (n%100>19 || n%100<11) ? 1 : n%1!=0 ? 2: 3);"
    ),
    "en": "nplurals=2; plural=(n != 1);",
}


def quote(value):
    return json.dumps(value, ensure_ascii=False)


def write_po(messages, language, output_dir):
    header = (
        "Project-Id-Version: KinKudos\n"
        f"Language: {language}\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        f"Plural-Forms: {PLURAL_FORMS[language]}\n"
    )
    lines = ['msgid ""', f"msgstr {quote(header)}", ""]
    for msgid, msgstr in sorted(messages.items()):
        lines.extend((f"msgid {quote(msgid)}", f"msgstr {quote(msgstr)}", ""))
    (output_dir / "django.po").write_text("\n".join(lines), encoding="utf-8")
    return header


def write_mo(messages, header, output_dir):
    catalog = {"": header, **messages}
    ids = sorted(catalog)
    encoded_ids = [key.encode("utf-8") for key in ids]
    encoded_values = [catalog[key].encode("utf-8") for key in ids]
    count = len(ids)
    key_table_offset = 28
    value_table_offset = key_table_offset + count * 8
    strings_offset = value_table_offset + count * 8

    key_blob = b""
    key_entries = []
    for value in encoded_ids:
        key_entries.append((len(value), strings_offset + len(key_blob)))
        key_blob += value + b"\0"

    value_blob_offset = strings_offset + len(key_blob)
    value_blob = b""
    value_entries = []
    for value in encoded_values:
        value_entries.append((len(value), value_blob_offset + len(value_blob)))
        value_blob += value + b"\0"

    output = struct.pack("<7I", 0x950412DE, 0, count, key_table_offset, value_table_offset, 0, 0)
    output += b"".join(struct.pack("<2I", *entry) for entry in key_entries)
    output += b"".join(struct.pack("<2I", *entry) for entry in value_entries)
    output += key_blob + value_blob
    (output_dir / "django.mo").write_bytes(output)


def compile_language(language):
    catalog = ROOT / "locale" / language / "messages.json"
    output_dir = ROOT / "locale" / language / "LC_MESSAGES"
    messages = json.loads(catalog.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    header = write_po(messages, language, output_dir)
    write_mo(messages, header, output_dir)
    print(f"Compiled {len(messages)} {language} translations.")


def main():
    for language in PLURAL_FORMS:
        compile_language(language)


if __name__ == "__main__":
    main()
