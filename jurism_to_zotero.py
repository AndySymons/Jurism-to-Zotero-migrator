#!/usr/bin/env python3
"""
jurism_to_zotero.py

Prototype exporter: reads a Jurism (Zotero-derived) SQLite DB and extracts
bibliographic items (excluding attachment/note top-level items), creators,
attachments and alternate-language title/container values.

Outputs:
 - items.json (metadata + resolved attachment paths)
 - items.rdf  (best-effort Zotero-friendly RDF export; prototype)
 - copies storage: files into attachments_dir
 - writes a log file stats.txt

Usage (example):
  python3 jurism_to_zotero.py --db jurism.sqlite --out out.rdf --attachments-dir attachments_export --limit 50

The script tries to autodetect prefs.js at:
  ~/Library/Application Support/Jurism/Profiles/*/prefs.js
and extract:
  user_pref("extensions.zotero.baseAttachmentPath", "...")  # linked base
  user_pref("extensions.zotero.dataDir", "...")            # data dir

If prefs.js is not found, the script uses CLI-provided values or fallbacks:
  linked_base default: /Volumes/X_Drive/Zotero linked attachments
  data_dir default: ~/Jurism

This is a prototype and conservative: it WILL NOT MODIFY your DB.
It logs what it cannot find and produces a best-effort RDF and a JSON export.

"""

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom


DEFAULT_LINKED_BASE = "/Volumes/X_Drive/Zotero linked attachments"
DEFAULT_DATA_DIR = os.path.expanduser("~/Jurism")
PREFS_GLOB = os.path.expanduser("~/Library/Application Support/Jurism/Profiles/*/prefs.js")


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
    # storage directory is <data_dir>/storage
    s = Path(data_dir) / "storage"
    if not s.exists():
        return None
    return s


def resolve_storage_file(storage_dir, path_value):
    # path_value might be like storage:foo.pdf or storage:subdir/foo.pdf
    # Jurism storage uses 8-char subfolders under storage; we search for basename
    if not storage_dir or not storage_dir.exists():
        return None
    # remove prefix up to ':' if present
    if ':' in path_value:
        _, tail = path_value.split(':', 1)
    else:
        tail = path_value
    basename = os.path.basename(tail)
    # search for basename under storage_dir (one level deep)
    for child in storage_dir.iterdir():
        if child.is_dir():
            candidate = child / basename
            if candidate.exists():
                return str(candidate)
    return None


def rebase_absolute_path(old_path, old_base, new_base):
    if not old_path:
        return None
    if old_base and old_path.startswith(old_base):
        rel = old_path[len(old_base):]
        if rel.startswith('/'):
            rel = rel[1:]
        return os.path.join(new_base, rel)
    # if no old_base provided but path looks like /Users/..., rebase by replacing /Users/... prefix with new_base
    # allow best-effort: get the tail after the last path component 'JurisM linked attachments' if present
    if old_path.startswith('/'):
        # try to find 'JurisM linked attachments' in the path
        marker = 'JurisM linked attachments'
        if marker in old_path:
            idx = old_path.index(marker)
            tail = old_path[idx + len(marker):]
            if tail.startswith('/'):
                tail = tail[1:]
            return os.path.join(new_base, tail)
        # fallback: take basename
        return os.path.join(new_base, os.path.basename(old_path))
    return None


def prettify_xml(elem):
    rough = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ")


def main():
    p = argparse.ArgumentParser(description='Jurism -> Zotero prototype exporter')
    p.add_argument('--db', default='jurism.sqlite', help='Path to jurism.sqlite')
    p.add_argument('--out', default='items.rdf', help='Output RDF filename (prototype)')
    p.add_argument('--json-out', default='items.json', help='Output JSON metadata file')
    p.add_argument('--attachments-dir', default='attachments_export', help='Directory where storage files will be copied')
    p.add_argument('--linked-base', default=None, help='Linked attachment base directory (overrides prefs.js)')
    p.add_argument('--data-dir', default=None, help='Jurism data directory (overrides prefs.js)')
    p.add_argument('--limit', type=int, default=20, help='Max number of items to export (0 = all)')
    p.add_argument('--rebase-old', default=None, help='If specified, rebase absolute paths from this old base to linked-base')
    p.add_argument('--drop-missing', action='store_true', help='Do not copy missing storage files (default: log only)')
    args = p.parse_args()

    # detect prefs
    prefs_path = find_prefs()
    prefs_linked, prefs_data = parse_prefs(prefs_path)

    linked_base = args.linked_base or prefs_linked or DEFAULT_LINKED_BASE
    data_dir = args.data_dir or prefs_data or DEFAULT_DATA_DIR

    print('Using linked_base:', linked_base)
    print('Using data_dir:', data_dir)
    if prefs_path:
        print('Detected prefs.js:', prefs_path)

    storage_dir = detect_storage_folder(data_dir)
    if storage_dir:
        print('Detected storage dir:', storage_dir)
    else:
        print('Warning: storage dir not found at', os.path.join(data_dir, 'storage'))

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # map itemTypes
    item_types = {}
    for r in sqlite_rows(conn, "SELECT itemTypeID, typeName FROM itemTypes"):
        item_types[r['itemTypeID']] = r['typeName']

    # find itemTypeIDs for attachment and note
    attachment_type_ids = [k for k,v in item_types.items() if v == 'attachment']
    note_type_ids = [k for k,v in item_types.items() if v == 'note']

    # items to export: exclude attachment & note
    q = "SELECT itemID, itemTypeID, key FROM items WHERE itemTypeID IS NOT NULL"
    if attachment_type_ids or note_type_ids:
        exclude_ids = attachment_type_ids + note_type_ids
        if exclude_ids:
            q += " AND itemTypeID NOT IN ({})".format(','.join(str(x) for x in exclude_ids))
    q += " ORDER BY itemID"
    if args.limit > 0:
        q += " LIMIT %d" % args.limit

    items = []
    for r in sqlite_rows(conn, q):
        items.append({'itemID': r['itemID'], 'itemTypeID': r['itemTypeID'], 'key': r['key']})

    print('Items to export:', len(items))

    # load fieldsCombined mapping
    fields_map = {r['fieldID']: r['fieldName'] for r in sqlite_rows(conn, 'SELECT fieldID, fieldName FROM fieldsCombined')}

    # load creators mapping
    creators = {r['creatorID']: {'firstName': r['firstName'], 'lastName': r['lastName'], 'fieldMode': r['fieldMode']} for r in sqlite_rows(conn, 'PRAGMA table_info("creators")') if False}
    # actual creators
    creators = {r['creatorID']: {'firstName': r['firstName'], 'lastName': r['lastName'], 'fieldMode': r['fieldMode']} for r in sqlite_rows(conn, 'SELECT creatorID, firstName, lastName, fieldMode FROM creators')}

    # load itemCreators (links)
    item_creators = {}
    for r in sqlite_rows(conn, 'SELECT itemID, creatorID, creatorTypeID, orderIndex FROM itemCreators ORDER BY itemID, orderIndex'):
        item_creators.setdefault(r['itemID'], []).append({'creatorID': r['creatorID'], 'creatorTypeID': r['creatorTypeID'], 'orderIndex': r['orderIndex']})

    # attachments: load all and attempt to resolve
    attachments_rows = [r for r in sqlite_rows(conn, 'SELECT itemID, parentItemID, linkMode, contentType, path, storageModTime, storageHash FROM itemAttachments')]

    attachments_by_parent = {}
    missing_files = []
    copied_files = []
    os.makedirs(args.attachments_dir, exist_ok=True)

    for a in attachments_rows:
        itemID = a['itemID']
        parent = a['parentItemID'] if a['parentItemID'] is not None else None
        path = a['path']
        resolved = None
        kind = 'unknown'
        if path:
            if path.startswith('storage:'):
                resolved = resolve_storage_file(Path(storage_dir) if storage_dir else None, path)
                kind = 'storage'
                if resolved:
                    # copy to attachments_dir
                    dst_name = os.path.basename(resolved)
                    dst = Path(args.attachments_dir) / dst_name
                    try:
                        shutil.copy2(resolved, dst)
                        copied_files.append(str(dst))
                    except Exception:
                        missing_files.append(resolved)
                        resolved = None
                else:
                    missing_files.append(path)
            elif path.startswith('attachments:'):
                tail = path.split(':',1)[1]
                # map to linked base
                resolved = os.path.join(linked_base, tail)
                kind = 'attachments'
            elif path.startswith('/'):
                # absolute path - rebase to linked_base
                resolved = rebase_absolute_path(path, args.rebase_old, linked_base)
                kind = 'absolute_rebased'
            else:
                # other forms
                resolved = os.path.join(linked_base, path)
                kind = 'assumed_linked'
        else:
            resolved = None
            kind = 'no_path'
        # attach to parent if present; otherwise skip (log)
        if parent:
            attachments_by_parent.setdefault(parent, []).append({'itemID': itemID, 'path': path, 'resolved': resolved, 'kind': kind, 'contentType': a['contentType']})
        else:
            # some attachments have no parent; log under special key None
            attachments_by_parent.setdefault(None, []).append({'itemID': itemID, 'path': path, 'resolved': resolved, 'kind': kind, 'contentType': a['contentType']})

    # itemData values
    def item_values(item_id):
        rows = list(sqlite_rows(conn, 'SELECT d.fieldID, f.fieldName, v.value FROM itemData d JOIN itemDataValues v ON d.valueID = v.valueID JOIN fieldsCombined f ON d.fieldID = f.fieldID WHERE d.itemID = ?', (item_id,)))
        return {r['fieldName']: r['value'] for r in rows}

    # itemDataAlt (language alternates)
    def item_alt_values(item_id):
        rows = list(sqlite_rows(conn, 'SELECT d.fieldID, f.fieldName, d.languageTag, v.value FROM itemDataAlt d JOIN itemDataValues v ON d.valueID = v.valueID JOIN fields f ON d.fieldID = f.fieldID WHERE d.itemID = ?', (item_id,)))
        # map by fieldName -> list of (lang, value)
        out = {}
        for r in rows:
            out.setdefault(r['fieldName'], []).append({'lang': r['languageTag'], 'value': r['value']})
        return out

    exported = []
    for it in items:
        iid = it['itemID']
        vals = item_values(iid)
        alts = item_alt_values(iid)
        # creators
        creators_list = []
        for c in item_creators.get(iid, []):
            cid = c['creatorID']
            cr = creators.get(cid)
            if cr:
                name = ''
                if cr.get('firstName') and cr.get('lastName'):
                    name = f"{cr.get('firstName')} {cr.get('lastName')}"
                elif cr.get('lastName'):
                    name = cr.get('lastName')
                elif cr.get('firstName'):
                    name = cr.get('firstName')
                creators_list.append(name)

        # attachments attached to this parent
        atts = attachments_by_parent.get(iid, [])
        # build CNE extra tags (only for title and container/bookTitle)
        extra_lines = []
        # existing extra
        if vals.get('extra'):
            extra_lines.append(vals.get('extra'))
        # add cne tags for english alternates if present
        for field in ('title', 'bookTitle', 'publicationTitle'):
            if field in alts:
                for alt in alts[field]:
                    if alt['lang'].startswith('en') or alt['lang'] == 'en':
                        # cne-title-english or cne-container-title-english
                        if field == 'title':
                            extra_lines.append(f"cne-title-english: {alt['value']}")
                        else:
                            extra_lines.append(f"cne-container-title-english: {alt['value']}")
                        break
        item_out = {
            'itemID': iid,
            'key': it.get('key'),
            'type': item_types.get(it['itemTypeID']),
            'fields': vals,
            'creators': creators_list,
            'attachments': atts,
            'extra_combined': '\n'.join(extra_lines)
        }
        exported.append(item_out)

    # write JSON
    with open(args.json_out, 'w', encoding='utf-8') as jf:
        json.dump(exported, jf, ensure_ascii=False, indent=2)
    print('Wrote', args.json_out)

    # write a very simple RDF (Dublin Core based) as a prototype for Zotero
    rdf = ET.Element('rdf:RDF', {
        'xmlns:rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
        'xmlns:dc': 'http://purl.org/dc/elements/1.1/'
    })

    for it in exported:
        desc = ET.SubElement(rdf, 'rdf:Description', {'rdf:about': 'urn:uuid:' + str(uuid.uuid4())})
        f = it['fields']
        if 'title' in f:
            t = ET.SubElement(desc, 'dc:title')
            t.text = str(f.get('title'))
        # creators as dc:creator entries (joined)
        if it['creators']:
            for c in it['creators']:
                c_el = ET.SubElement(desc, 'dc:creator')
                c_el.text = c
        # container / bookTitle / publicationTitle
        cont = f.get('publicationTitle') or f.get('bookTitle')
        if cont:
            el = ET.SubElement(desc, 'dc:source')
            el.text = str(cont)
        if 'date' in f:
            el = ET.SubElement(desc, 'dc:date')
            el.text = str(f.get('date'))
        if 'publisher' in f:
            el = ET.SubElement(desc, 'dc:publisher')
            el.text = str(f.get('publisher'))
        if 'ISBN' in f:
            el = ET.SubElement(desc, 'dc:identifier')
            el.text = str(f.get('ISBN'))
        if it['attachments']:
            for a in it['attachments']:
                a_el = ET.SubElement(desc, 'dc:relation')
                a_el.text = str(a.get('resolved') or a.get('path') or '')
        if it.get('extra_combined'):
            el = ET.SubElement(desc, 'dc:description')
            el.text = it['extra_combined']

    with open(args.out, 'w', encoding='utf-8') as rf:
        xmlstr = prettify_xml(rdf)
        rf.write(xmlstr)
    print('Wrote', args.out)

    # stats
    stats = {
        'items_exported': len(exported),
        'attachments_copied': len(copied_files),
        'missing_files_count': len(missing_files),
        'missing_files_sample': missing_files[:50]
    }
    with open('stats.txt', 'w', encoding='utf-8') as sf:
        json.dump(stats, sf, indent=2)
    print('Wrote stats.txt')
    print('Done. Review items.json,', args.out, 'and stats.txt')


if __name__ == '__main__':
    main()
