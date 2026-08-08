import sqlite3
import os
import shutil
import xml.sax.saxutils as xml_escape

def export_extractor_test_to_rdf_v21():
    base_dir = os.path.expanduser('~/Jurism')
    db_path = os.path.join(base_dir, 'jurism.sqlite')
    storage_base = os.path.join(base_dir, 'storage')  # Fixed: ~/Jurism/storage

    # Target directory payload
    export_dir = os.path.join(base_dir, '#Extractor test v21')
    files_dir = os.path.join(export_dir, 'files')

    if os.path.exists(export_dir):
        shutil.rmtree(export_dir)
    os.makedirs(files_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    rdf_tag_map = {
        'journalArticle': 'bib:Article',
        'book': 'bib:Book',
        'bookSection': 'bib:BookSection',
        'thesis': 'bib:Thesis',
        'report': 'bib:Report',
        'manuscript': 'bib:Manuscript',
        'newspaperArticle': 'bib:Article',
        'magazineArticle': 'bib:Article',
        'letter': 'bib:Letter',
        'interview': 'bib:Interview',
        'artwork': 'bib:Illustration',
        'presentation': 'bib:ConferenceProceedings',
        'film': 'bib:MotionPicture',
        'videoRecording': 'bib:Recording',
        'audioRecording': 'bib:Recording',
        'podcast': 'bib:Recording',
        'tvBroadcast': 'bib:Recording',
        'radioBroadcast': 'bib:Recording',
        'webpage': 'bib:Document',
        'encyclopediaArticle': 'rdf:Description',
        'dictionaryEntry': 'rdf:Description',
        'document': 'bib:Document'
    }

    cursor.execute("SELECT itemTypeID, typeName FROM itemTypes")
    item_types = {row[0]: row[1] for row in cursor.fetchall()}

    # Get '#Export test' collection ID
    cursor.execute("SELECT collectionID FROM collections WHERE collectionName = '#Export test';")
    col_row = cursor.fetchone()
    if not col_row:
        print("Error: Collection '#Export test' not found!")
        conn.close()
        return

    collection_id = col_row[0]
    cursor.execute("SELECT itemID FROM collectionItems WHERE collectionID = ?;", (collection_id,))
    target_item_ids = [row[0] for row in cursor.fetchall()]

    placeholders = ','.join(['?'] * len(target_item_ids))

    # 1. FETCH ATTACHMENTS
    cursor.execute(f"""
        SELECT i.itemID, ia.parentItemID, i.key, ia.path, ia.linkMode
        FROM items i
        JOIN itemAttachments ia ON i.itemID = ia.itemID
        WHERE ia.parentItemID IN ({placeholders});
    """, target_item_ids)

    attachments_by_parent = {}
    all_attachments = []
    linked_count = 0

    for att_id, parent_id, key, raw_path, link_mode_db in cursor.fetchall():
        path_str = raw_path or ""
        clean_p = path_str.replace('attachments:', '').replace('storage:', '').strip()

        cursor.execute("""
            SELECT iv.value FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.itemID = ? AND f.fieldName IN ('title', 'url');
        """, (att_id,))
        t_row = cursor.fetchone()
        att_title = t_row[0] if (t_row and t_row[0]) else (clean_p if clean_p else "Attachment")

        # Symlink creation for stored files (0) and snapshots (1)
        rel_rdf_path = ""
        if link_mode_db in (0, 1):
            src_key_dir = os.path.join(storage_base, key)
            if os.path.exists(src_key_dir):
                target_sub_dir = os.path.join(files_dir, str(att_id))
                os.makedirs(target_sub_dir, exist_ok=True)
                
                # Determine target filename
                filename = clean_p if clean_p else "snapshot.html"
                src_file = os.path.join(src_key_dir, filename)
                
                if not os.path.exists(src_file):
                    # Fallback search for any visible file in key directory
                    files_in_key = [f for f in os.listdir(src_key_dir) if not f.startswith('.')]
                    if files_in_key:
                        filename = files_in_key[0]
                        src_file = os.path.join(src_key_dir, filename)

                if os.path.exists(src_file):
                    dest_file = os.path.join(target_sub_dir, filename)
                    try:
                        os.symlink(src_file, dest_file)
                    except OSError:
                        shutil.copy2(src_file, dest_file)
                    rel_rdf_path = f"files/{att_id}/{filename}"
                    linked_count += 1

        att_entry = {
            'att_id': att_id,
            'parent_id': parent_id,
            'clean_p': clean_p,
            'raw_path': path_str,
            'title': att_title,
            'link_mode_db': link_mode_db,
            'rel_rdf_path': rel_rdf_path
        }
        attachments_by_parent.setdefault(parent_id, []).append(att_entry)
        all_attachments.append(att_entry)

    # 2. FETCH NOTES
    cursor.execute(f"""
        SELECT itemID, parentItemID, note 
        FROM itemNotes 
        WHERE parentItemID IN ({placeholders});
    """, target_item_ids)

    notes_by_parent = {}
    all_notes = []

    for note_id, parent_id, note_text in cursor.fetchall():
        note_entry = {
            'note_id': note_id,
            'parent_id': parent_id,
            'note_text': note_text or ""
        }
        notes_by_parent.setdefault(parent_id, []).append(note_entry)
        all_notes.append(note_entry)

    # 3. FETCH PRIMARY ITEMS
    cursor.execute(f"""
        SELECT itemID, itemTypeID FROM items 
        WHERE itemID IN ({placeholders})
        AND itemID NOT IN (SELECT itemID FROM deletedItems)
        AND itemTypeID IN (SELECT itemTypeID FROM itemTypes WHERE typeName != 'attachment' AND typeName != 'note');
    """, target_item_ids)
    items = cursor.fetchall()

    rdf_path = os.path.join(export_dir, '#Extractor test v21.rdf')

    with open(rdf_path, 'w', encoding='utf-8') as f:
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

        # Primary Items
        for item_id, type_id in items:
            internal_type = item_types.get(type_id, 'document')
            rdf_tag = rdf_tag_map.get(internal_type, 'rdf:Description')

            f.write(f'    <{rdf_tag} rdf:about="#item_{item_id}">\n')
            f.write(f'        <z:itemType>{internal_type}</z:itemType>\n')

            # Child Notes
            if item_id in notes_by_parent:
                for note in notes_by_parent[item_id]:
                    f.write(f'        <dcterms:isReferencedBy rdf:resource="#item_{note["note_id"]}"/>\n')

            # Child Attachments
            if item_id in attachments_by_parent:
                for att in attachments_by_parent[item_id]:
                    f.write(f'        <link:link rdf:resource="#item_{att["att_id"]}"/>\n')

            # Title
            cursor.execute("""
                SELECT iv.value FROM itemData id
                JOIN fields f ON id.fieldID = f.fieldID
                JOIN itemDataValues iv ON id.valueID = iv.valueID
                WHERE id.itemID = ? AND f.fieldName = 'title';
            """, (item_id,))
            t_row = cursor.fetchone()
            if t_row and t_row[0]:
                f.write(f'        <dc:title>{xml_escape.escape(t_row[0])}</dc:title>\n')

            f.write(f'    </{rdf_tag}>\n\n')

        # Notes Nodes
        for note in all_notes:
            f.write(f'    <bib:Memo rdf:about="#item_{note["note_id"]}">\n')
            f.write(f'        <rdf:value>{xml_escape.escape(note["note_text"])}</rdf:value>\n')
            f.write('    </bib:Memo>\n\n')

        # Attachment Nodes
        for att in all_attachments:
            f.write(f'    <z:Attachment rdf:about="#item_{att["att_id"]}">\n')
            f.write('        <z:itemType>attachment</z:itemType>\n')
            f.write(f'        <dc:title>{xml_escape.escape(att["title"])}</dc:title>\n')

            if att['rel_rdf_path']:
                # Stored File / Snapshot payload path
                f.write(f'        <rdf:resource rdf:resource="{xml_escape.escape(att["rel_rdf_path"])}"/>\n')
                f.write(f'        <z:linkMode>{att["link_mode_db"]}</z:linkMode>\n')
                mime = 'text/html' if att['rel_rdf_path'].endswith(('.html', '.htm')) else 'application/pdf'
                f.write(f'        <link:type>{mime}</link:type>\n')

            elif att['link_mode_db'] == 2:
                # Linked External File
                f.write(f'        <z:path rdf:resource="attachments:{xml_escape.escape(att["clean_p"])}"/>\n')
                f.write('        <z:linkMode>2</z:linkMode>\n')
                f.write('        <link:type>application/pdf</link:type>\n')

            else:
                # Web Link
                f.write('        <dc:identifier>\n')
                f.write('            <dcterms:URI>\n')
                f.write(f'                <rdf:value>{xml_escape.escape(att["raw_path"])}</rdf:value>\n')
                f.write('            </dcterms:URI>\n')
                f.write('        </dc:identifier>\n')
                f.write('        <z:linkMode>3</z:linkMode>\n')
                f.write('        <link:type>text/html</link:type>\n')

            f.write('    </z:Attachment>\n\n')

        f.write('</rdf:RDF>\n')

    conn.close()
    print(f"Successfully generated folder payload at: {export_dir}")
    print(f"Created {linked_count} file symlinks in '{export_dir}/files/'.")

if __name__ == "__main__":
    export_extractor_test_to_rdf_v21()
