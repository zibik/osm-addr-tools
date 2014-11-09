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
from overpass import getAddresses
from utils import parallel_execution

__log = logging.getLogger(__name__)

# depends FreeBSD
# portmaster graphics/py-pyproj devel/py-rtree devel/py-shapely www/py-beautifulsoup devel/py-lxml

# depeds Lubuntu:
# apt-get install python3-pyproj libspatialindex-dev python3-shapely python3-bs4 python3-lxml 
# easy_install3 Rtree


__geod = pyproj.Geod(ellps="WGS84")

def distance(a, b):
    """returns distance betwen a and b points in meters"""
    return __geod.inv(a[1], a[0], b[1], b[0])[2]

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
        addTag('teryt:sym_ul', entry.get('teryt:sym_ul'))
    if 'addr:place' in entry:
        addTag('addr:place', entry['addr:place'])

    addTag('addr:housenumber', entry['addr:housenumber'])
    addTag('source:addr', entry['source:addr'])
    addTag('teryt:simc', entry.get('teryt:simc'))
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
    #ret |= _updateTag(node, 'teryt:simc', entry['teryt:simc'])
    if ret:
        ret |= _updateTag(node, 'source:addr', entry['source:addr'])
        node['action'] = 'modify'
    return node

def getcenter(node):
    try:
        return tuple(map(float, (node['lat'], node['lon'])))
    except KeyError:
        b = node.bounds
        return ( (sum(map(float, (b['minlat'], b['maxlat']))))/2,
                 (sum(map(float, (b['minlon'], b['maxlon']))))/2)
   
def entrystr(entry):
    return "%s, %s, %s" % (entry.get('addr:city'), entry.get('addr:street') if entry.get('addr:street') else entry.get('addr:place'), entry.get('addr:housenumber'))

def _processOne(osmdb, entry):
    """process one entry (of type dict) and work with OsmDb instance to find addresses to merge

    returns list of updated nodes (might be more than one, but at most one will be added
    """
    entry_point = tuple(map(float, (entry['location']['lat'], entry['location']['lon'])))

    existing = osmdb.getbyaddress(_getAddr(entry))
    if existing:
        # we have something with this address in db
        # sort by distance
        existing = sorted(map(lambda x: (distance(entry_point, getcenter(x)), x), existing))

        if len(existing) > 1:
            # mark duplicates
            __log.warning("More than one address node for %s. Marking duplicates. Distances: %s", 
                            entrystr(entry), 
                            ", ".join(str(x[0]) for x in existing)
                         )
            for (n, (dist, node)) in enumerate(existing):
                _updateTag(node,'fixme', 'Duplicate node %s, distance: %s' % (n+1, dist))
                node['action'] = 'modify' # keep all duplicates in file
        # update address on all elements
        return list(map(lambda x: _updateNode(x[1], entry), existing))    

    # look for building nearby
    candidates = list(osmdb.nearest(entry_point, num_results=10))

    candidates_within = list(filter(lambda x: x.name in ('way', 'relation') and Point(entry_point).within(osmdb.getShape(x)), candidates))
    if candidates_within:
        c = candidates_within[0]
        if not c('tag', k='addr:housenumber'):
            # no address on way/relation
            return [_updateNode(c, entry)]
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
                    __log.warning("Update not based on address but on location, but have the same address")
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
        __log.info("Found probably same address node at (%s, %s). Skipping. Address is: %s", entry['location']['lon'], entry['location']['lat'], entry)
        return []
    return [_createPoint(entry)]

def mergeAddrWithBuilding(soup):
    if soup.name == 'node':
        entry_point = tuple(map(float, (soup['lat'], soup['lon'])))
        candidates = list(osmdb.nearest(entry_point, num_results=10))
        candidates_within = list(filter(lambda x: x.name in ('way', 'relation') and Point(entry_point).within(osmdb.getShape(x)), candidates))
        if candidates_within:
            c = candidates_within[0]
            if not c('tag', k='addr:housenumber'):
                # only merge with buildings without address
                for tag in soup.find_all('tag'):
                    c.append(tag)
                # mark for deletion
                soup['action'] = 'delete'

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

def mergeInc(asis, impdata, logIO):
    asis = BeautifulSoup(asis)
    osmdb = OsmDb(asis)
    # TODO - refactor and add mergeAddrWithBuilding
    new_nodes = list(map(lambda x: _processOne(osmdb, x), impdata))
    new_nodes = [item for sublist in new_nodes for item in sublist]
    ret = getEmptyOsm(asis.meta)

    # add all modified nodes, way and relations
    for i in filter(lambda x: x.get('action') == 'modify', new_nodes):
        ret.osm.append(i)
    nd_refs = set(i['ref'] for i in ret.find_all('nd'))
    nodes = set(i['id'] for i in ret.find_all('node'))
    nd_refs = nd_refs - nodes
    for i in asis.find_all(lambda x: x.name == 'node' and x['id'] in nd_refs):
        ret.osm.append(i)
    # add log entries as note
    ret.osm.note.string = "The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.\n" + logIO.getvalue()
    return ret.prettify()

def mergeFull(asis, impdata, logIO):
    asis = BeautifulSoup(asis, "xml")
    osmdb = OsmDb(asis)
    # TODO - refactor and add mergeAddrWithBuilding
    new_nodes = list(map(lambda x: _processOne(osmdb, x), impdata))
    new_nodes = [item for sublist in new_nodes for item in sublist]
    ret = asis
    for i in filter(lambda x: x.get('action') == 'modify',new_nodes):
        _updateTag(i, 'import:action', 'modify')
    for i in filter(lambda x: float(x['id']) < 0, new_nodes):
        asis.osm.append(i)
    asis.osm.note.string = "The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.\n" + logIO.getvalue()
    return asis.prettify()
        
def main():
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
