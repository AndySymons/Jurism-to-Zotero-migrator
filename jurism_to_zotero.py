#!/usr/bin/env python3
"""
jurism_to_zotero.py

Updated prototype exporter: improved Zotero RDF output (zotero-friendly tags),
Extra/CNE handling, language field, and attachment URIs.

Usage example:
  python3 jurism_to_zotero.py --db jurism.sqlite --out zotero_import.rdf --attachments-dir attachments_export --limit 20

Defaults:
 - linked_base read from prefs.js or defaults to /Volumes/X_Drive/Zotero linked attachments
 - data_dir read from prefs.js or defaults to ~/Jurism

This version changes RDF output to use the Zotero export namespace (z:)
and writes Extra into <z:extra> so Zotero imports CNE tags into the Extra field.
Stored attachments copied into attachments_dir are referenced as file:// URIs
so Zotero imports them as attachments. Linked attachments are written as
file:// URIs pointing to the linked-base.

"""

import argparse
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
    s = Path(data_dir) / "storage"
    if not s.exists():
        return None
    return s


def build_storage_index(storage_dir):
    """Build a mapping of lowercase basename -> full path for fast lookup."""
    index = {}
    if not storage_dir or not storage_dir.exists():
        return index
    for child in storage_dir.iterdir():
        if child.is_dir():
            for f in child.iterdir():
                if f.is_file():
                    index.setdefault(f.name.lower(), []).append(str(f))
    return index


def resolve_storage_file_with_index(storage_index, path_value):
    if not storage_index:
        return None
    if ':' in path_value:
        _, tail = path_value.split(':', 1)
    else:
        tail = path_value
    basename = os.path.basename(tail).lower()
    if basename in storage_index:
        # return the first match
        return storage_index[basename][0]
    # try more tolerant matching: strip query-like parts
    if '?' in basename:
        key = basename.split('?',1)[0]
        if key in storage_index:
            return storage_index[key][0]
    # fallback: substring search
    for k, v in storage_index.items():
        if basename in k:
            return v[0]
    return None


def rebase_absolute_path(old_path, old_base, new_base):
    if not old_path:
        return None
    if old_base and old_path.startswith(old_base):
        rel = old_path[len(old_base):]
        if rel.startswith('/'):
            rel = rel[1:]
        return os.path.join(new_base, rel)
    if old_path.startswith('/'):
        marker = 'JurisM linked attachments'
        if marker in old_path:
            idx = old_path.index(marker)
            tail = old_path[idx + len(marker):]
            if tail.startswith('/'):
                tail = tail[1:]
            return os.path.join(new_base, tail)
        return os.path.join(new_base, os.path.basename(old_path))
    return None


def prettify_xml(elem):
    rough = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ")


def main():
    p = argparse.ArgumentParser(description='Jurism -> Zotero prototype exporter')
    p.add_argument('--db', default='jurism.sqlite', help='Path to jurism.sqlite')
    p.add_argument('--out', default='zotero_import.rdf', help='Output RDF filename (prototype)')
    p.add_argument('--json-out', default='items.json', help='Output JSON metadata file')
    p.add_argument('--attachments-dir', default='attachments_export', help='Directory where storage files will be copied')
    p.add_argument('--linked-base', default=None, help='Linked attachment base directory (overrides prefs.js)')
    p.add_argument('--data-dir', default=None, help='Jurism data directory (overrides prefs.js)')
    p.add_argument('--limit', type=int, default=20, help='Max number of items to export (0 = all)')
    p.add_argument('--rebase-old', default=None, help='If specified, rebase absolute paths from this old base to linked-base')
    p.add_argument('--drop-missing', action='store_true', help='Do not copy missing storage files (default: log only)')
    args = p.parse_args()

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

    # build storage index for robust matching
    storage_index = build_storage_index(storage_dir) if storage_dir else {}
    print('Storage index entries:', sum(len(v) for v in storage_index.values()))

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    item_types = {}
    for r in sqlite_rows(conn, "SELECT itemTypeID, typeName FROM itemTypes"):
        item_types[r['itemTypeID']] = r['typeName']

    attachment_type_ids = [k for k,v in item_types.items() if v == 'attachment']
    note_type_ids = [k for k,v in item_types.items() if v == 'note']

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

    fields_map = {r['fieldID']: r['fieldName'] for r in sqlite_rows(conn, 'SELECT fieldID, fieldName FROM fieldsCombined')}

    creators = {r['creatorID']: {'firstName': r['firstName'], 'lastName': r['lastName'], 'fieldMode': r['fieldMode']} for r in sqlite_rows(conn, 'SELECT creatorID, firstName, lastName, fieldMode FROM creators')}

    item_creators = {}
    for r in sqlite_rows(conn, 'SELECT itemID, creatorID, creatorTypeID, orderIndex FROM itemCreators ORDER BY itemID, orderIndex'):
        item_creators.setdefault(r['itemID'], []).append({'creatorID': r['creatorID'], 'creatorTypeID': r['creatorTypeID'], 'orderIndex': r['orderIndex']})

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
                resolved = resolve_storage_file_with_index(storage_index, path)
                kind = 'storage'
                if resolved:
                    dst_name = os.path.basename(resolved)
                    dst = Path(args.attachments_dir) / dst_name
                    try:
                        shutil.copy2(resolved, dst)
                        copied_files.append(str(dst))
                        # use absolute file URI for RDF
                        resolved = str(dst.resolve())
                    except Exception:
                        missing_files.append(path)
                        resolved = None
                else:
                    missing_files.append(path)
            elif path.startswith('attachments:'):
                tail = path.split(':',1)[1]
                resolved = os.path.join(linked_base, tail)
                kind = 'attachments'
            elif path.startswith('/'):
                resolved = rebase_absolute_path(path, args.rebase_old, linked_base)
                kind = 'absolute_rebased'
            else:
                resolved = os.path.join(linked_base, path)
                kind = 'assumed_linked'
        else:
            resolved = None
            kind = 'no_path'
        attachments_by_parent.setdefault(parent, []).append({'itemID': itemID, 'path': path, 'resolved': resolved, 'kind': kind, 'contentType': a['contentType']})

    def item_values(item_id):
        rows = list(sqlite_rows(conn, 'SELECT d.fieldID, f.fieldName, v.value FROM itemData d JOIN itemDataValues v ON d.valueID = v.valueID JOIN fieldsCombined f ON d.fieldID = f.fieldID WHERE d.itemID = ?', (item_id,)))
        return {r['fieldName']: r['value'] for r in rows}

    def item_alt_values(item_id):
        rows = list(sqlite_rows(conn, 'SELECT d.fieldID, f.fieldName, d.languageTag, v.value FROM itemDataAlt d JOIN itemDataValues v ON d.valueID = v.valueID JOIN fields f ON d.fieldID = f.fieldID WHERE d.itemID = ?', (item_id,)))
        out = {}
        for r in rows:
            out.setdefault(r['fieldName'], []).append({'lang': r['languageTag'], 'value': r['value']})
        return out

    exported = []
    for it in items:
        iid = it['itemID']
        vals = item_values(iid)
        alts = item_alt_values(iid)
        creators_list = []
        for c in item_creators.get(iid, []):
            cid = c['creatorID']
            cr = creators.get(cid)
            if cr:
                if cr.get('firstName') and cr.get('lastName'):
                    creators_list.append({'firstName': cr.get('firstName'), 'lastName': cr.get('lastName')})
                elif cr.get('lastName'):
                    creators_list.append({'lastName': cr.get('lastName')})
                elif cr.get('firstName'):
                    creators_list.append({'firstName': cr.get('firstName')})

        atts = attachments_by_parent.get(iid, [])
        extra_lines = []
        if vals.get('extra'):
            extra_lines.append(vals.get('extra'))
        for field in ('title', 'bookTitle', 'publicationTitle'):
            if field in alts:
                for alt in alts[field]:
                    if alt['lang'].startswith('en') or alt['lang'] == 'en':
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

    with open(args.json_out, 'w', encoding='utf-8') as jf:
        json.dump(exported, jf, ensure_ascii=False, indent=2)
    print('Wrote', args.json_out)

    # Build Zotero-friendly RDF using the z: namespace
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
        # item type
        itype = ET.SubElement(item_el, '{%s}itemType' % NS['z'])
        itype.text = it['type'] or 'document'
        # title
        if it['fields'].get('title'):
            t = ET.SubElement(item_el, '{%s}title' % NS['z'])
            t.text = it['fields'].get('title')
        # creators
        if it['creators']:
            for c in it['creators']:
                creator_el = ET.SubElement(item_el, '{%s}creator' % NS['z'])
                # prefer lastName/given if present
                if isinstance(c, dict):
                    if c.get('lastName'):
                        ln = ET.SubElement(creator_el, '{%s}lastName' % NS['z'])
                        ln.text = c.get('lastName')
                    if c.get('firstName'):
                        gn = ET.SubElement(creator_el, '{%s}firstName' % NS['z'])
                        gn.text = c.get('firstName')
                else:
                    creator_el.text = c
        # language
        if it['fields'].get('language'):
            lang_el = ET.SubElement(item_el, '{%s}language' % NS['z'])
            lang_el.text = it['fields'].get('language')
        # abstract/description
        if it['fields'].get('abstractNote'):
            abs_el = ET.SubElement(item_el, '{%s}abstractNote' % NS['z'])
            abs_el.text = it['fields'].get('abstractNote')
        # publisher, date, ISBN, pages
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
        # container title
        container = it['fields'].get('publicationTitle') or it['fields'].get('bookTitle')
        if container:
            cont_el = ET.SubElement(item_el, '{%s}publicationTitle' % NS['z'])
            cont_el.text = container
        # Extra (use z:extra so Zotero imports into Extra field)
        if it.get('extra_combined'):
            extra_el = ET.SubElement(item_el, '{%s}extra' % NS['z'])
            extra_el.text = it.get('extra_combined')

        # attachments: add z:attachment elements with file:// URIs where possible
        if it['attachments']:
            for a in it['attachments']:
                resolved = a.get('resolved')
                if resolved:
                    # if this is not already a file URI, make it absolute and file://
                    if not resolved.startswith('file://'):
                        # if it's not absolute, resolve relative to attachments_dir
                        if not os.path.isabs(resolved):
                            resolved_abs = os.path.abspath(resolved)
                        else:
                            resolved_abs = resolved
                        uri = 'file://' + resolved_abs
                    else:
                        uri = resolved
                    att_el = ET.SubElement(item_el, '{%s}attachment' % NS['z'], {'{%s}resource' % NS['rdf']: uri})
                else:
                    # unresolved attachments: include a simple attachment element with path text
                    att_el = ET.SubElement(item_el, '{%s}attachment' % NS['z'])
                    if a.get('path'):
                        att_el.text = a.get('path')

    # write RDF
    with open(args.out, 'w', encoding='utf-8') as rf:
        xmlstr = prettify_xml(rdf)
        rf.write(xmlstr)
    print('Wrote', args.out)

    stats = {
        'items_exported': len(exported),
        'attachments_copied': len(copied_files),
        'missing_files_count': len(missing_files),
        'missing_files_sample': missing_files[:50]
    }
    with open('stats.txt', 'w', encoding='utf-8') as sf:
        json.dump(stats, sf, indent=2)
    print('Wrote stats.txt')
    print('Done. Review', args.json_out, args.out, 'and stats.txt')


if __name__ == '__main__':
    main()
