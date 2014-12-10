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
    return __multipliers[soup['type']](int(soup['id']))


__position_cache = {}
def get_soup_position(soup):
    try:
        return __position_cache[soup['id']]
    except KeyError:
        ret = get_soup_position_cached(soup)
        __position_cache[soup['id']] = ret
        return ret

def get_soup_position_cached(soup):
    """Extracts position for way/node as bounding box"""
    if soup['type'] == 'node':
        return (float(soup['lat']), float(soup['lon'])) * 2

    if soup['type'] in ('way', 'relation'):
        b = soup['bounds']
        if b:
            return tuple(
                    map(float,
                        (b['minlat'], b['minlon'], b['maxlat'], b['maxlon'])
                    )
                )
        else:
            raise TypeError("No bounds for ways and relations!")
    raise TypeError("%s not supported" % (soup['type'],))

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

    def __getitem__(self, attr):
        return self._entry[attr]

    def within(self, other):
        return self.shape.within(other)

    def contains(self, other):
        return self.shape.contains(other)

class OsmDb(object):
    __log = logging.getLogger(__name__).getChild('OsmDb')
    def __init__(self, osmdata, valuefunc=lambda x: x, indexes={}):
        # assume osmdata is a BeautifulSoup object already
        # do it an assert
        self._osmdata = osmdata
        self.__custom_indexes = dict((x, {}) for x in indexes.keys())
        self._valuefunc=valuefunc
        self.__custom_indexes_conf = indexes
        self.__cached_shapes = {}

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

        for i in self._osmdata['elements']:
            val = OsmDbEntry(self._valuefunc(i), i, self)
            self.__osm_obj[(i['type'], i['id'])] = val

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
        id_ = soup['id']
        try:
            return self.__cached_shapes[id_]
        except KeyError:
            ret = self.get_shape_cached(soup)
            self.__cached_shapes[id_] = ret
            return ret

    def get_shape_cached(self, soup):
        if soup['type'] == 'node':
            return Point(float(soup['lon']), float(soup['lat']))

        if soup['type'] == 'way':
            nodes = tuple(self.__osm_obj[('node', y)] for y in soup['nodes'])
            if len(nodes) < 3:
                self.__log.warning("Way has less than 3 nodes. Check geometry. way:%s" % (soup['id'],))
                self.__log.warning("Returning geometry as a point")
                return Point(sum(x.center.x for x in nodes)/len(nodes), sum(x.center.y for x in nodes)/len(nodes))
            return Polygon((x.center.x, x.center.y) for x in nodes)

        if soup['type'] == 'relation':
            # returns only outer ways, no exclusion for inner ways
            # hardest one
            # outer ways
            # TODO: handle multiple outer ways, and inner ways
            # multiple outer: terc=1019042
            # inner ways: terc=1014082
            outer = []
            inner = []
            for member in filter(lambda x: x['type'] == 'way', soup['members']):
                obj = self.__osm_obj[(member['type'], member['ref'])]
                if member['role'] == 'outer' or not member.get('role'):
                    outer.append(obj)
                if member['role'] == 'inner':
                    inner.append(obj)

            inner = self.get_closed_ways(inner)
            outer = self.get_closed_ways(outer)

            ret = None
            for out in outer:
                val = out
                for inn in filter(out.contains, inner):
                    val = val.difference(inn)
                if not ret:
                    ret = val
                else:
                    ret = ret.union(val)
            return ret

    def get_closed_ways(self, ways):
        if not ways:
            return []
        ways = list(ways)
        way_by_first_node = utils.groupby(ways, lambda x: x._raw['nodes'][0])
        way_by_last_node = utils.groupby(ways, lambda x: x._raw['nodes'][-1])
        ret = []
        cur_elem = ways[0]
        node_ids = []

        def _get_ids(elem):
            return elem['nodes']

        def _get_way(id_, dct):
            if id_ in dct:
                ret = tuple(filter(lambda x: x in ways, dct[id_]))
                if ret:
                    return ret[0]
            return None

        ids = _get_ids(cur_elem)
        while ways:
            #ids = list(y['ref'] for y in cur_elem._raw.find_all('nd', recursive=False))
            node_ids.extend(ids)
            ways.remove(cur_elem)
            if node_ids[0] == node_ids[-1]:
                # full circle, append to Polygons in ret
                ret.append(
                    Polygon(
                        (x.center.x, x.center.y) for x in (self.__osm_obj[('node', y)] for y in node_ids)
                    )
                )
                if ways:
                    cur_elem = ways[0]
                    node_ids = []
                    ids = _get_ids(cur_elem)
            else:
                # not full circle
                if ways: # check if there is something to work on
                    last_id = node_ids[-1]
                    first_id = node_ids[0]
                    if _get_way(last_id, way_by_first_node):
                        cur_elem = _get_way(last_id, way_by_first_node)
                        ids = _get_ids(cur_elem)

                    elif _get_way(last_id, way_by_last_node):
                        cur_elem = _get_way(last_id, way_by_last_node)
                        ids = reversed(_get_ids(cur_elem))

                    elif _get_way(first_id, way_by_first_node):
                        cur_elem = _get_way(first_id, way_by_first_node)
                        node_ids = reversed(node_ids)
                        ids = _get_ids(cur_elem)

                    elif _get_way(first_id, way_by_last_node):
                        cur_elem = _get_way(first_id, way_by_last_node)
                        node_ids = reversed(node_ids)
                        ids = reversed(cur_elem)
                    else:
                        raise ValueError("Broken geometry for relation: %s", soup['id'])
                else: # if ways
                    raise ValueError("Broken geometry for relation: %s", soup['id'])
        # end while
        return ret

                
def main():
    odb = OsmDb(open("adresy.osm").read())
    print(list(odb.nearest((53.5880600, 19.5555200), 10)))


if __name__ == '__main__':
    main()
