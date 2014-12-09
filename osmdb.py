from rtree import index
from bs4 import BeautifulSoup
from shapely.geometry import Point, Polygon
import utils
import logging



__multipliers = {
    'node'    : lambda x: x*3,
    'way'     : lambda x: x*3+1,
    'relation': lambda x: x*3+2,
}

def _get_id(soup):
    """Converts overlapping identifiers for node, ways and relations in single integer space"""
    return __multipliers[soup.name](int(soup['id']))


def get_soup_position(soup):
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
            raise TypeError("No bounds for ways and relations!")
    raise TypeError("%s not supported" % (soup.name,))

def get_soup_center(soup):
    # lat, lon
    pos = get_soup_position(soup)
    return (pos[0] + pos[2])/2, (pos[1] + pos[3])/2

class OsmDbEntry(object):
    def __init__(self, entry, raw, osmdb):
        self._entry = entry
        self._raw = raw
        self._osmdb = osmdb

    @property
    def entry(self):
        return self._entry

    @property
    def shape(self):
        return self._osmdb.get_shape(self._raw)
    
    @property
    def center(self):
        return self.shape.centroid

    def __getattr__(self, attr):
        return getattr(self.entry, attr)

    def within(self, other):
        return self.shape.within(other)

    def contains(self, other):
        return self.shape.contains(other)

class OsmDb(object):
    __log = logging.getLogger(__name__).getChild('OsmDb')
    def __init__(self, osmdata, valuefunc=lambda x: x, indexes={}):
        # assume osmdata is a BeautifulSoup object already
        # do it an assert
        if isinstance(osmdata, BeautifulSoup):
            soup = osmdata
        else:
            soup = BeautifulSoup(osmdata)
        self._soup = soup
        self.__custom_indexes = dict((x, {}) for x in indexes.keys())
        self._valuefunc=valuefunc
        self.__custom_indexes_conf = indexes

        def makegetfromindex(i):
            def getfromindex(key):
                return self.__custom_indexes[i].get(key, [])
            return getfromindex
        def makegetallindexed(i):
            def getallindexed():
                return tuple(self.__custom_indexes[i].keys())
            return getallindexed

        for i in indexes.keys():
            setattr(self, 'getby' + i, makegetfromindex(i))
            setattr(self, 'getall' + i, makegetallindexed(i))
        self.update_index()

    def update_index(self):
        self.__log.debug("Recreating index")

        self.__index = index.Index()
        self.__index_entries = {}
        self.__osm_obj = {}
        self.__custom_indexes = dict((x, {}) for x in self.__custom_indexes_conf.keys())

        for i in self._soup.osm.find_all(['node', 'way', 'relation'], recursive=False):
            val = OsmDbEntry(self._valuefunc(i), i, self)
            self.__osm_obj[(i.name, i['id'])] = val

            pos = get_soup_position(i)
            if pos:
                _id = _get_id(i)
                self.__index.insert(_id, pos)

                self.__index_entries[_id] = val

                for j in self.__custom_indexes_conf.keys():
                    custom_index = self.__custom_indexes[j]
                    key = self.__custom_indexes_conf[j](val)
                    try:
                        entry = custom_index[key]
                    except KeyError:
                        entry = []
                        custom_index[key] = entry
                    entry.append(val)


    def get_all_values(self):
        return self.__index_entries.values()

    def nearest(self, point, num_results=1):
        if isinstance(point, Point):
            point = (point.y, point.x)
        return map(self.__index_entries.get, 
                   self.__index.nearest(point * 2, num_results)
               )

    def get_shape(self, soup):
        if soup.name == 'node':
            return Point(tuple(map(float, (soup['lon'], soup['lat']))))

        if soup.name == 'way':
            return Polygon(tuple(map(float, (x.center.x, x.center.y))) for x in (self.__osm_obj[('node', y['ref'])] for y in soup.find_all('nd', recursive=False)))

        if soup.name == 'relation':
            # returns only outer ways, no exclusion for inner ways
            # hardest one
            # outer ways
            # TODO: handle multiple outer ways, and inner ways
            # multiple outer: terc=1019042
            # inner ways: terc=1014082
            outer = list(self.__osm_obj[(x['type'], x['ref'])] for x in soup.find_all(
                                                    lambda x: x.name == 'member' and 
                                                    x['type'] == 'way' and 
                                                    (x['role'] == 'outer' or not x.get('role')))
                    )
            
            way_by_first_node = utils.groupby(outer, lambda x: x._raw.find('nd')['ref'])
            way_by_last_node = utils.groupby(outer, lambda x: x._raw.find_all('nd')[-1]['ref'])
            ret = []
            cur_elem = outer[0]
            node_ids = []
            while outer:
                ids = list(y['ref'] for y in cur_elem._raw.find_all('nd', recursive=False))
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
                ret.extend(tuple(map(float, (x.center.x, x.center.y))) for x in (self.__osm_obj[('node', y)] for y in ids))
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
                
def main():
    odb = OsmDb(open("adresy.osm").read())
    print(list(odb.nearest((53.5880600, 19.5555200), 10)))


if __name__ == '__main__':
    main()
