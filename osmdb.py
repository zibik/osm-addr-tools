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
    return soup.find(k=key).get('v')

def _getAddr(soup):
    """converts tags to address tuple"""
    city = _getVal(soup, 'addr:city')
    street = _getVal(soup, 'addr:street')
    if not street:
        street = _getVal(soup, 'addr:place')
    housenumber = _getVal(soup, 'addr:housenumber')
    return (city, street, housenumber)

    

class OsmDb(object):
    def __init__(self, osmdata):
        # assume osmdata is a BeautifulSoup object already
        # do it an assert
        if isinstance(osmdata, BeautifulSoup):
            soup = osmdata
        else:
            soup = BeautifulSoup(osmdata)
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
                    key = _getAddr(i)
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

    def nearest(self, point, num_results=1):

        return map(self.__index_entries.get, 
                   self.__index.nearest(point * 2, num_results)
               )
    def getbyaddress(self, key):
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
            
            way_by_first_node = dict((x.find('node')['id'], x) for x in outer)
            ret = []
            cur_elem = outer.pop()
            while outer:
                node_ids = list(y['ref'] for y in cur_elem.find_all('nd', recursive=False))
                ret.extend(tuple(map(float, (x['lat'], x['lon']))) for x in (self.__nodes[y] for y in node_ids))
                outer.remove(cur_elem)
                cur_elem = way_by_first_node[node_ids[-1]]
            return Polygon(ret)
                

def main():
    odb = OsmDb(open("adresy.osm").read())
    print(list(odb.nearest((53.5880600, 19.5555200), 10)))


if __name__ == '__main__':
    main()
