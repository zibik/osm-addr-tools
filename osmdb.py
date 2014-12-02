from rtree import index
from bs4 import BeautifulSoup
from shapely.geometry import Point, Polygon



__multipliers = {
    'node'    : lambda x: x*3,
    'way'     : lambda x: x*3+1,
    'relation': lambda x: x*3+2,
}

def _getId(soup):
    """Converts overlapping identifiers for node, ways and relations in single integer space"""
    return __multipliers[soup.name](int(soup['id']))

def _getVal(soup, key):
    tag = soup.find(k=key)
    if tag:
        return tag.get('v')
    else:
        return None

def _getAddr(soup):
    """converts tags to address tuple"""
    city = _getVal(soup, 'addr:city')
    street = _getVal(soup, 'addr:street')
    if not street:
        street = _getVal(soup, 'addr:place')
        city = None
    housenumber = _getVal(soup, 'addr:housenumber').replace(' ', '')
    return (city, street, housenumber)

    

class OsmDb(object):
    def __init__(self, osmdata, keyfunc=lambda x: x):
        # assume osmdata is a BeautifulSoup object already
        # do it an assert
        if isinstance(osmdata, BeautifulSoup):
            soup = osmdata
        else:
            soup = BeautifulSoup(osmdata)
        self.__keyfunc = keyfunc
        self.__index = index.Index()
        self.__index_entries = {}
        self.__addr_index = {}
        self.__nodes = {}
        self.__ways = {}

        for i in soup.osm.find_all(['node', 'way', 'relation'], recursive=False):
            _id = _getId(i)
            pos = self.__getPos(i)
            if pos:
                self.__index.insert(_id, pos)
                self.__index_entries[_id] = i
                if i.find(k="addr:housenumber"):
                    key = tuple(keyfunc(x) if x else x for x in _getAddr(i))
                    if key:
                        lst = self.__addr_index.get(key)
                        if not lst:
                            lst = []
                            self.__addr_index[key] = lst
                        lst.append(i)
            
            if i.name=='node':
                self.__nodes[i['id']] = i

            if i.name=='way':
                self.__ways[i['id']] = i

    def __getPos(self, soup):
        """Extracts position for way/node as bounding box"""
        if soup.name == 'node':
            return (float(soup['lat']), float(soup['lon'])) * 2

        if soup.name in ('way', 'relation'):
            b = soup.bounds
            if b:
                return tuple(
                        map(float,
                            (b['minlat'], b['minlon'], b['maxlat'], b['maxlon'])
                        )
                    )
            else:
                return None

        raise TypeError("%s not supported" % (soup.name,))

    def getCenter(self, soup):
        pos = self.__getPos(soup)
        return (pos[0] + pos[2])/2, (pos[1] + pos[3])/2

    def nearest(self, point, num_results=1):

        return map(self.__index_entries.get, 
                   self.__index.nearest(point * 2, num_results)
               )
    def getbyaddress(self, key):
        key = tuple(self.__keyfunc(x) if x else x for x in key)
        key = key[:2] + (key[2].replace(' ',''),)
        return self.__addr_index.get(key, [])

    def getalladdresses(self):
        return list(self.__addr_index.keys())

    def getShape(self, soup):
        if soup.name == 'node':
            return Point(tuple(map(float, soup['lat'], soup['lon'])))

        if soup.name == 'way':
            return Polygon(tuple(map(float, (x['lat'], x['lon']))) for x in (self.__nodes[y['ref']] for y in soup.find_all('nd', recursive=False)))

        if soup.name == 'relation':
            # returns only outer ways, no exclusion for inner ways
            # hardest one
            # outer ways
            outer = list(self.__ways[x['ref']] for x in soup.find_all(
                                                    lambda x: x.name == 'member' and 
                                                    x['type'] == 'way' and 
                                                    (x['role'] == 'outer' or not x.get('role')))
                    )
            
            way_by_first_node = createDictOfList((x.find('nd')['ref'], x) for x in outer)
            way_by_last_node = createDictOfList((x.find_all('nd')[-1]['ref'], x) for x in outer)
            ret = []
            cur_elem = outer[0]
            node_ids = []
            while outer:
                ids = list(y['ref'] for y in cur_elem.find_all('nd', recursive=False))
                if not node_ids:
                    node_ids.extend(ids)
                else:
                    if ids[0] == node_ids[-1]:
                        pass
                    elif ids[-1] == node_ids[-1]:
                        ids = list(reversed(ids))
                    elif ids[0] == node_ids[0]:
                        node_ids = list(reversed(node_ids))
                    elif ids[-1] == node_ids[0]:
                        node_ids = list(reversed(node_ids))
                        ids = list(reversed(ids))
                ret.extend(tuple(map(float, (x['lat'], x['lon']))) for x in (self.__nodes[y] for y in ids))
                node_ids.extend(ids)
                outer.remove(cur_elem)
                if node_ids[0] == node_ids[-1]:
                    # get only first outer 
                    # TODO - return MultiPolygon with inner and outer shapes
                    break
                # TODO: refactor this
                # try last node with first node of what's left in outer
                if outer:
                    try:
                        cur_elem = list(filter(lambda x: x in outer, way_by_first_node[node_ids[-1]]))[0]
                    except (KeyError, IndexError):
                        try:
                            # try last node with last node of what's left in outer
                            cur_elem = list(filter(lambda x: x in outer, way_by_last_node[node_ids[-1]]))[0]
                        except (KeyError, IndexError):
                            try:
                                # try first node with last node of what's left in outer
                                cur_elem = list(filter(lambda x: x in outer, way_by_last_node[node_ids[0]]))[0]
                            except (KeyError, IndexError):
                                # try first node with first node of what's left in outer
                                cur_elem = list(filter(lambda x: x in outer, way_by_first_node[node_ids[0]]))[0]

            return Polygon(ret)
                
def createDictOfList(lst):
    ret = {}
    for (k, v) in lst:
        try:
            entry = ret[k]
        except KeyError:
            entry = []
            ret[k] = entry
        entry.append(v)
    return ret
def main():
    odb = OsmDb(open("adresy.osm").read())
    print(list(odb.nearest((53.5880600, 19.5555200), 10)))


if __name__ == '__main__':
    main()
