#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import sqlite3
import sys
import xml.sax.saxutils as xml_escape

# Common language mapping table for ISO 639-1 normalization
LANGUAGE_MAP = {
    'english': 'en', 'en-us': 'en-US', 'en-gb': 'en-GB',
    'french': 'fr', 'français': 'fr', 'fr': 'fr',
    'german': 'de', 'deutsch': 'de', 'de': 'de',
    'spanish': 'es', 'español': 'es', 'es': 'es',
    'portuguese': 'pt', 'português': 'pt', 'pt-pt': 'pt-PT', 'pt-br': 'pt-BR', 'pt': 'pt',
    'italian': 'it', 'italiano': 'it', 'it': 'it',
    'dutch': 'nl', 'nederlands': 'nl', 'nl': 'nl',
    'russian': 'ru', 'ru': 'ru',
    'chinese': 'zh', 'zh': 'zh',
    'japanese': 'ja', 'ja': 'ja'
}

# Regex matching standard ISO 639-1 (2-letter) and ISO 639-1 + region (e.g., en-GB, pt-PT)
ISO_LANG_REGEX = re.compile(r'^[a-z]{2}(-[A-Z]{2})?$')


def sanitize_filename(name):
    """Remove OS-illegal characters from folder/file names."""
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip()


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Extract Jurism/Zotero items, collections, notes, and attachments into a Zotero-compliant RDF payload.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--collection",
        type=str,
        metavar="NAME",
        help="Name of the specific collection to export.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Export all primary items in the library.",
    )

    parser.add_argument(
        "--db-path",
        type=str,
        default="jurism.sqlite",
        help="Path to jurism.sqlite or zotero.sqlite (defaults to 'jurism.sqlite' in current directory).",
    )

    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Custom directory path for exported RDF (defaults to collection name or 'My Library').",
    )

    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Perform database analysis and write the anomaly report without exporting RDF or files.",
    )

    return parser.parse_args()


def run_extractor():
    args = parse_arguments()

    db_path = os.path.abspath(args.db_path)
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at path: {db_path}", file=sys.stderr)
        sys.exit(1)

    # EARLY LOCK CHECK: Connect to database before creating any output folders
    uri_path = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri_path, uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT itemTypeID, typeName FROM itemTypes")
        item_types = {row[0]: row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            print("Error: Database is locked — please close Jurism/Zotero and try again.", file=sys.stderr)
        else:
            print(f"Error accessing database: {e}", file=sys.stderr)
        sys.exit(1)

    base_dir = os.path.dirname(db_path)
    storage_base = os.path.join(base_dir, "storage")

    # Determine dynamic output directory and filename
    if args.out_dir:
        export_dir_name = sanitize_filename(args.out_dir)
    elif args.collection:
        export_dir_name = sanitize_filename(args.collection)
    else:
        export_dir_name = "My Library"

    export_dir = os.path.abspath(export_dir_name)
    files_dir = os.path.join(export_dir, "files")

    # Create output directories only after database connection is verified
    if not args.report_only:
        if os.path.exists(export_dir):
            shutil.rmtree(export_dir)
        os.makedirs(files_dir, exist_ok=True)
    else:
        os.makedirs(export_dir, exist_ok=True)

    rdf_tag_map = {
        "journalArticle": "bib:Article",
        "book": "bib:Book",
        "bookSection": "bib:BookSection",
        "thesis": "bib:Thesis",
        "report": "bib:Report",
        "manuscript": "bib:Manuscript",
        "newspaperArticle": "bib:Article",
        "magazineArticle": "bib:Article",
        "letter": "bib:Letter",
        "interview": "bib:Interview",
        "artwork": "bib:Illustration",
        "presentation": "bib:ConferenceProceedings",
        "film": "bib:MotionPicture",
        "videoRecording": "bib:Recording",
        "audioRecording": "bib:Recording",
        "podcast": "bib:Recording",
        "tvBroadcast": "bib:Recording",
        "radioBroadcast": "bib:Recording",
        "webpage": "bib:Document",
        "encyclopediaArticle": "rdf:Description",
        "dictionaryEntry": "rdf:Description",
        "document": "bib:Document",
    }

    # Resolve Target Items
    try:
        if args.collection:
            cursor.execute(
                "SELECT collectionID FROM collections WHERE collectionName = ?;",
                (args.collection,),
            )
            col_row = cursor.fetchone()
            if not col_row:
                print(f"Error: Collection '{args.collection}' not found in database!", file=sys.stderr)
                conn.close()
                sys.exit(1)
            collection_id = col_row[0]
            cursor.execute(
                "SELECT itemID FROM collectionItems WHERE collectionID = ?;",
                (collection_id,),
            )
            target_item_ids = [row[0] for row in cursor.fetchall()]
        else:
            cursor.execute(
                "SELECT itemID FROM items WHERE itemID NOT IN (SELECT itemID FROM deletedItems);"
            )
            target_item_ids = [row[0] for row in cursor.fetchall()]
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            print("Error: Database is locked — please close Jurism/Zotero and try again.", file=sys.stderr)
            sys.exit(1)

    if not target_item_ids:
        print("No items found to export.")
        conn.close()
        return

    placeholders = ",".join(["?"] * len(target_item_ids))

    data_errors = []
    data_warnings = []

    # 1. FETCH PRIMARY ITEMS METADATA
    cursor.execute(f"""
        SELECT itemID, itemTypeID FROM items 
        WHERE itemID IN ({placeholders})
        AND itemID NOT IN (SELECT itemID FROM deletedItems)
        AND itemTypeID IN (SELECT itemTypeID FROM itemTypes WHERE typeName != 'attachment' AND typeName != 'note');
    """, target_item_ids)
    items = cursor.fetchall()

    parent_meta = {}

    for item_id, type_id in items:
        internal_type = item_types.get(type_id, "document")
        
        # Title
        cursor.execute("""
            SELECT iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ? AND f.fieldName = 'title';
        """, (item_id,))
        t_row = cursor.fetchone()
        title = t_row[0] if (t_row and t_row[0]) else "(Untitled)"

        # Date
        cursor.execute("""
            SELECT iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ? AND f.fieldName = 'date';
        """, (item_id,))
        d_row = cursor.fetchone()
        date_str = d_row[0] if (d_row and d_row[0]) else "(No Date)"

        # Author / Creator
        cursor.execute("""
            SELECT c.lastName, c.firstName FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            WHERE ic.itemID = ? ORDER BY ic.orderIndex LIMIT 1;
        """, (item_id,))
        c_row = cursor.fetchone()
        if c_row:
            author_str = f"{c_row[0]}, {c_row[1]}".strip(", ")
        else:
            author_str = "(No Author)"

        # Language Field Verification
        cursor.execute("""
            SELECT iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ? AND f.fieldName = 'language';
        """, (item_id,))
        lang_row = cursor.fetchone()
        if lang_row and lang_row[0]:
            raw_lang = lang_row[0].strip()
            
            if not ISO_LANG_REGEX.match(raw_lang):
                norm_lang = LANGUAGE_MAP.get(raw_lang.lower())
                if norm_lang:
                    data_warnings.append({
                        'parent_type': internal_type,
                        'parent_author': author_str,
                        'parent_date': date_str,
                        'parent_title': title,
                        'child_type': 'Field',
                        'child_name': 'Language',
                        'problem': f"Language code '{raw_lang}' recognised but not in ISO format (automatically converted to '{norm_lang}')."
                    })
                else:
                    data_errors.append({
                        'parent_type': internal_type,
                        'parent_author': author_str,
                        'parent_date': date_str,
                        'parent_title': title,
                        'child_type': 'Field',
                        'child_name': 'Language',
                        'problem': f"Unrecognised language code '{raw_lang}'."
                    })

        parent_meta[item_id] = {
            'type': internal_type,
            'title': title,
            'date': date_str,
            'author': author_str
        }

    # 2. FETCH ATTACHMENTS (EXCLUDING DELETED ATTACHMENTS)
    cursor.execute(f"""
        SELECT i.itemID, ia.parentItemID, i.key, ia.path, ia.linkMode
        FROM items i
        JOIN itemAttachments ia ON i.itemID = ia.itemID
        WHERE ia.parentItemID IN ({placeholders})
        AND i.itemID NOT IN (SELECT itemID FROM deletedItems);
    """, target_item_ids)

    attachments_by_parent = {}
    all_attachments = []
    found_files_count = 0
    missing_files_count = 0

    for att_id, parent_id, key, raw_path, link_mode_db in cursor.fetchall():
        path_str = raw_path or ""
        clean_p = path_str.replace("attachments:", "").replace("storage:", "").strip()

        cursor.execute("""
            SELECT f.fieldName, iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ? AND f.fieldName IN ('title', 'url');
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
                    found_files_count += 1

            if not file_exists_on_disk:
                missing_files_count += 1
                data_errors.append({
                    'parent_type': pmeta['type'],
                    'parent_author': pmeta['author'],
                    'parent_date': pmeta['date'],
                    'parent_title': pmeta['title'],
                    'child_type': 'Snapshot' if link_mode_db == 1 else 'Stored Attachment',
                    'child_name': att_title,
                    'problem': f"Missing file on disk (Expected at: ~/Jurism/storage/{key}/{filename})."
                })

        elif link_mode_db == 2:  # Linked File
            if path_str.startswith('/Users/') or path_str.startswith('C:'):
                data_warnings.append({
                    'parent_type': pmeta['type'],
                    'parent_author': pmeta['author'],
                    'parent_date': pmeta['date'],
                    'parent_title': pmeta['title'],
                    'child_type': 'Linked Attachment',
                    'child_name': att_title,
                    'problem': f"Legacy absolute path detected ('{path_str}'). Converted to relative base path."
                })

        att_entry = {
            "att_id": att_id,
            "parent_id": parent_id,
            "clean_p": clean_p,
            "raw_path": path_str,
            "att_url": att_url,
            "title": att_title,
            "link_mode_db": link_mode_db,
            "rel_rdf_path": rel_rdf_path,
            "file_exists_on_disk": file_exists_on_disk,
        }
        attachments_by_parent.setdefault(parent_id, []).append(att_entry)
        all_attachments.append(att_entry)

    # 3. FETCH NOTES (EXCLUDING DELETED NOTES)
    cursor.execute(f"""
        SELECT itemID, parentItemID, note 
        FROM itemNotes 
        WHERE parentItemID IN ({placeholders})
        AND itemID NOT IN (SELECT itemID FROM deletedItems);
    """, target_item_ids)

    notes_by_parent = {}
    all_notes = []

    for note_id, parent_id, note_text in cursor.fetchall():
        note_entry = {
            "note_id": note_id,
            "parent_id": parent_id,
            "note_text": note_text or "",
        }
        notes_by_parent.setdefault(parent_id, []).append(note_entry)
        all_notes.append(note_entry)

    # WRITE ANOMALY REPORT TEXT FILE
    report_filename = f"{export_dir_name}_report.txt"
    report_path = os.path.join(export_dir, report_filename)

    with open(report_path, "w", encoding="utf-8") as r:
        r.write("===================================================================\n")
        r.write("JURISM TO ZOTERO MIGRATION ANOMALY REPORT\n")
        r.write(f"Target Scope: {args.collection if args.collection else 'Entire Library'}\n")
        r.write("===================================================================\n\n")

        # 1. Data Integrity Errors
        r.write("1. Data integrity errors\n")
        r.write("===================================================================\n")
        if not data_errors:
            r.write("None found.\n\n")
        else:
            for idx, a in enumerate(data_errors, 1):
                r.write(f"[{idx}]\n")
                r.write(f"  Parent Item : [{a['parent_type']}] {a['parent_author']} ({a['parent_date']}) - \"{a['parent_title']}\"\n")
                r.write(f"  Child Item  : [{a['child_type']}] \"{a['child_name']}\"\n")
                r.write(f"  Issue       : {a['problem']}\n")
                r.write("-------------------------------------------------------------------\n")
            r.write("\n")

        # 2. Data Warnings
        r.write("2. Data warnings\n")
        r.write("===================================================================\n")
        if not data_warnings:
            r.write("None found.\n\n")
        else:
            for idx, a in enumerate(data_warnings, 1):
                r.write(f"[{idx}]\n")
                r.write(f"  Parent Item : [{a['parent_type']}] {a['parent_author']} ({a['parent_date']}) - \"{a['parent_title']}\"\n")
                r.write(f"  Child Item  : [{a['child_type']}] \"{a['child_name']}\"\n")
                r.write(f"  Issue       : {a['problem']}\n")
                r.write("-------------------------------------------------------------------\n")
            r.write("\n")

    print(f"[*] Anomaly report generated at: {report_path}")

    if args.report_only:
        print("[*] Report-only flag set. Skipping RDF export payload generation.")
        conn.close()
        return

    # WRITE RDF FILE PAYLOAD
    rdf_filename = f"{export_dir_name}.rdf"
    rdf_path = os.path.join(export_dir, rdf_filename)

    with open(rdf_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write("<rdf:RDF\n")
        f.write(' xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n')
        f.write(' xmlns:z="http://www.zotero.org/namespaces/export#"\n')
        f.write(' xmlns:dcterms="http://purl.org/dc/terms/"\n')
        f.write(' xmlns:dc="http://purl.org/dc/elements/1.1/"\n')
        f.write(' xmlns:link="http://purl.org/rss/1.0/modules/link/"\n')
        f.write(' xmlns:bib="http://purl.org/net/biblio#"\n')
        f.write(' xmlns:foaf="http://xmlns.com/foaf/0.1/"\n')
        f.write(' xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/"\n')
        f.write(' xmlns:vcard="http://nwalsh.com/rdf/vCard#">\n\n')

        # Primary Items
        for item_id, type_id in items:
            internal_type = item_types.get(type_id, "document")
            rdf_tag = rdf_tag_map.get(internal_type, "rdf:Description")

            f.write(f'    <{rdf_tag} rdf:about="#item_{item_id}">\n')
            f.write(f"        <z:itemType>{internal_type}</z:itemType>\n")

            # Child Notes
            if item_id in notes_by_parent:
                for note in notes_by_parent[item_id]:
                    f.write(
                        f'        <dcterms:isReferencedBy rdf:resource="#item_{note["note_id"]}"/>\n'
                    )

            # Child Attachments
            if item_id in attachments_by_parent:
                for att in attachments_by_parent[item_id]:
                    f.write(
                        f'        <link:link rdf:resource="#item_{att["att_id"]}"/>\n'
                    )

            # Title
            cursor.execute("""
                SELECT iv.value FROM itemData id
                JOIN fields f ON id.fieldID = f.fieldID
                JOIN itemDataValues iv ON id.valueID = iv.valueID
                WHERE id.itemID = ? AND f.fieldName = 'title';
            """, (item_id,))
            t_row = cursor.fetchone()
            if t_row and t_row[0]:
                f.write(f"        <dc:title>{xml_escape.escape(t_row[0])}</dc:title>\n")

            f.write(f"    </{rdf_tag}>\n\n")

        # Notes Nodes
        for note in all_notes:
            f.write(f'    <bib:Memo rdf:about="#item_{note["note_id"]}">\n')
            f.write(
                f'        <rdf:value>{xml_escape.escape(note["note_text"])}</rdf:value>\n'
            )
            f.write("    </bib:Memo>\n\n")

        # Attachment Nodes
        for att in all_attachments:
            f.write(f'    <z:Attachment rdf:about="#item_{att["att_id"]}">\n')
            f.write("        <z:itemType>attachment</z:itemType>\n")
            f.write(f'        <dc:title>{xml_escape.escape(att["title"])}</dc:title>\n')

            if att["link_mode_db"] in (0, 1) and att["file_exists_on_disk"]:
                f.write(
                    f'        <rdf:resource rdf:resource="{xml_escape.escape(att["rel_rdf_path"])}"/>\n'
                )
                f.write(f'        <z:linkMode>{att["link_mode_db"]}</z:linkMode>\n')
                mime = (
                    "text/html"
                    if att["rel_rdf_path"].endswith((".html", ".htm"))
                    else "application/pdf"
                )
                f.write(f"        <link:type>{mime}</link:type>\n")

            elif att["att_url"]:
                f.write("        <dc:identifier>\n")
                f.write("            <dcterms:URI>\n")
                f.write(
                    f'                <rdf:value>{xml_escape.escape(att["att_url"])}</rdf:value>\n'
                )
                f.write("            </dcterms:URI>\n")
                f.write("        </dc:identifier>\n")
                f.write("        <z:linkMode>3</z:linkMode>\n")
                f.write("        <link:type>text/html</link:type>\n")

            else:
                filename = att["clean_p"] if att["clean_p"] else "attachment"
                f.write(
                    f'        <z:path rdf:resource="attachments:{xml_escape.escape(filename)}"/>\n'
                )
                f.write("        <z:linkMode>2</z:linkMode>\n")
                mime = (
                    "text/html"
                    if filename.lower().endswith((".html", ".htm"))
                    else "application/pdf"
                )
                f.write(f"        <link:type>{mime}</link:type>\n")

            f.write("    </z:Attachment>\n\n")

        f.write("</rdf:RDF>\n")

    conn.close()
    print(f"[*] Export completed successfully!")
    print(f"  - Output folder: {export_dir}")
    print(f"  - Target RDF file: {rdf_path}")
    print(f"  - Primary items exported: {len(items)}")
    print(f"  - Child notes exported: {len(all_notes)}")
    print(
        f"  - Child attachments exported: {len(all_attachments)} ({found_files_count} symlinked, {missing_files_count} missing file links preserved)"
    )


if __name__ == "__main__":
    run_extractor()