#!/usr/bin/env python3.4

import argparse
import logging
import io
import sys
from osmdb import OsmDb
import json
import pyproj
from bs4 import BeautifulSoup
from shapely.geometry import Point
from punktyadresowe_import import iMPA
import overpass 
from utils import parallel_execution

__log = logging.getLogger(__name__)

# depends FreeBSD
# portmaster graphics/py-pyproj devel/py-rtree devel/py-shapely www/py-beautifulsoup devel/py-lxml

# depeds Lubuntu:
# apt-get install python3-pyproj libspatialindex-dev python3-shapely python3-bs4 python3-lxml 
# easy_install3 Rtree

__geod = pyproj.Geod(ellps="WGS84")

def getAddresses(terc):
    query = """
[out:xml]
[timeout:600]
;
area
  ["boundary"="administrative"]
  ["admin_level"="7"]
  ["teryt:terc"~"%s"]
  ["type"="boundary"]
->.boundryarea;
(
  node
    (area.boundryarea)
    ["addr:housenumber"]
    ["amenity"!~"."]
    ["shop"!~"."]
    ["tourism"!~"."]
    ["emergency"!~"."]
    ["company"!~"."];
  way
    (area.boundryarea)
    ["addr:housenumber"];
  way
    (area.boundryarea)
    ["building"];
  relation
    (area.boundryarea)
    ["addr:housenumber"];
  relation
    (area.boundryarea)
    ["building"];
);
out meta bb qt;
>;
out meta qt;
""" % (terc,)
    return overpass.query(query)

def distance(a, b):
    """returns distance betwen a and b points in meters"""
    return __geod.inv(a[1], a[0], b[1], b[0])[2]

def buffer(shp, meters=0):
    # 0.0000089831528 is the 1m length in arc degrees of great circle
    return shp.buffer(meters*0.0000089831528)


def _getAddr(dct):
    if dct.get('addr:street'):
        return (
            dct['addr:city'],
            dct['addr:street'],
            dct['addr:housenumber'])
    else:
        return (
            None,
            dct['addr:place'],
            dct['addr:housenumber'])

def _getVal(node, key):
    n = node.find('tag', k=key)
    return n['v'] if n else None

def _valEq(node, key, value):
    v = _getVal(node, key)
    return v == None or v == value

def _updateTag(node, key, val):
    """returns True if something was modified"""
    n = node.find('tag', k=key)
    if n:
        if n['v'] == val:
            return False
        n['v'] = val
    else:
        new = BeautifulSoup().new_tag(name='tag', k=key, v=val)
        node.append(new)
    return True

_last_node_id = 0
def _getNodeId():
    global _last_node_id
    _last_node_id -= 1
    return _last_node_id

def _createPoint(entry):
    ret = BeautifulSoup()
    node = ret.new_tag(name='node', id=_getNodeId(), action='modify', lat=entry['location']['lat'], lon=entry['location']['lon'])
    ret.append(node)
    def addTag(key, val):
        if val:
            node.append(ret.new_tag(name='tag', k=key, v=val))

    if 'addr:street' in entry:
        addTag('addr:street', entry['addr:street'])
        addTag('addr:city', entry['addr:city'])
        #addTag('teryt:sym_ul', entry.get('teryt:sym_ul'))
    if 'addr:place' in entry:
        addTag('addr:place', entry['addr:place'])

    addTag('addr:housenumber', entry['addr:housenumber'])
    addTag('source:addr', entry['source:addr'])
    addTag('teryt:simc', entry.get('teryt:simc'))
    if entry.get('fixme'):
        addTag('fixme', entry.get('fixme'))
    return node
        



def _updateNode(node, entry):
    ret = False
    if 'addr:street' in entry:
        ret |= _updateTag(node, 'addr:city', entry['addr:city'])
        ret |= _updateTag(node, 'addr:street', entry['addr:street'])
        rmv = node.find('tag', k='addr:place')
        if rmv:
            ret |= bool(rmv.extract())
            assert ret == True
    if 'addr:place' in entry:
        ret |= _updateTag(node, 'addr:place', entry['addr:place'])

        rmv = node.find('tag', k='addr:street')
        if rmv:
            ret |= bool(rmv.extract())
            assert ret == True
        rmv = node.find('tag', k='addr:city')
        if rmv:
            ret |= bool(rmv.extract())
            assert ret == True
    ret |= _updateTag(node, 'addr:housenumber', entry['addr:housenumber'])
    #ret |= _updateTag(node, 'teryt:sym_ul', entry['teryt:sym_ul'])
    #ret |= _updateTag(node, 'teryt:simc', entry.get('teryt:simc'))
    if entry.get('fixme'):
        newfixme = _getVal(node, 'fixme')
        fixme = entry['fixme'] + ' ' + (newfixme if newfixme else '')
        ret |= _updateTag(node, 'fixme', fixme)

    if ret or isEMUiAAddr(node):
        source = node.find('tag', k='source')
        if source and 'EMUIA' in source['v'].upper():
            # Remove EMUIA source, if updating from iMPA
            source.extract()
        ret |= _updateTag(node, 'source:addr', entry['source:addr'])
        node['action'] = 'modify'
    assert isEMUiAAddr(node) == False
    return node

def onlyAddressNode(node):
    # return true, if its a node, has a addr:housenumber,
    # and consists only of tags listeb below
    if node.name != 'node':
        return False
    return _getVal(node, 'addr:housenumber') and all(map(
        lambda x: x in {'addr:housenumber', 'addr:street', 'addr:place', 
                        'addr:city', 'addr:postcode', 'teryt:sym_ul', 
                        'teryt:simc', 'source', 'source:addr', 'fixme'},
        (x['k'] for x in node.find_all('tag'))
        )
    )

def isEMUiAAddr(node):
    ret = False
    source = _getVal(node, 'source')
    if source:
        ret |= ('EMUIA' in source.upper())
    source_addr = _getVal(node, 'source:addr')
    if source_addr:
        ret |= ('EMUIA' in source_addr.upper())
    return ret


def getcenter(node):
    try:
        return tuple(map(float, (node['lat'], node['lon'])))
    except KeyError:
        b = node.bounds
        return ( (sum(map(float, (b['minlat'], b['maxlat']))))/2,
                 (sum(map(float, (b['minlon'], b['maxlon']))))/2)
   
def entrystr(entry):
    return "%s, %s, %s" % ( entry.get('addr:city'), 
                            entry.get('addr:street') if entry.get('addr:street') else entry.get('addr:place'), 
                            entry.get('addr:housenumber'))

def nodestr(node):
    return "%s, %s, %s" % ( _getVal(node, 'addr:city'), 
                            _getVal(node, 'addr:street') if _getVal(node, 'addr:street') else _getVal(node, 'addr:place'), 
                            _getVal(node, 'addr:housenumber'))

def _processOne(osmdb, entry):
    """process one entry (of type dict) and work with OsmDb instance to find addresses to merge

    returns list of updated nodes (might be more than one, but at most one will be added
    """
    __log.debug("Processing address: %s", entrystr(entry))

    entry_point = tuple(map(float, (entry['location']['lat'], entry['location']['lon'])))
    
    existing = osmdb.getbyaddress(_getAddr(entry))
    if existing:
        # we have something with this address in db
        # sort by distance
        emuia_nodes = tuple(filter(lambda x: isEMUiAAddr(x) and onlyAddressNode(x), existing))
        existing = sorted(map(lambda x: (distance(entry_point, getcenter(x)), x), existing), key=lambda x: x[0])

        # update location of first node
        if emuia_nodes:
            emuia_nodes[0]['lon'] = entry['location']['lon']
            emuia_nodes[0]['lat'] = entry['location']['lat']

        # all the others mark for deletion
        if len(emuia_nodes)>1:
            for node in emuia_nodes[1:]:
                node['action'] = 'delete'

        if max(x[0] for x in existing) > 100:
            for node in existing:
                __log.warning("Address (id=%s:%s) %s is %d meters from imported point", node[1].name, node[1]['id'], entrystr(entry), node[0])

        if len(existing) - len(emuia_nodes) > 1:
            # mark duplicates
            __log.warning("More than one address node for %s. Marking duplicates. %s", 
                            entrystr(entry), 
                            ", ".join("Id: %s:%s, dist: %sm" % (x[1].name, x[1]['id'], str(x[0])) for x in existing)
                         )
            for (n, (dist, node)) in enumerate(existing):
                if n > 0:
                    # skip closest one
                    _updateTag(node,'fixme', 'Duplicate node %s, distance: %s' % (n+1, dist))
                node['action'] = 'modify' # keep all duplicates in file
        # update data only on first duplicate, rest - leave to OSM-ers
        return [_updateNode(existing[0][1], entry)]

    # look for building nearby
    candidates = list(osmdb.nearest(entry_point, num_results=10))

    candidates_within = list(filter(lambda x: x.name in ('way', 'relation') and Point(entry_point).within(osmdb.getShape(x)), candidates))
    if candidates_within:
        c = candidates_within[0]
        if not c('tag', k='addr:housenumber'):
            # no address on way/relation -> add address
            # create a point, will be merged with building later
            return [_createPoint(entry)]
            #return [_updateNode(c, entry)]
        else:
            # WARNING - candidate has an address

            # do not compare on street names - assume that OSM has better name
            if _valEq(
                    c, 'addr:city', entry.get('addr:city')) and _valEq(
                    c, 'addr:place', entry.get('addr:place')) and _valEq(
                    c, 'addr:housenumber', entry.get('addr:housenumber')):
                if not _valEq(c, 'addr:street', entry.get('addr:street')):
                    # take addr:street value from OSM instead of imported data
                    entry['addr:street'] = _getVal(c, 'addr:street')
                else:
                    __log.warning("Update not based on address but on location, but have the same address for: %s (id: %s:%s)", 
                            entrystr(entry), 
                            c.name, 
                            c['id'])
                return [_updateNode(c, entry)]
            else:
                if c.get('addr:city') == c.get('addr:place') and not(c.get('addr:street')) and _valEq(
                    c, 'addr:place', entry.get('place')) and _valEq(
                    c, 'addr:housenumber', entry.get('addr:housenumber')) and _valEq(
                    c, 'addr:street', entry.get('addr:street')):
                    # we have addr:city, addr:place and no add:street in OSM
                    # addr:city is to be removed from OSM, update the point as
                    return [_updateNode(c, entry)]

                # address within a building that has different address, add a point, maybe building needs spliting
                __log.debug("Adding new node within building with address")
                return [_createPoint(entry)]
    # no address existing, no candidates within buildings, check closest one
    #c = candidates[0]
    #dist = distance(tuple(map(float, (c['lat'], c['lon']))), entry_point)
    candidates_same = list(filter(lambda x: _getVal(x, 'addr:housenumber') == entry['addr:housenumber'] and \
        distance(osmdb.getCenter(x), entry_point) < 2.0, candidates))
    if len(candidates_same) > 0:
        # same location, both are an address, and have same housenumber, can't be coincidence,
        # probably mapper changed something
        for c in candidates_same:
            ret = []
            if c.get('addr:city') == c.get('addr:place') and not(c.get('addr:street')) and _valEq(
                c, 'addr:place', entry.get('place')) and _valEq(
                c, 'addr:housenumber', entry.get('addr:housenumber')) and _valEq(
                c, 'addr:street', entry.get('addr:street')):
                # we have addr:city, addr:place and no add:street in OSM
                # addr:city is to be removed from OSM, update the point as
                ret.append(_updateNode(c, entry))
            if ret:
                return ret
        if all(map(isEMUiAAddr, candidates_same)):
            ret = [_createPoint(entry)]
            _updateTag(ret[0], 'fixme', 'Check for nearby EMUiA address that might be obsolete')
            return ret
        __log.info("Found probably same address node at (%s, %s). Skipping. Address is: %s", entry['location']['lon'], entry['location']['lat'], entrystr(entry))
        return []
    return [_createPoint(entry)]

def mergeAddr(node, addr):
    for tag in addr.find_all('tag'):
        # note: this moves tags from one object to another 
        node.append(tag)
    # mark for deletion
    if int(addr['id']) < 0:
        __log.info("Merging addr %s with building. Removing address node: %s:%s", nodestr(node), addr.name, addr['id'])
        addr['action'] = 'delete'
        addr.extract()
    else:
        # TODO - check if the addr node is used in ways - if so, remove addr tags
        __log.info("Merging addr %s with building. Marking address node for deletion: %s:%s", nodestr(node), addr.name, addr['id'])
        addr['action'] = 'delete'
    __log.debug(node)
    __log.debug(addr)

def _mergeAddrWithBuilding(soup, osmdb, buf=0):
    __log.info("Merging buildings with buffer: %s", buf)
    to_merge = {} # dictionary - building node_id -> list of address nodes
    for node in soup.find_all(lambda x: onlyAddressNode(x) and x.get('action')!='delete'):
        entry_point = tuple(map(float, (node['lat'], node['lon'])))
        candidates = list(osmdb.nearest(entry_point, num_results=10))
        # first look for relations and if this fails check for ways
        candidates_within = list(filter(lambda x: x.name == 'relation' and Point(entry_point).within(buffer(osmdb.getShape(x),buf)), candidates))
        if not candidates_within:
            candidates_within = list(filter(lambda x: x.name == 'way' and Point(entry_point).within(buffer(osmdb.getShape(x),buf)), candidates))
        if candidates_within:
            c = candidates_within[0]
            key = "%s:%s" % (c.name, c['id'])
            if _getVal(c, 'addr:housenumber'):
                # address is within building, that alread has an address
                # mark building, so human may choose
                if not c.get('action'):
                    __log.info("Marking building (%s:%s) as modified, because address %s is within", c.name, c['id'], nodestr(node))
                    c['action'] = 'modify'
            if not c('tag', k='addr:housenumber') and not c('tag', k='fixme'):
                # only merge with buildings without address and without fixmes
                try:
                    lst = to_merge[key]
                except KeyError:
                    lst = []
                    to_merge[key] = lst
                lst.append(node)

    buildings = dict(
        ("%s:%s" % (x.name, x['id']), x) for x in soup.find_all(['way', 'relation'])
    )
    __log.info("Merging %d addresses with buildings", len(tuple(filter(lambda x: len(x[1]) == 1, to_merge.items()))))
    for (_id, nodes) in to_merge.items():
        c = buildings[_id]
        if len(nodes) > 0:
            __log.debug("building: %s - marking as modified", _id)
            c['action'] = 'modify' # all buildings mark as modify, so they will be visible in changeset as candidates or merging
            if c.name == 'relation':
                for way in map(buildings.get, ("%s:%s" % (x['type'], x['ref']) for x in c.find_all('member'))):
                    if way.get('action') != 'delete':
                        way['action'] = 'modify'
        # do the merge, when only one candidate exists
        if len(nodes) == 1:
            if _getVal(node, 'fixme'):
                __log.info("Skipping merging node: %s, because of fixme: %s:%s", node.name, node['id'], _getVal(node, 'fixme'))
            else:
                __log.debug("building: %s - merging with address", _id)
                mergeAddr(c, nodes[0])
        if len(nodes) > 1: # ensure, that all nodes will be visible for manual addr merging
            __log.debug("building: %s - leaving building and addresses unmerged", _id)
            for node in nodes:
                if node.get('action') != 'delete':
                    __log.debug("address: %s:%s - marking as modified, just to be sure", node.name, node['id'])
                    node['action'] = 'modify'

def mergeAddrWithBuilding(soup):
    osmdb = OsmDb(soup, keyfunc=str.upper)
    _mergeAddrWithBuilding(soup, osmdb, 0)
    _mergeAddrWithBuilding(soup, osmdb, 2)
    _mergeAddrWithBuilding(soup, osmdb, 5)
    _mergeAddrWithBuilding(soup, osmdb, 10)

def removeNotexitingAddresses(asis, impdata):
    imp_addr = set(map(lambda x: tuple(map(lambda x: str.upper(x) if x else x, _getAddr(x))), impdata))
    osmdb = OsmDb(asis, keyfunc=str.upper)
    ret = []
    for addr in (set(osmdb.getalladdresses()) - imp_addr):
        entries = osmdb.getbyaddress(addr)
        for entry in entries:
            ret.append(entry)
            fixme = _getVal(entry, 'fixme')
            if not fixme:
                fixme = ""
            if isEMUiAAddr(entry):
                if onlyAddressNode(entry):
                    __log.debug("Marking entry to delete: %s", entry)
                    _updateTag(entry, 'fixme', 'Delete this node with address. Comes from obsolete EMUiA import, now doesn''t exist. ' +fixme)
                else:
                    __log.debug("Marking entry to delete: %s", entry)
                    _updateTag(entry, 'fixme', 'Delete addr fields from this node. Comes from obsolete EMUiA import, now doesn''t exit. ' +fixme)
                if entry.get('action') != 'delete':
                    entry['action'] = 'modify'
            else:
                __log.warning("iMPA doesn't contain address present in OSM. Marking with fixme=Check existance")
                _updateTag(entry, 'fixme', 'Check existance. ' +fixme)
                if entry.get('action') != 'delete':
                    entry['action'] = 'modify'
    return ret
            

def getEmptyOsm(meta):
    ret = BeautifulSoup("", "xml")
    #ret = BeautifulSoup()
    osm = ret.new_tag('osm', version="0.6", generator="import adresy merge.py")
    ret.append(osm)
    nt = ret.new_tag('note')
    nt.string = 'The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.'
    ret.osm.append(nt)
    ret.osm.append(meta)
    return ret

def mergeInc(asis, impdata, logIO=None):
    asis = BeautifulSoup(asis, "xml")
    osmdb = OsmDb(asis, keyfunc=str.upper)
    new_nodes = list(map(lambda x: _processOne(osmdb, x), impdata))
    new_nodes = [item for sublist in new_nodes for item in sublist]

    new_nodes.extend(removeNotexitingAddresses(asis, impdata))
    # add all new objects to asis, so merge'ing will take them into account
    for i in filter(lambda x: float(x['id']) < 0, new_nodes):
        asis.osm.append(i)

    mergeAddrWithBuilding(asis)

    ret = getEmptyOsm(asis.meta)

    # add all modified nodes, way and relations
    for i in filter(lambda x: x.get('action'), asis.find_all(['node', 'way', 'relation'])):
        ret.osm.append(i)
    for i in filter(lambda x: int(x['id']) < 0, new_nodes):
        ret.osm.append(i)
    nd_refs = set(i['ref'] for i in ret.find_all('nd'))
    nodes = set(i['id'] for i in ret.find_all('node'))
    nd_refs = nd_refs - nodes
    for i in asis.find_all(lambda x: x.name == 'node' and x['id'] in nd_refs):
        ret.osm.append(i)
    # add log entries as note
    ret.osm.note.string = "The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.\n"
    if logIO:
        ret.osm.note.string += logIO.getvalue()
    return ret.prettify()

def mergeFull(asis, impdata, logIO=None):
    asis = BeautifulSoup(asis, "xml")
    osmdb = OsmDb(asis, keyfunc=str.upper)

    new_nodes = list(map(lambda x: _processOne(osmdb, x), impdata))
    new_nodes = [item for sublist in new_nodes for item in sublist]

    new_nodes.extend(removeNotexitingAddresses(asis, impdata))
    # add all new objects to asis, so merge'ing will take them into account
    for i in filter(lambda x: float(x['id']) < 0, new_nodes):
        asis.osm.append(i)

    mergeAddrWithBuilding(asis)

    ret = asis
    for i in filter(lambda x: x.get('action') == 'modify', asis.find_all(['node', 'way', 'relation'])):
        _updateTag(i, 'import:action', 'modify')
    asis.osm.note.string = "The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.\n"
    if logIO:
        asis.osm.note.string += logIO.getvalue()
    return asis.prettify()
        
def main():
    # TODO: create mode where no unchanged data are returned (as addresses to be merged with buildings)
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, 
    description="""Merge data from WMS with OSM data. Generate output OSM file for JOSM. You need to provide one of:
    1. --impa with service name
    2. --import-file and --addresses-file
    3. --import-file and --terc """)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--impa', help='name of iMPA service, use "milawa" when the address is milawa.e-mapa.net')
    source_group.add_argument('--import-file', type=argparse.FileType("r", encoding='UTF-8'), help='JSON file generated by punktyadresowe_import.py for the area', dest='import_file')
    address_group = parser.add_argument_group()
    address_group.add_argument('--addresses-file', type=argparse.FileType("r", encoding='UTF-8'), help='OSM file with addresses and buildings for imported area', dest='addresses_file')
    address_group.add_argument('--terc', help='teryt:terc code, for which to download addresses from OSM using Overpass API')
    parser.add_argument('--output', type=argparse.FileType('w+', encoding='UTF-8'), help='output file with merged data (default: result.osm)', default='result.osm')
    parser.add_argument('--full', help='Use to output all address data for region, not only modified address data as per default', action='store_const', const=True, dest='full_mode', default=False)
    parser.add_argument('--log-level', help='Set logging level (debug=10, info=20, warning=30, error=40, critical=50), default: 20', dest='log_level', default=20, type=int)

    args = parser.parse_args()
    logIO = io.StringIO()
    logging.basicConfig(level=args.log_level, handlers=[logging.StreamHandler(sys.stderr), logging.StreamHandler(logIO)])

    if args.impa:
        imp = iMPA(args.impa)
        terc = imp.getConf()['terc']
        dataFunc = lambda: imp.fetchTiles()

    if args.import_file:
        dataFunc = lambda: json.load(args.import_file)

    if args.terc:
        terc = args.terc

    if args.addresses_file:
        addrFunc = lambda: args.addresses_file.read()
    else:
        addrFunc = lambda: getAddresses(terc)

    (addr, data) = parallel_execution(addrFunc, dataFunc)

    if len(data) < 1:
        __log.warning("Warning - import data is empty. Check your import")

    if 'node' not in addr:
        __log.warning("Warning - address data is empty. Check your file/terc code")

    if args.full_mode:
        ret = mergeFull(addr, data, logIO)
    else:
        ret = mergeInc(addr, data, logIO)

    args.output.write(ret)

if __name__ == '__main__':
    main()
