#!/usr/bin/env python3
"""
Jurism to Zotero RDF Extractor
Release 1.2
Andrew Symons 14-Aug-2026 
Fixes:
  - Fixes "Storing invalid field 'title' / 'extra' for type annotation" Zotero import crash.
  - Strictly sanitizes bib:Memo and z:Attachment nodes so non-item nodes never carry illegal extra/title tags.
  - Multi-language parser: Extracts primary language, creates Bilingual/Multilingual Zotero notes.
  - Creator fix: Missing surnames convert to '(unknown)' while preserving given names.
  - Report formatting: Clear [ERROR x] and [WARNING x] tags, en-GB spellings, concise messages.
  - Sanitizes unescaped line breaks, control characters, and single-field creators.
"""

import argparse
import html
import os
import re
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET

LANGUAGE_MAP = {
    'english': ('en', 'English'), 'en-us': ('en-US', 'English'), 'en-gb': ('en-GB', 'English'), 'eng': ('en', 'English'), 'en': ('en', 'English'),
    'french': ('fr', 'French'), 'français': ('fr', 'French'), 'fre': ('fr', 'French'), 'franc': ('fr', 'French'), 'fr': ('fr', 'French'),
    'german': ('de', 'German'), 'deutsch': ('de', 'German'), 'ger': ('de', 'German'), 'deu': ('de', 'German'), 'germ': ('de', 'German'), 'deut': ('de', 'German'), 'de': ('de', 'German'),
    'spanish': ('es', 'Spanish'), 'español': ('es', 'Spanish'), 'spa': ('es', 'Spanish'), 'esp': ('es', 'Spanish'), 'es': ('es', 'Spanish'),
    'portuguese': ('pt', 'Portuguese'), 'português': ('pt', 'Portuguese'), 'pt-pt': ('pt-PT', 'Portuguese'), 'pt-br': ('pt-BR', 'Portuguese'), 'por': ('pt', 'Portuguese'), 'pt': ('pt', 'Portuguese'),
    'italian': ('it', 'Italian'), 'italiano': ('it', 'Italian'), 'ita': ('it', 'Italian'), 'it': ('it', 'Italian'),
    'dutch': ('nl', 'Dutch'), 'nederlands': ('nl', 'Dutch'), 'dut': ('nl', 'Dutch'), 'nld': ('nl', 'Dutch'), 'nl': ('nl', 'Dutch'),
    'russian': ('ru', 'Russian'), 'rus': ('ru', 'Russian'), 'ru': ('ru', 'Russian'),
    'chinese': ('zh', 'Chinese'), 'zho': ('zh', 'Chinese'), 'chi': ('zh', 'Chinese'), 'zh': ('zh', 'Chinese'),
    'japanese': ('ja', 'Japanese'), 'jpn': ('ja', 'Japanese'), 'ja': ('ja', 'Japanese'),
    'latin': ('la', 'Latin'), 'lat': ('la', 'Latin'), 'la': ('la', 'Latin'),
    'greek': ('el', 'Greek'), 'ell': ('el', 'Greek'), 'gre': ('el', 'Greek'), 'el': ('el', 'Greek')
}

ISO_LANG_REGEX = re.compile(r'^[a-z]{2}(-[A-Z]{2})?$', re.IGNORECASE)

XML_ILLEGAL_CHARS_REGEX = re.compile(
    r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F\uD800-\uDFFF\uFFFE\uFFFF\u200B-\u200D\uFEFF]'
)

def clean_xml(val):
    if not val:
        return ""
    s = XML_ILLEGAL_CHARS_REGEX.sub('', str(val))
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    return html.escape(s, quote=False)

def clean_attr(val):
    if not val:
        return ""
    s = XML_ILLEGAL_CHARS_REGEX.sub('', str(val))
    s = s.replace('\r', ' ').replace('\n', ' ')
    return html.escape(s, quote=True)


def parse_languages(raw_lang_str):
    if not raw_lang_str:
        return "", []

    tokens = re.split(r'[/;,&\s]+', raw_lang_str.strip())
    tokens = [t.strip() for t in tokens if t.strip() and t.lower() != 'and']

    parsed_langs = []
    seen_codes = set()

    for tok in tokens:
        clean = tok.lower()
        iso_code = ""
        english_name = ""

        if ISO_LANG_REGEX.match(clean):
            iso_code = clean.lower()
            english_name = clean.upper()
            for k, v in LANGUAGE_MAP.items():
                if v[0] == iso_code:
                    english_name = v[1]
                    break
        elif clean in LANGUAGE_MAP:
            iso_code, english_name = LANGUAGE_MAP[clean]
        elif len(clean) >= 3 and clean[:3] in LANGUAGE_MAP:
            iso_code, english_name = LANGUAGE_MAP[clean[:3]]

        if iso_code and iso_code not in seen_codes:
            seen_codes.add(iso_code)
            parsed_langs.append((iso_code, english_name))

    primary_iso = parsed_langs[0][0] if parsed_langs else ""
    lang_names = [l[1] for l in parsed_langs]
    return primary_iso, lang_names


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip()


def parse_arguments():
    parser = argparse.ArgumentParser(description="Extract Jurism items into a Zotero-compliant RDF payload.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collection", type=str, metavar="NAME", help="Name of collection to extract.")
    group.add_argument("--all", action="store_true", help="Extract all items in the library.")
    parser.add_argument("--report-only", action="store_true", help="Perform database analysis and write anomaly report only.")
    return parser.parse_args()


def run_extractor():
    args = parse_arguments()

    db_path = os.path.abspath("jurism.sqlite")
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at path: {db_path}", file=sys.stderr)
        sys.exit(1)

    uri_path = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri_path, uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT itemTypeID, typeName FROM itemTypes")
        item_types = {row[0]: row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError as e:
        print(f"Error accessing database: {e}", file=sys.stderr)
        sys.exit(1)

    base_dir = os.path.dirname(db_path)
    storage_base = os.path.join(base_dir, "storage")

    extract_dir_name = sanitize_filename(args.collection) if args.collection else "My Library"
    extract_dir = os.path.abspath(extract_dir_name)
    files_dir = os.path.join(extract_dir, "files")

    if not args.report_only:
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        os.makedirs(files_dir, exist_ok=True)
    else:
        os.makedirs(extract_dir, exist_ok=True)

    rdf_tag_map = {
        "journalArticle": "bib:Article", "book": "bib:Book", "bookSection": "bib:BookSection",
        "thesis": "bib:Thesis", "report": "bib:Report", "manuscript": "bib:Manuscript",
        "newspaperArticle": "bib:Article", "magazineArticle": "bib:Article", "letter": "bib:Memo",
        "interview": "bib:Interview", "artwork": "bib:Illustration", "presentation": "bib:ConferenceProceedings",
        "film": "bib:MotionPicture", "videoRecording": "bib:Recording", "audioRecording": "bib:Recording",
        "podcast": "bib:Recording", "tvBroadcast": "bib:Recording", "radioBroadcast": "bib:Recording",
        "webpage": "bib:Document", "encyclopediaArticle": "bib:Article",
        "dictionaryEntry": "bib:Article", "document": "bib:Document",
        "case": "bib:Legislation", "statute": "bib:Legislation", "regulation": "bib:Legislation",
        "gazette": "bib:Legislation", "treaty": "bib:Legislation", "hearing": "bib:Recording",
        "bills": "bib:Legislation"
    }

    target_item_ids = []
    try:
        if args.collection:
            cursor.execute("SELECT collectionID FROM collections WHERE collectionName = ?;", (args.collection,))
            col_row = cursor.fetchone()
            if not col_row:
                print(f"Error: Collection '{args.collection}' not found!", file=sys.stderr)
                conn.close()
                sys.exit(1)
            collection_id = col_row[0]
            cursor.execute("""
                SELECT ci.itemID FROM collectionItems ci
                JOIN items i ON ci.itemID = i.itemID
                WHERE ci.collectionID = ? 
                AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
                AND i.itemTypeID IN (SELECT itemTypeID FROM itemTypes WHERE typeName NOT IN ('attachment', 'note'));
            """, (collection_id,))
            target_item_ids = [row[0] for row in cursor.fetchall()]
        else:
            cursor.execute("""
                SELECT DISTINCT ci.itemID FROM collectionItems ci
                JOIN items i ON ci.itemID = i.itemID
                WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
                AND i.itemTypeID IN (SELECT itemTypeID FROM itemTypes WHERE typeName NOT IN ('attachment', 'note'))
                UNION
                SELECT i.itemID FROM items i
                WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
                AND i.itemID NOT IN (SELECT itemID FROM collectionItems)
                AND i.itemTypeID IN (SELECT itemTypeID FROM itemTypes WHERE typeName NOT IN ('attachment', 'note'));
            """)
            target_item_ids = [row[0] for row in cursor.fetchall()]
    except sqlite3.OperationalError as e:
        print(f"Error querying database: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    if not target_item_ids:
        print("No items found to extract.")
        conn.close()
        return

    placeholders = ",".join(["?"] * len(target_item_ids))
    data_errors = []
    data_warnings = []
    generated_notes = [] 
    note_counter = 9000000 

    # 1. FETCH PRIMARY ITEMS
    cursor.execute(f"""
        SELECT itemID, itemTypeID, dateAdded, dateModified FROM items 
        WHERE itemID IN ({placeholders})
        AND itemID NOT IN (SELECT itemID FROM deletedItems);
    """, target_item_ids)
    items = cursor.fetchall()

    parent_meta = {}
    item_full_data = {}
    container_fields = {'publicationtitle', 'booktitle', 'proceedingstitle', 'encyclopediatitle', 'dictionarytitle', 'container-title', 'series'}

    for item_id, type_id, date_added_db, date_modified_db in items:
        internal_type = item_types.get(type_id, "document")
        cursor.execute("""
            SELECT LOWER(f.fieldName), iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ?;
        """, (item_id,))
        fields_dict = dict(cursor.fetchall())

        title = fields_dict.get('title', '(Untitled)')
        date_str = fields_dict.get('date', '(No Date)')

        cursor.execute("""
            SELECT c.lastName, c.firstName, ct.creatorType FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ? ORDER BY ic.orderIndex;
        """, (item_id,))
        creators_rows = cursor.fetchall()

        # Audit creators
        valid_creators_summary = []
        sanitised_creators = []
        for s, f, ctype in creators_rows:
            clean_s = (s or "").strip()
            clean_f = (f or "").strip()

            if not clean_s and not clean_f:
                data_warnings.append({
                    'is_field': True, 'item_type': internal_type, 'item_author': '(Empty)',
                    'item_date': date_str, 'item_title': title, 'field_name': f'Creator ({ctype})',
                    'problem': "Completely empty creator entry detected. Safe fallback applied (skipped)."
                })
            elif not clean_s and clean_f:
                clean_s = "(unknown)"
                data_warnings.append({
                    'is_field': True, 'item_type': internal_type, 'item_author': clean_f,
                    'item_date': date_str, 'item_title': title, 'field_name': f'Creator ({ctype})',
                    'problem': "Missing surname converted to '(unknown)'."
                })
                valid_creators_summary.append(f"{clean_s}, {clean_f}")
                sanitised_creators.append((clean_s, clean_f, ctype))
            else:
                valid_creators_summary.append(f"{clean_s}, {clean_f}" if clean_f else clean_s)
                sanitised_creators.append((clean_s, clean_f, ctype))

        author_summary = "; ".join(valid_creators_summary) if valid_creators_summary else "(No Author)"

        # Multi-language handling
        raw_lang = (fields_dict.get('language') or fields_dict.get('languagecode') or '').strip()
        norm_lang, lang_names = parse_languages(raw_lang)

        if raw_lang:
            note_added_str = ""
            if len(lang_names) >= 2:
                doc_type_str = "Bilingual" if len(lang_names) == 2 else "Multilingual"
                if len(lang_names) == 2:
                    joined_langs = f"{lang_names[0]} and {lang_names[1]}"
                else:
                    joined_langs = f"{', '.join(lang_names[:-1])} and {lang_names[-1]}"

                note_text = f"{doc_type_str} document in {joined_langs}"
                note_counter += 1
                generated_notes.append({"note_id": note_counter, "parent_id": item_id, "note_text": note_text})
                note_added_str = f' And a note added: "{note_text}"'

            if norm_lang:
                data_warnings.append({
                    'is_field': True, 'item_type': internal_type, 'item_author': author_summary,
                    'item_date': date_str, 'item_title': title, 'field_name': 'Language',
                    'problem': f"Non-ISO language code '{raw_lang}' converted to ISO '{norm_lang}'.{note_added_str}"
                })
            else:
                data_warnings.append({
                    'is_field': True, 'item_type': internal_type, 'item_author': author_summary,
                    'item_date': date_str, 'item_title': title, 'field_name': 'Language',
                    'problem': f"Unrecognised language code '{raw_lang}' converted to blank (unknown).{note_added_str}"
                })

        raw_extra = (fields_dict.get('extra') or '').strip()
        extra_lines = [raw_extra] if raw_extra else []

        if date_added_db and "original-date-added:" not in raw_extra.lower():
            extra_lines.append(f"original-date-added: {date_added_db.strip()}")
        if date_modified_db and "previous-date-modified:" not in raw_extra.lower():
            extra_lines.append(f"previous-date-modified: {date_modified_db.strip()}")

        cursor.execute("""
            SELECT f.fieldName, ida.languageTag, iv.value
            FROM itemDataAlt ida
            JOIN fields f ON ida.fieldID = f.fieldID
            JOIN itemDataValues iv ON ida.valueID = iv.valueID
            WHERE ida.itemID = ?;
        """, (item_id,))
        alt_rows = cursor.fetchall()

        for fname_raw, lang_tag_raw, fval_raw in alt_rows:
            if not fval_raw or not fval_raw.strip():
                continue
            fname = fname_raw.strip().lower()
            fval = fval_raw.strip()
            var_iso = parse_languages(lang_tag_raw)[0] or lang_tag_raw.strip().lower()

            if fname == 'title':
                extra_lines.append(f"title-{var_iso}: {fval}")
            elif fname in container_fields:
                extra_lines.append(f"container-title-{var_iso}: {fval}")
            else:
                extra_lines.append(f"{fname}-{var_iso}: {fval}")

        final_extra = "\n".join(extra_lines).strip()
        parent_meta[item_id] = {'type': internal_type, 'title': title, 'date': date_str, 'author': author_summary}
        item_full_data[item_id] = {
            'title': title, 'date': date_str, 'norm_lang': norm_lang,
            'final_extra': final_extra, 'creators': sanitised_creators
        }

    # 2. FETCH ATTACHMENTS
    cursor.execute(f"""
        SELECT i.itemID, ia.parentItemID, i.key, ia.path, ia.linkMode
        FROM items i
        JOIN itemAttachments ia ON i.itemID = ia.itemID
        WHERE ia.parentItemID IN ({placeholders})
        AND i.itemID NOT IN (SELECT itemID FROM deletedItems);
    """, target_item_ids)

    attachments_by_parent = {}
    all_attachments = []

    for att_id, parent_id, key, raw_path, link_mode_db in cursor.fetchall():
        path_str = raw_path or ""
        clean_p = path_str.replace("attachments:", "").replace("storage:", "").strip()

        cursor.execute("""
            SELECT LOWER(f.fieldName), iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ? AND LOWER(f.fieldName) IN ('title', 'url');
        """, (att_id,))

        fdata = dict(cursor.fetchall())
        att_title = fdata.get("title") or (clean_p if clean_p else "Snapshot")
        att_url = fdata.get("url") or (path_str if path_str.startswith("http") else "")

        rel_rdf_path = ""
        file_exists_on_disk = False
        pmeta = parent_meta.get(parent_id, {'type': 'Item', 'author': '(Unknown)', 'date': '', 'title': '(Unknown Parent)'})

        if link_mode_db in (0, 1):
            src_key_dir = os.path.join(storage_base, key)
            filename = clean_p if clean_p else "snapshot.html"

            if os.path.exists(src_key_dir):
                src_file = os.path.join(src_key_dir, filename)
                if not os.path.exists(src_file):
                    files_in_key = [f for f in os.listdir(src_key_dir) if not f.startswith(".")]
                    if files_in_key:
                        filename = files_in_key[0]
                        src_file = os.path.join(src_key_dir, filename)

                if os.path.exists(src_file):
                    file_exists_on_disk = True
                    if not args.report_only:
                        target_sub_dir = os.path.join(files_dir, str(att_id))
                        os.makedirs(target_sub_dir, exist_ok=True)
                        dest_file = os.path.join(target_sub_dir, filename)
                        try:
                            os.symlink(src_file, dest_file)
                        except OSError:
                            shutil.copy2(src_file, dest_file)
                    rel_rdf_path = f"files/{att_id}/{filename}"

            if not file_exists_on_disk:
                data_errors.append({
                    'is_field': False,
                    'parent_type': pmeta['type'], 'parent_author': pmeta['author'],
                    'parent_date': pmeta['date'], 'parent_title': pmeta['title'],
                    'child_type': 'Snapshot' if link_mode_db == 1 else 'Stored Attachment',
                    'child_name': att_title,
                    'problem': f"Missing file on disk (Expected at: ~/Jurism/storage/{key}/{filename})."
                })

        elif link_mode_db == 2:
            if path_str.startswith('/Users/') or path_str.startswith('C:'):
                data_warnings.append({
                    'is_field': False,
                    'parent_type': pmeta['type'], 'parent_author': pmeta['author'],
                    'parent_date': pmeta['date'], 'parent_title': pmeta['title'],
                    'child_type': 'Linked Attachment', 'child_name': att_title,
                    'problem': f"Legacy absolute path detected ('{path_str}'). Converted to relative base path."
                })

        att_entry = {
            "att_id": att_id, "parent_id": parent_id, "clean_p": clean_p,
            "raw_path": path_str, "att_url": att_url, "title": att_title,
            "link_mode_db": link_mode_db, "rel_rdf_path": rel_rdf_path,
            "file_exists_on_disk": file_exists_on_disk,
        }
        attachments_by_parent.setdefault(parent_id, []).append(att_entry)
        all_attachments.append(att_entry)

    # 3. FETCH NOTES
    cursor.execute(f"""
        SELECT itemID, parentItemID, note 
        FROM itemNotes 
        WHERE parentItemID IN ({placeholders})
        AND itemID NOT IN (SELECT itemID FROM deletedItems);
    """, target_item_ids)

    notes_by_parent = {}
    all_notes = []

    for note_id, parent_id, note_text in cursor.fetchall():
        note_entry = {"note_id": note_id, "parent_id": parent_id, "note_text": note_text or ""}
        notes_by_parent.setdefault(parent_id, []).append(note_entry)
        all_notes.append(note_entry)

    # Append generated language notes
    for gnote in generated_notes:
        notes_by_parent.setdefault(gnote["parent_id"], []).append(gnote)
        all_notes.append(gnote)

    # WRITE ANOMALY REPORT
    report_filename = f"{extract_dir_name}_report.txt"
    report_path = os.path.join(extract_dir, report_filename)

    def write_report_section(file_handle, items_list, prefix_label):
        for idx, a in enumerate(items_list, 1):
            file_handle.write(f"[{prefix_label} {idx}]\n")
            if a.get('is_field'):
                file_handle.write(f"  Item        : [{a['item_type']}] {a['item_author']} ({a['item_date']}) - \"{a['item_title']}\"\n")
                file_handle.write(f"  Field       : \"{a['field_name']}\"\n")
            else:
                file_handle.write(f"  Parent Item : [{a['parent_type']}] {a['parent_author']} ({a['parent_date']}) - \"{a['parent_title']}\"\n")
                file_handle.write(f"  Child Item  : [{a['child_type']}] \"{a['child_name']}\"\n")
            file_handle.write(f"  Issue       : {a['problem']}\n-------------------------------------------------------------------\n")

    with open(report_path, "w", encoding="utf-8") as r:
        r.write("===================================================================\n")
        r.write("JURISM TO ZOTERO MIGRATION ANOMALY REPORT\n")
        r.write(f"Target Scope: {args.collection if args.collection else 'Entire Library'}\n")
        r.write("===================================================================\n\n")

        r.write("1. Data Integrity Errors\n===================================================================\n")
        if not data_errors:
            r.write("None found.\n\n")
        else:
            write_report_section(r, data_errors, "ERROR")
            r.write("\n")

        r.write("2. Data Warnings\n===================================================================\n")
        if not data_warnings:
            r.write("None found.\n\n")
        else:
            write_report_section(r, data_warnings, "WARNING")
            r.write("\n")

    print(f"[*] Anomaly report generated at: {report_path}")

    if args.report_only:
        print("[*] Analysis completed (report-only flag passed).")
        conn.close()
        return

    # WRITE RDF PAYLOAD
    rdf_filename = f"{extract_dir_name}.rdf"
    rdf_path = os.path.join(extract_dir, rdf_filename)

    with open(rdf_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<rdf:RDF\n')
        f.write(' xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n')
        f.write(' xmlns:z="http://www.zotero.org/namespaces/export#"\n')
        f.write(' xmlns:dcterms="http://purl.org/dc/terms/"\n')
        f.write(' xmlns:dc="http://purl.org/dc/elements/1.1/"\n')
        f.write(' xmlns:link="http://purl.org/rss/1.0/modules/link/"\n')
        f.write(' xmlns:bib="http://purl.org/net/biblio#"\n')
        f.write(' xmlns:foaf="http://xmlns.com/foaf/0.1/"\n')
        f.write(' xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/"\n')
        f.write(' xmlns:vcard="http://nwalsh.com/rdf/vCard#">\n\n')

        try:
            # Primary Items
            for item_id, type_id, date_added_db, date_modified_db in items:
                internal_type = item_types.get(type_id, "document")
                rdf_tag = rdf_tag_map.get(internal_type, "bib:Document")
                idata = item_full_data[item_id]

                f.write(f'    <{rdf_tag} rdf:about="#item_{item_id}">\n')
                f.write(f"        <z:itemType>{clean_xml(internal_type)}</z:itemType>\n")

                if idata['creators']:
                    f.write("        <bib:authors>\n            <rdf:Seq>\n")
                    for surname, forename, ctype in idata['creators']:
                        clean_s = (surname or "").strip()
                        clean_f = (forename or "").strip()

                        if not clean_s and not clean_f:
                            continue

                        f.write("                <rdf:li>\n")
                        f.write("                    <foaf:Person>\n")
                        f.write(f"                        <foaf:surname>{clean_xml(clean_s)}</foaf:surname>\n")
                        f.write(f"                        <foaf:givenName>{clean_xml(clean_f)}</foaf:givenName>\n")
                        f.write("                    </foaf:Person>\n")
                        f.write("                </rdf:li>\n")
                    f.write("            </rdf:Seq>\n        </bib:authors>\n")

                if item_id in notes_by_parent:
                    for note in notes_by_parent[item_id]:
                        f.write(f'        <dcterms:isReferencedBy rdf:resource="#item_{note["note_id"]}"/>\n')

                if item_id in attachments_by_parent:
                    for att in attachments_by_parent[item_id]:
                        f.write(f'        <link:link rdf:resource="#item_{att["att_id"]}"/>\n')

                # Strictly suppress title/extra for items mapped as annotations
                if internal_type.lower() != "annotation":
                    if idata['title']:
                        f.write(f"        <dc:title>{clean_xml(idata['title'])}</dc:title>\n")

                    if idata['final_extra']:
                        f.write(f"        <dc:description>{clean_xml(idata['final_extra'])}</dc:description>\n")

                if idata['date'] and idata['date'] != '(No Date)':
                    f.write(f"        <dc:date>{clean_xml(idata['date'])}</dc:date>\n")

                if idata['norm_lang']:
                    f.write(f"        <dc:language>{clean_xml(idata['norm_lang'])}</dc:language>\n")

                f.write(f"    </{rdf_tag}>\n\n")

            # Notes Nodes (strictly clean of any title or extra tags)
            for note in all_notes:
                f.write(f'    <bib:Memo rdf:about="#item_{note["note_id"]}">\n')
                f.write(f'        <rdf:value>{clean_xml(note["note_text"])}</rdf:value>\n')
                f.write("    </bib:Memo>\n\n")

            # Attachment Nodes (strictly clean of description/extra tags)
            for att in all_attachments:
                f.write(f'    <z:Attachment rdf:about="#item_{att["att_id"]}">\n')
                f.write("        <z:itemType>attachment</z:itemType>\n")
                f.write(f'        <dc:title>{clean_xml(att["title"])}</dc:title>\n')

                if att["link_mode_db"] in (0, 1) and att["file_exists_on_disk"]:
                    f.write(f'        <rdf:resource rdf:resource="{clean_attr(att["rel_rdf_path"])}"/>\n')
                    f.write(f'        <z:linkMode>{att["link_mode_db"]}</z:linkMode>\n')
                    mime = "text/html" if att["rel_rdf_path"].endswith((".html", ".htm")) else "application/pdf"
                    f.write(f"        <link:type>{mime}</link:type>\n")

                elif att["att_url"]:
                    f.write("        <dc:identifier>\n            <dcterms:URI>\n")
                    f.write(f'                <rdf:value>{clean_xml(att["att_url"])}</rdf:value>\n')
                    f.write("            </dcterms:URI>\n        </dc:identifier>\n")
                    f.write("        <z:linkMode>3</z:linkMode>\n        <link:type>text/html</link:type>\n")

                else:
                    filename = att["clean_p"] if att["clean_p"] else "attachment"
                    f.write(f'        <z:path rdf:resource="attachments:{clean_attr(filename)}"/>\n')
                    f.write("        <z:linkMode>2</z:linkMode>\n")
                    mime = "text/html" if filename.lower().endswith((".html", ".htm")) else "application/pdf"
                    f.write(f"        <link:type>{mime}</link:type>\n")

                f.write("    </z:Attachment>\n\n")
        finally:
            f.write("</rdf:RDF>\n")
            f.flush()

    conn.close()

    # Automatic Diagnostic XML Validation
    try:
        ET.parse(rdf_path)
        print(f"[*] Extraction completed successfully! File passed XML validation: {rdf_path}")
    except ET.ParseError as pe:
        print(f"\n[!] XML Validation Error detected at line {pe.position[0]}, column {pe.position[1]}: {pe}", file=sys.stderr)


if __name__ == "__main__":
    run_extractor()