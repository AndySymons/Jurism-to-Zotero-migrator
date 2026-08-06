#!/usr/bin/env python3
"""
jurism_to_zotero.py

Commit: #4
Added features:
 - Export a specific collection by name with --collection-name "NAME" (looks up collection and its members in the DB).
 - Default behavior unchanged: emit file:/// absolute URIs for linked/storage files (no copying).
 - Keeps --verbose-attachments, --package-files planned but not implemented here.

This patch adds robust lookup for collection and collection->item mapping tables in the sqlite DB used by Jurism (which is Zotero-like).
If no collection tables are present, an error is shown and the export falls back to previous behavior.

Usage examples:
  python3 jurism_to_zotero.py --db jurism.sqlite --collection-name "#Export test" --out zotero_export_collection.rdf --json-out /dev/null --verbose-attachments
  python3 jurism_to_zotero.py --db jurism.sqlite --out zotero_import.rdf --limit 20

"""

from datetime import datetime
import zoneinfo
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom
import uuid

DEFAULT_LINKED_BASE = "/Volumes/X-Drive/Zotero linked attachments"
DEFAULT_DATA_DIR = os.path.expanduser("~/Jurism")
PREFS_GLOB = os.path.expanduser("~/Library/Application Support/Jurism/Profiles/*/prefs.js")

# common language normalisation map (extend as needed)
LANG_MAP = {
    'english': 'en', 'en': 'en', 'eng': 'en',
    'german': 'de', 'de': 'de', 'ger': 'de', 'deutsch': 'de',
    'french': 'fr', 'fr': 'fr', 'fra': 'fr', 'français': 'fr',
    'spanish': 'es', 'es': 'es', 'spa': 'es', 'italian': 'it', 'it': 'it',
    'zxx': 'zxx', 'unknown': 'unknown'
}


def now_lisbon():
    try:
        tz = zoneinfo.ZoneInfo('Europe/Lisbon')
    except Exception:
        tz = zoneinfo.ZoneInfo('UTC')
    return datetime.now(tz).isoformat()


def log(msg):
    print(f"[{now_lisbon()}] {msg}")


def find_prefs(prefs_glob=PREFS_GLOB):
    import glob
    for p in glob.glob(prefs_glob):
        if os.path.isfile(p):
            return p
    return None


def parse_prefs(prefs_path):
    linked = None
    dataDir = None
    if not prefs_path:
        return linked, dataDir
    try:
        with open(prefs_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                m = re.search(r'user_pref\("extensions.zotero.baseAttachmentPath",\s*"(.*)"\);', line)
                if m:
                    linked = m.group(1)
                m2 = re.search(r'user_pref\("extensions.zotero.dataDir",\s*"(.*)"\);', line)
                if m2:
                    dataDir = m2.group(1)
    except Exception:
        return linked, dataDir
    return linked, dataDir


def sqlite_rows(conn, query, params=()):
    cur = conn.cursor()
    cur.execute(query, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    for row in cur:
        yield dict(zip(cols, row))


def detect_storage_folder(data_dir):
    s = Path(data_dir) / "storage"
    if not s.exists():
        return None
    return s


def build_storage_index(storage_dir):
    index = {}
    if not storage_dir or not storage_dir.exists():
        return index
    for child in storage_dir.iterdir():
        if child.is_dir():
            for f in child.iterdir():
                if f.is_file():
                    index.setdefault(f.name.lower(), []).append(str(f.resolve()))
    return index


def resolve_storage_with_index(storage_index, path_value):
    if not storage_index:
        return None
    if ':' in path_value:
        _, tail = path_value.split(':', 1)
    else:
        tail = path_value
    basename = os.path.basename(tail).lower()
    if basename in storage_index:
        return storage_index[basename][0]
    if '?' in basename:
        key = basename.split('?', 1)[0]
        if key in storage_index:
            return storage_index[key][0]
    for k, v in storage_index.items():
        if basename in k:
            return v[0]
    return None


def find_notes_table(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%note%';")
    rows = [r[0] for r in cur.fetchall()]
    for cand in ['itemNotes', 'notes', 'itemNotesCombined', 'itemNotesCombinedLatest']:
        if cand in rows:
            return cand
    return rows[0] if rows else None


def get_note_text(conn, notes_table, parent_item_id):
    if not notes_table:
        return None
    cols = [r['name'] for r in sqlite_rows(conn, f"PRAGMA table_info('{notes_table}')")]
    possible = [c for c in cols if c.lower() in ('note', 'content', 'note_text', 'text')]
    if not possible:
        col_types = [(r['name'], r.get('type', '').lower()) for r in sqlite_rows(conn, f"PRAGMA table_info('{notes_table}')")]
        for name, ctype in col_types:
            if 'char' in ctype or 'text' in ctype or name.lower().endswith('content'):
                possible.append(name)
    if not possible:
        return None
    col = possible[0]
    q = f"SELECT {col} as note FROM {notes_table} WHERE itemID = ?"
    rows = list(sqlite_rows(conn, q, (parent_item_id,)))
    if not rows:
        return None
    texts = [r['note'] for r in rows if r.get('note')]
    return '\n\n'.join(texts) if texts else None


def normalize_language(token):
    if not token:
        return None
    t = str(token).strip().lower()
    if t in LANG_MAP:
        return LANG_MAP[t]
    if len(t) == 2:
        return t
    if t.startswith('eng'):
        return 'en'
    if t.startswith('de') or 'germ' in t:
        return 'de'
    if t.startswith('fr') or 'fran' in t:
        return 'fr'
    if t.startswith('es'):
        return 'es'
    return t


def prettify_xml(elem):
    rough = ET.tostring(elem, 'utf-8')
    try:
        from xml.dom import minidom
        reparsed = minidom.parseString(rough)
        return reparsed.toprettyxml(indent="  ")
    except Exception:
        return rough.decode('utf-8')


# --- New collection lookup helpers ---

def find_collection_tables(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%collection%';")
    return [r[0] for r in cur.fetchall()]


def inspect_table_columns(conn, table):
    return [r['name'] for r in sqlite_rows(conn, f"PRAGMA table_info('{table}')")]


def find_collection_table_and_columns(conn):
    # Return (collection_table, name_col, id_col) or (None, None, None)
    candidates = find_collection_tables(conn)
    for t in candidates:
        cols = inspect_table_columns(conn, t)
        lower = [c.lower() for c in cols]
        # find plausible name column
        name_col = None
        for c in ('name', 'collectionName', 'displayName'):
            if c in cols:
                name_col = c
                break
        if not name_col:
            for c in cols:
                if 'name' in c.lower():
                    name_col = c
                    break
        # find plausible id column
        id_col = None
        for c in ('collectionID', 'id', 'collection_id'):
            if c in cols:
                id_col = c
                break
        if not id_col:
            for c in cols:
                if 'id' in c.lower():
                    id_col = c
                    break
        if name_col and id_col:
            return t, name_col, id_col
    return None, None, None


def find_collection_item_mapping_table(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%collection%';")
    tables = [r[0] for r in cur.fetchall()]
    # find a table that also includes 'item' in the name
    for t in tables:
        if 'item' in t.lower():
            cols = inspect_table_columns(conn, t)
            low = [c.lower() for c in cols]
            if any('item' in c.lower() for c in cols) and any('collection' in c.lower() for c in cols):
                return t
    # fallback: search all tables for a mapping pattern
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    all_tables = [r[0] for r in cur.fetchall()]
    for t in all_tables:
        cols = inspect_table_columns(conn, t)
        if any('item' in c.lower() for c in cols) and any('collection' in c.lower() for c in cols):
            return t
    return None


def get_itemIDs_for_collection(conn, collection_name):
    ctable, name_col, id_col = find_collection_table_and_columns(conn)
    if not ctable:
        raise RuntimeError('No collection table found in DB')
    # find matching collection rows (case-insensitive match)
    cur = conn.cursor()
    q = f"SELECT {id_col} FROM {ctable} WHERE lower({name_col}) = lower(?)"
    cur.execute(q, (collection_name,))
    rows = cur.fetchall()
    if not rows:
        # try LIKE
        q2 = f"SELECT {id_col} FROM {ctable} WHERE lower({name_col}) LIKE lower(?)"
        cur.execute(q2, (f"%{collection_name}%",))
        rows = cur.fetchall()
        if not rows:
            raise RuntimeError(f'Collection named "{collection_name}" not found in table {ctable}')
    collection_ids = [r[0] for r in rows]
    mapping_table = find_collection_item_mapping_table(conn)
    if not mapping_table:
        raise RuntimeError('No collection->item mapping table found in DB')
    # determine column names in mapping table
    cols = inspect_table_columns(conn, mapping_table)
    col_lower = [c.lower() for c in cols]
    # possible names for item and collection columns
    item_col = None
    coll_col = None
    for c in cols:
        lc = c.lower()
        if 'item' in lc and 'id' in lc:
            item_col = c
        if 'collection' in lc and 'id' in lc:
            coll_col = c
    # fallback heuristics
    if not item_col:
        for c in cols:
            if 'item' in c.lower():
                item_col = c
                break
    if not coll_col:
        for c in cols:
            if 'collection' in c.lower():
                coll_col = c
                break
    if not item_col or not coll_col:
        # try common names
        for c in ('itemID','item_id','itemid'):
            if c in col_lower:
                item_col = cols[col_lower.index(c)]
        for c in ('collectionID','collection_id','collectionid'):
            if c in col_lower:
                coll_col = cols[col_lower.index(c)]
    if not item_col or not coll_col:
        raise RuntimeError(f'Could not identify item/collection columns in {mapping_table} (columns: {cols})')
    # gather itemIDs for all matching collection_ids
    item_ids = []
    for cid in collection_ids:
        q3 = f"SELECT {item_col} FROM {mapping_table} WHERE {coll_col} = ?"
        for r in sqlite_rows(conn, q3, (cid,)):
            # r is dict
            val = list(r.values())[0]
            item_ids.append(val)
    # deduplicate and preserve order
    seen = set()
    ordered = []
    for x in item_ids:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered


# --- End collection helpers ---


def main():
    p = argparse.ArgumentParser(description='Jurism -> Zotero exporter (collection-aware)')
    p.add_argument('--db', default='jurism.sqlite', help='Path to jurism.sqlite')
    p.add_argument('--out', default='zotero_import.rdf', help='Output RDF filename')
    p.add_argument('--json-out', default='items.json', help='Output JSON metadata file (use /dev/null to skip)')
    p.add_argument('--attachments-dir', default='attachments_export', help='Directory used only for legacy copying (ignored for linked/storage files)')
    p.add_argument('--linked-base', default=None, help='Linked attachment base directory (overrides prefs.js)')
    p.add_argument('--data-dir', default=None, help='Jurism data directory (overrides prefs.js)')
    p.add_argument('--limit', type=int, default=20, help='Max number of items to export (0 = all)')
    p.add_argument('--collection-name', default=None, help='Export items in the collection with this name (exact or substring match)')
    p.add_argument('--rebase-old', default=None, help='If specified, rebase absolute paths from this old base to linked-base')
    p.add_argument('--drop-missing', action='store_true', help='Do not include missing snapshot attachments in RDF (default: include as note in stats)')
    p.add_argument('--verbose-attachments', action='store_true', help='Print one-line log per attachment processed')
    args = p.parse_args()

    log('Starting export')

    prefs_path = find_prefs()
    prefs_linked, prefs_data = parse_prefs(prefs_path)

    linked_base = args.linked_base or prefs_linked or DEFAULT_LINKED_BASE
    data_dir = args.data_dir or prefs_data or DEFAULT_DATA_DIR

    log(f'Using linked_base: {linked_base}')
    log(f'Using data_dir: {data_dir}')
    if prefs_path:
        log(f'Detected prefs.js: {prefs_path}')

    storage_dir = detect_storage_folder(data_dir)
    if storage_dir:
        log(f'Detected storage dir: {storage_dir}')
    else:
        log(f'Warning: storage dir not found at {os.path.join(data_dir, "storage")}')

    storage_index = build_storage_index(storage_dir) if storage_dir else {}
    log(f'Storage index entries: {sum(len(v) for v in storage_index.values())}')

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # map itemTypes
    item_types = {r['itemTypeID']: r['typeName'] for r in sqlite_rows(conn, "SELECT itemTypeID, typeName FROM itemTypes")}

    # determine which itemTypeIDs are attachments/notes
    attachment_type_ids = [k for k, v in item_types.items() if v == 'attachment']
    note_type_ids = [k for k, v in item_types.items() if v == 'note']

    # build item list either from collection or from general items query
    items = []
    if args.collection_name:
        try:
            ids = get_itemIDs_for_collection(conn, args.collection_name)
            if not ids:
                log(f'Collection "{args.collection_name}" found but contains no items')
            else:
                # fetch item rows by itemID preserving order
                for iid in ids:
                    row = list(sqlite_rows(conn, 'SELECT itemID, itemTypeID, key FROM items WHERE itemID = ?', (iid,)))
                    if row:
                        # filter out attachment/note items
                        if row[0]['itemTypeID'] in (attachment_type_ids + note_type_ids):
                            continue
                        items.append(row[0])
        except Exception as e:
            log(f'Error locating collection: {e}')
            log('Falling back to default item selection')

    if not args.collection_name or not items:
        q = "SELECT itemID, itemTypeID, key FROM items WHERE itemTypeID IS NOT NULL"
        if attachment_type_ids or note_type_ids:
            exclude_ids = attachment_type_ids + note_type_ids
            if exclude_ids:
                q += " AND itemTypeID NOT IN ({})".format(','.join(str(x) for x in exclude_ids))
        q += " ORDER BY itemID"
        if args.limit > 0:
            q += " LIMIT %d" % args.limit
        items = [r for r in sqlite_rows(conn, q)]

    log(f'Items to export: {len(items)}')

    # creators
    creators = {r['creatorID']: {'firstName': r['firstName'], 'lastName': r['lastName'], 'fieldMode': r['fieldMode']} for r in sqlite_rows(conn, 'SELECT creatorID, firstName, lastName, fieldMode FROM creators')}

    item_creators = {}
    for r in sqlite_rows(conn, 'SELECT itemID, creatorID, creatorTypeID, orderIndex FROM itemCreators ORDER BY itemID, orderIndex'):
        item_creators.setdefault(r['itemID'], []).append(r)

    # attachments table
    attachments_rows = [r for r in sqlite_rows(conn, 'SELECT itemID, parentItemID, linkMode, contentType, path, storageModTime, storageHash FROM itemAttachments')]

    # build attachments_by_parent map from DB (we will resolve to file:// URIs but not copy)
    attachments_by_parent = {}
    missing_snapshots = []

    for a in attachments_rows:
        parent = a['parentItemID'] if a['parentItemID'] is not None else None
        attachments_by_parent.setdefault(parent, []).append(a)

    exported = []

    # utility: get item dateAdded if present
    def get_date_added(item_id):
        row = list(sqlite_rows(conn, 'SELECT dateAdded FROM items WHERE itemID = ?', (item_id,)))
        if row and row[0].get('dateAdded'):
            return row[0]['dateAdded']
        return None

    # find notes table if present
    notes_table = find_notes_table(conn)
    if notes_table:
        log(f'Notes table detected: {notes_table}')

    # process each item
    for it in items:
        iid = it['itemID']
        # load item fields
        rows = list(sqlite_rows(conn, 'SELECT d.fieldID, f.fieldName, v.value FROM itemData d JOIN itemDataValues v ON d.valueID = v.valueID JOIN fieldsCombined f ON d.fieldID = f.fieldID WHERE d.itemID = ?', (iid,)))
        fields = {r['fieldName']: r['value'] for r in rows}

        # alternates (for CNE)
        alt_rows = list(sqlite_rows(conn, 'SELECT d.fieldID, f.fieldName, d.languageTag, v.value FROM itemDataAlt d JOIN itemDataValues v ON d.valueID = v.valueID JOIN fields f ON d.fieldID = f.fieldID WHERE d.itemID = ?', (iid,)))
        alts = {}
        for r in alt_rows:
            alts.setdefault(r['fieldName'], []).append({'lang': r['languageTag'], 'value': r['value']})

        # creators
        clist = []
        for c in item_creators.get(iid, []):
            cid = c['creatorID']
            cr = creators.get(cid)
            if cr:
                clist.append(cr)

        # attachments: resolve paths to absolute file URIs but do NOT copy files
        atts = []
        for a in attachments_by_parent.get(iid, []):
            path = a['path']
            resolved_abs = None
            kind = None
            if path:
                if path.startswith('storage:'):
                    # resolve using storage index
                    resolved = resolve_storage_with_index(storage_index, path)
                    if resolved:
                        resolved_abs = resolved
                        kind = 'storage'
                    else:
                        kind = 'storage-missing'
                elif path.startswith('attachments:'):
                    tail = path.split(':', 1)[1]
                    resolved_abs = os.path.join(linked_base, tail)
                    kind = 'linked'
                elif path.startswith('/'):
                    # absolute path in DB; attempt to rebase to linked_base if requested
                    if args.rebase_old:
                        rebased = None
                        if path.startswith(args.rebase_old):
                            tail = path[len(args.rebase_old):]
                            if tail.startswith('/'):
                                tail = tail[1:]
                            rebased = os.path.join(linked_base, tail)
                        if rebased:
                            resolved_abs = rebased
                            kind = 'absolute_rebased'
                        else:
                            resolved_abs = path
                            kind = 'absolute'
                    else:
                        resolved_abs = path
                        kind = 'absolute'
                else:
                    # assume linked-base relative
                    resolved_abs = os.path.join(linked_base, path)
                    kind = 'assumed_linked'
            else:
                # path is NULL in DB (likely a snapshot stored in storage)
                # attempt to find a reasonable match in storage_index
                possible = None
                parent_key_row = list(sqlite_rows(conn, 'SELECT key FROM items WHERE itemID = ?', (iid,)))
                parent_key = parent_key_row[0]['key'] if parent_key_row else None
                if parent_key:
                    for fn in storage_index:
                        if parent_key.lower() in fn:
                            possible = storage_index[fn][0]
                            break
                if not possible and fields.get('title'):
                    title_basename = re.sub(r'[^0-9a-zA-Z]+', ' ', fields.get('title')).strip().lower().split()
                    for fn in storage_index:
                        hits = sum(1 for w in title_basename if w and w in fn)
                        if hits >= 3:
                            possible = storage_index[fn][0]
                            break
                if possible:
                    resolved_abs = possible
                    kind = 'snapshot_found'
                else:
                    kind = 'snapshot_missing'
                    missing_snapshots.append({'parent': iid, 'attachItemID': a['itemID']})

            file_uri = None
            if resolved_abs:
                if not os.path.isabs(resolved_abs):
                    resolved_abs = os.path.abspath(resolved_abs)
                file_uri = 'file://' + resolved_abs

            atts.append({'attachItemID': a['itemID'], 'path': path, 'resolved': resolved_abs, 'file_uri': file_uri, 'kind': kind, 'contentType': a['contentType']})

            if args.verbose_attachments:
                log(f"ATTACH parent={iid} attach={a['itemID']} db_path={path!s} kind={kind} resolved={resolved_abs!s}")

        # build cne extra lines
        extra_lines = []
        if fields.get('extra'):
            extra_lines.append(fields.get('extra'))
        for field in ('title', 'bookTitle', 'publicationTitle'):
            if field in alts:
                for alt in alts[field]:
                    if alt['lang'] and alt['lang'].startswith('en'):
                        if field == 'title':
                            extra_lines.append(f"cne-title-english: {alt['value']}")
                        else:
                            extra_lines.append(f"cne-container-title-english: {alt['value']}")
                        break

        exported.append({
            'itemID': iid,
            'key': it.get('key'),
            'type': item_types.get(it['itemTypeID']),
            'fields': fields,
            'creators': clist,
            'attachments': atts,
            'extra_combined': '\n'.join(extra_lines),
            'dateAdded': get_date_added(iid),
        })

    if args.json_out and args.json_out != '/dev/null':
        with open(args.json_out, 'w', encoding='utf-8') as jf:
            json.dump(exported, jf, ensure_ascii=False, indent=2)
        log(f'Wrote {args.json_out}')

    NS = {
        'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'z': 'http://www.zotero.org/namespaces/export#'
    }
    ET.register_namespace('rdf', NS['rdf'])
    ET.register_namespace('dc', NS['dc'])
    ET.register_namespace('z', NS['z'])

    rdf = ET.Element('{%s}RDF' % NS['rdf'])

    for it in exported:
        item_el = ET.SubElement(rdf, '{%s}item' % NS['z'], {'{%s}about' % NS['rdf']: 'urn:uuid:' + str(uuid.uuid4())})
        itype = ET.SubElement(item_el, '{%s}itemType' % NS['z'])
        itype.text = it.get('type') or 'document'
        if it['fields'].get('title'):
            t = ET.SubElement(item_el, '{%s}title' % NS['z'])
            t.text = it['fields'].get('title')
        if it['creators']:
            for c in it['creators']:
                creator_el = ET.SubElement(item_el, '{%s}creator' % NS['z'])
                if isinstance(c, dict):
                    if c.get('lastName'):
                        ln = ET.SubElement(creator_el, '{%s}lastName' % NS['z'])
                        ln.text = c.get('lastName')
                    if c.get('firstName'):
                        gn = ET.SubElement(creator_el, '{%s}firstName' % NS['z'])
                        gn.text = c.get('firstName')
                else:
                    creator_el.text = str(c)
        lang_val = it['fields'].get('language')
        norm_lang = normalize_language(lang_val)
        if norm_lang:
            lang_el = ET.SubElement(item_el, '{%s}language' % NS['z'])
            lang_el.text = norm_lang
        if it['fields'].get('abstractNote'):
            abs_el = ET.SubElement(item_el, '{%s}abstractNote' % NS['z'])
            abs_el.text = it['fields'].get('abstractNote')
        if it.get('dateAdded'):
            da_el = ET.SubElement(item_el, '{%s}dateAdded' % NS['z'])
            da_el.text = it.get('dateAdded')
        if it['fields'].get('publisher'):
            pub_el = ET.SubElement(item_el, '{%s}publisher' % NS['z'])
            pub_el.text = it['fields'].get('publisher')
        if it['fields'].get('date'):
            date_el = ET.SubElement(item_el, '{%s}date' % NS['z'])
            date_el.text = it['fields'].get('date')
        if it['fields'].get('ISBN'):
            isbn_el = ET.SubElement(item_el, '{%s}ISBN' % NS['z'])
            isbn_el.text = it['fields'].get('ISBN')
        if it['fields'].get('pages'):
            pages_el = ET.SubElement(item_el, '{%s}pages' % NS['z'])
            pages_el.text = it['fields'].get('pages')
        container = it['fields'].get('publicationTitle') or it['fields'].get('bookTitle')
        if container:
            cont_el = ET.SubElement(item_el, '{%s}publicationTitle' % NS['z'])
            cont_el.text = container
        if it.get('extra_combined'):
            extra_el = ET.SubElement(item_el, '{%s}extra' % NS['z'])
            extra_el.text = it.get('extra_combined')
        if notes_table:
            note_text = get_note_text(conn, notes_table, it['itemID'])
            if note_text:
                note_el = ET.SubElement(item_el, '{%s}note' % NS['z'])
                note_el.text = note_text
        if it['attachments']:
            for a in it['attachments']:
                uri = a.get('file_uri')
                if uri:
                    att_el = ET.SubElement(item_el, '{%s}attachment' % NS['z'], {'{%s}resource' % NS['rdf']: uri})
                else:
                    if a.get('kind') and a.get('kind').startswith('snapshot'):
                        note_el = ET.SubElement(item_el, '{%s}note' % NS['z'])
                        note_el.text = f"Snapshot missing for parent {it['itemID']} attach {a.get('attachItemID')}"
                    else:
                        att_el = ET.SubElement(item_el, '{%s}attachment' % NS['z'])
                        att_el.text = str(a.get('path') or '')

    with open(args.out, 'w', encoding='utf-8') as rf:
        xmlstr = prettify_xml(rdf)
        rf.write(xmlstr)
    log(f'Wrote {args.out}')

    stats = {
        'items_exported': len(exported),
        'attachments_indexed': sum(len(v) for v in storage_index.values()),
        'missing_snapshots_count': len(missing_snapshots),
        'missing_snapshots_sample': missing_snapshots[:50]
    }
    with open('stats.txt', 'w', encoding='utf-8') as sf:
        json.dump(stats, sf, indent=2)
    log('Wrote stats.txt')
    log('Done.')


if __name__ == '__main__':
    main()
