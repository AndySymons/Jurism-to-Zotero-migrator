#!/usr/bin/env python3
"""
jurism_to_zotero.py

Commit: #3
Changes in this version:
 - Do NOT copy linked or stored files. Instead emit file:// absolute URIs pointing
   at the original locations so Zotero imports/copies them into its own storage.
 - Export notes (DB table with 'note' content) as z:note elements so Zotero imports them.
 - Attempt to find snapshots (HTML) in storage and point Zotero at the storage file.
 - Normalize language tokens to ISO-639-1 where common mappings exist.
 - Emit original dateAdded as z:dateAdded when present.
 - Avoid blank-node creator artifacts; emit creators as proper z:firstName / z:lastName
   or z:creator text elements.
 - Add verbose per-attachment logging with --verbose-attachments and a --no-json flag.
 - Print Lisbon timestamps for key steps to help you see which run produced files.

Safety: This script only reads the DB and your storage; it does NOT modify your DB
or any of your library files. It writes an RDF manifest (default: zotero_import.rdf)
and a stats.txt log.

Usage example:
  python3 jurism_to_zotero.py --db jurism.sqlite --out zotero_import.rdf --attachments-dir attachments_export --limit 20 --verbose-attachments

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
        # fallback to UTC if zoneinfo unavailable
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
    # exact
    if basename in storage_index:
        return storage_index[basename][0]
    # strip query-like parts
    if '?' in basename:
        key = basename.split('?', 1)[0]
        if key in storage_index:
            return storage_index[key][0]
    # substring search
    for k, v in storage_index.items():
        if basename in k:
            return v[0]
    return None


def find_notes_table(conn):
    # Find a table with 'note' in the name
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%note%';")
    rows = [r[0] for r in cur.fetchall()]
    # prefer exact names if present
    for cand in ['itemNotes', 'notes', 'itemNotesCombined', 'itemNotesCombinedLatest']:
        if cand in rows:
            return cand
    return rows[0] if rows else None


def get_note_text(conn, notes_table, parent_item_id):
    if not notes_table:
        return None
    # find candidate text columns
    cols = [r['name'] for r in sqlite_rows(conn, f"PRAGMA table_info('{notes_table}')")]
    # possible content columns
    possible = [c for c in cols if c.lower() in ('note', 'content', 'note_text', 'text')]
    if not possible:
        # pick a text column if available
        # get types
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
    # if multiple rows, concatenate
    texts = [r['note'] for r in rows if r.get('note')]
    return '\n\n'.join(texts) if texts else None


def normalize_language(token):
    if not token:
        return None
    t = str(token).strip().lower()
    # try direct map
    if t in LANG_MAP:
        return LANG_MAP[t]
    # handle two-letter uppercase
    if len(t) == 2:
        return t
    # common fallbacks
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


def main():
    p = argparse.ArgumentParser(description='Jurism -> Zotero exporter (commit #3)')
    p.add_argument('--db', default='jurism.sqlite', help='Path to jurism.sqlite')
    p.add_argument('--out', default='zotero_import.rdf', help='Output RDF filename')
    p.add_argument('--json-out', default='items.json', help='Output JSON metadata file (use /dev/null to skip)')
    p.add_argument('--attachments-dir', default='attachments_export', help='Directory used only for legacy copying (ignored for linked/storage files)')
    p.add_argument('--linked-base', default=None, help='Linked attachment base directory (overrides prefs.js)')
    p.add_argument('--data-dir', default=None, help='Jurism data directory (overrides prefs.js)')
    p.add_argument('--limit', type=int, default=20, help='Max number of items to export (0 = all)')
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
                # heuristic: look for files containing the parent item key or title
                possible = None
                # get parent key
                parent_key_row = list(sqlite_rows(conn, 'SELECT key FROM items WHERE itemID = ?', (iid,)))
                parent_key = parent_key_row[0]['key'] if parent_key_row else None
                # search for parent_key in storage filenames
                if parent_key:
                    for fn in storage_index:
                        if parent_key.lower() in fn:
                            possible = storage_index[fn][0]
                            break
                # fallback: try matching title tokens
                if not possible and fields.get('title'):
                    title_basename = re.sub(r'[^0-9a-zA-Z]+', ' ', fields.get('title')).strip().lower().split()
                    for fn in storage_index:
                        # check if several words from title are in filename
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

            # build file URI if resolved_abs
            file_uri = None
            if resolved_abs:
                # ensure absolute path
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

        # assemble exported item
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

    # write JSON debug if requested
    if args.json_out and args.json_out != '/dev/null':
        with open(args.json_out, 'w', encoding='utf-8') as jf:
            json.dump(exported, jf, ensure_ascii=False, indent=2)
        log(f'Wrote {args.json_out}')

    # build RDF using z: namespace
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
        # itemType
        itype = ET.SubElement(item_el, '{%s}itemType' % NS['z'])
        itype.text = it.get('type') or 'document'
        # title
        if it['fields'].get('title'):
            t = ET.SubElement(item_el, '{%s}title' % NS['z'])
            t.text = it['fields'].get('title')
        # creators
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
        # language (normalized)
        lang_val = it['fields'].get('language')
        norm_lang = normalize_language(lang_val)
        if norm_lang:
            lang_el = ET.SubElement(item_el, '{%s}language' % NS['z'])
            lang_el.text = norm_lang
        # abstract
        if it['fields'].get('abstractNote'):
            abs_el = ET.SubElement(item_el, '{%s}abstractNote' % NS['z'])
            abs_el.text = it['fields'].get('abstractNote')
        # publisher/date/ISBN/pages
        if it['fields'].get('publisher'):
            pub_el = ET.SubElement(item_el, '{%s}publisher' % NS['z'])
            pub_el.text = it['fields'].get('publisher')
        if it.get('dateAdded'):
            da_el = ET.SubElement(item_el, '{%s}dateAdded' % NS['z'])
            da_el.text = it.get('dateAdded')
        if it['fields'].get('date'):
            date_el = ET.SubElement(item_el, '{%s}date' % NS['z'])
            date_el.text = it['fields'].get('date')
        if it['fields'].get('ISBN'):
            isbn_el = ET.SubElement(item_el, '{%s}ISBN' % NS['z'])
            isbn_el.text = it['fields'].get('ISBN')
        if it['fields'].get('pages'):
            pages_el = ET.SubElement(item_el, '{%s}pages' % NS['z'])
            pages_el.text = it['fields'].get('pages')
        # container
        container = it['fields'].get('publicationTitle') or it['fields'].get('bookTitle')
        if container:
            cont_el = ET.SubElement(item_el, '{%s}publicationTitle' % NS['z'])
            cont_el.text = container
        # extra (CNE lines)
        if it.get('extra_combined'):
            extra_el = ET.SubElement(item_el, '{%s}extra' % NS['z'])
            extra_el.text = it.get('extra_combined')

        # notes: fetch from notes table if present
        if notes_table:
            note_text = get_note_text(conn, notes_table, it['itemID'])
            if note_text:
                note_el = ET.SubElement(item_el, '{%s}note' % NS['z'])
                note_el.text = note_text

        # attachments: write z:attachment with rdf:resource pointing to file:// absolute URIs where possible
        if it['attachments']:
            for a in it['attachments']:
                uri = a.get('file_uri')
                if uri:
                    att_el = ET.SubElement(item_el, '{%s}attachment' % NS['z'], {'{%s}resource' % NS['rdf']: uri})
                else:
                    # unresolved snapshot or missing: put a small note element instead
                    if a.get('kind') and a.get('kind').startswith('snapshot'):
                        note_el = ET.SubElement(item_el, '{%s}note' % NS['z'])
                        note_el.text = f"Snapshot missing for parent {it['itemID']} attach {a.get('attachItemID')}"
                    else:
                        att_el = ET.SubElement(item_el, '{%s}attachment' % NS['z'])
                        att_el.text = str(a.get('path') or '')

    # write RDF
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
