from osmdb import OsmDb
import json
import pyproj
from bs4 import BeautifulSoup
from shapely.geometry import Point
from punktyadresowe_import import iMPA
from overpass import getAddresses

# depends:
# py-proj
# py-rtree
# py-shapely
# py-beautifulsoup

__geod = pyproj.Geod(ellps="WGS84")

def distance(a, b):
    """returns distance betwen a and b points in meters"""
    return __geod.inv(a[0], a[1], b[0], b[1])[2]

def _getAddr(dct):
    return (
        dct['addr:city'],
        dct.get('addr:street', dct.get('addr:place')),
        dct['addr:housenumber'])

def _updateTag(node, key, val):
    """returns True if something was modified"""
    n = node.find('tag', k=key)
    if n:
        if n['v'] == val:
            return False
        n['v'] = val
    else:
        new = node.parent.parent.new_tag(name='tag', k=key, v=val)
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
    print("Adding point %s" % (node['id'],))
    ret.append(node)
    def addTag(key, val):
        node.append(ret.new_tag(name='tag', k=key, v=val))

    addTag('addr:city', entry['addr:city'])
    if 'addr:street' in entry:
        addTag('addr:street', entry['addr:street'])
    if 'addr:place' in entry:
        addTag('addr:place', entry['addr:place'])

    addTag('addr:housenumber', entry['addr:housenumber'])
    addTag('source:addr', entry['source:addr'])
    addTag('teryt:sym_ul', entry['teryt:sym_ul'])
    addTag('teryt:simc', entry['teryt:simc'])
    return node
        



def _updateNode(node, entry):
    ret = False
    ret |= _updateTag(node, 'addr:city', entry['addr:city'])
    if 'addr:street' in entry:
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
        ret |= bool(node.find('tag', k='addr:street').extract())
    ret |= _updateTag(node, 'addr:housenumber', entry['addr:housenumber'])
    ret |= _updateTag(node, 'source:addr', entry['source:addr'])
    #ret |= _updateTag(node, 'teryt:sym_ul', entry['teryt:sym_ul'])
    #ret |= _updateTag(node, 'teryt:simc', entry['teryt:simc'])
    if ret:
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
    return "%s, %s, %s" % (entry['addr:city'], entry['addr:street'] if entry['addr:street'] else entry['addr:place'], entry['addr:housenumber'])

def _processOne(osmdb, entry):
    entry_point = tuple(map(float, (entry['location']['lat'], entry['location']['lon'])))

    if entry['teryt:sym_ul'] == '00285' and entry['addr:housenumber'] == '10':
        print("Got ya")
    existing = osmdb.getbyaddress(_getAddr(entry))
    if existing:
        # we have something with this address in db
        # sort by distance
        existing = sorted(map(lambda x: (distance(entry_point, getcenter(x)), x), existing))

        #TODO: dodaj if-a czy existing to node, czy way. Jak node z samym adresem - to można przesuwać ile wlezie
        if existing[0][1].name == 'node':
            # just update the node with values, no moving
            # TODO: update all?
            if len(existing) > 1:
                print("More than one address node for %s. Others not moved" % (entrystr(entry),))
            return _updateNode(existing[0][1], entry)
        if existing[0][0] < 100:
            # if the closest one is closer by 100m then just update and return the node
            return _updateNode(existing[0][1], entry)
        else:
            # we have something, but its futher than 100m
            print("Address %s is %s away from imported location and not on a node. Not updating" % (entrystr(entry), existing[0][0]))

    # look for building nearby
    candidates = list(osmdb.nearest(entry_point, num_results=10))

    def getbbox(soup):
        b = soup.bounds
        return tuple(map(float, (b['minlat'], b['minlon'], b['maxlat'], b['maxlon'])))

    candidates_within = list(filter(lambda x: x.name in ('way', 'relation') and Point(entry_point).within(osmdb.getShape(x)), candidates))
    if candidates_within:
        c = candidates_within[0]
        if not c('tag', k='addr:housenumber'):
            return _updateNode(c, entry)
        else:
            # WARNING - candidate has an address
            def getVal(node, key):
                n = node.find('tag', k=key)
                return n['v'] if n else None
            
            def valEq(node, key):
                v = getVal(node, key)
                return v == None or v == entry[key]

            # do not compare on street names - assume that OSM has better name
            if valEq(c, 'addr:city') and valEq(c, 'addr:place') and valEq(c, 'addr:housenumber'):
                if not valEq(c, 'addr:street'):
                    entry['addr:street'] = getVal(c, 'addr:street')
                else:
                    print("Update not based on address but on location")
                return _updateNode(c, entry)
            else:
                # address within a building that has different address, add a point, maybe building needs spliting
                print("Adding new node within building")
                return _createPoint(entry)
    # no address existing, no candidates
    return _createPoint(entry)

def getEmptyOsm(meta):
    #ret = BeautifulSoup("", "xml")
    ret = BeautifulSoup()
    osm = ret.new_tag('osm', version="0.6", generator="import adresy merge.py")
    ret.append(osm)
    nt = ret.new_tag('note')
    nt.string = 'The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.'
    ret.osm.append(nt)
    ret.osm.append(meta)
    return ret

def mergeInc(asis, impdata):
    asis = BeautifulSoup(asis)
    osmdb = OsmDb(asis)
    new_nodes = list(map(lambda x: _processOne(osmdb, x), impdata))
    #ret = asis
    #for i in filter(lambda x: float(x['id']) < 0, new_nodes):
    #    asis.osm.append(i)
    ret = getEmptyOsm(asis.meta)
    #print(",".join(map(lambda x: x['id'], filter(lambda x: x.name == 'way', new_nodes))))

    # add all modified nodes, way and relations
    for i in filter(lambda x: x.get('action') == 'modify', new_nodes):
        ret.osm.append(i)
    nd_refs = set(i['ref'] for i in ret.find_all('nd'))
    print("found %s nd_refs" % (len(nd_refs),))
    nodes = set(i['id'] for i in ret.find_all('node'))
    nd_refs = nd_refs - nodes
    print("after removing existring nodes: %s" % (len(nd_refs),))
    for i in asis.find_all(lambda x: x.name == 'node' and x['id'] in nd_refs):
        ret.osm.append(i)
    return ret.prettify()

def mergeFull(asis, impdata):
    asis = BeautifulSoup(asis)
    osmdb = OsmDb(asis)
    new_nodes = list(map(lambda x: _processOne(osmdb, x), impdata))
    ret = asis
    for i in filter(lambda x: float(x['id']) < 0, new_nodes):
        asis.osm.append(i)
    return asis.prettify()
        
    

def main():
    #osm = BeautifulSoup(open("adresy.osm"))
    #imp = json.load(open("milawa.json"))
    #ret = mergeInc(osm, imp)
    name = "milawa"
    imp = iMPA(name)
    terc = imp.getConf()['terc']

    addr = getAddresses(terc)
    data = imp.fetchTiles()

    ret = mergeInc(addr, data)

    with open("result.osm", "w+") as f:
        f.write(ret)

if __name__ == '__main__':
    main()
