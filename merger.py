#!/usr/bin/env python3.4

import argparse
import logging
import io
import itertools
import sys
from osmdb import OsmDb, get_soup_center
import json
import pyproj
from bs4 import BeautifulSoup
from shapely.geometry import Point
import shapely.geometry.base
from punktyadresowe_import import iMPA, GUGiK, Address
import overpass
from utils import parallel_execution

__log = logging.getLogger(__name__)

# depends FreeBSD
# portmaster graphics/py-pyproj devel/py-rtree devel/py-shapely www/py-beautifulsoup devel/py-lxml

# depeds Lubuntu:
# apt-get install python3-pyproj libspatialindex-dev python3-shapely python3-bs4 python3-lxml
# easy_install3 Rtree

# TODO: import admin_level=8 for area, and add addr:city if missing for addresses within that area (needs greater refactoring)
__geod = pyproj.Geod(ellps="WGS84")

class OsmAddress(Address):
    def __init__(self, soup, *args, **kwargs):
        super(OsmAddress, self).__init__(*args, **kwargs)
        self._soup = soup
        self.state = None
        self._resetChanges()

    @staticmethod
    def from_soup(obj):
        cache = dict((str(tag['k']), str(tag['v'])) for tag in obj.find_all('tag'))

        ret = OsmAddress(
            housenumber = cache.get('addr:housenumber', ''),
            postcode    = cache.get('addr:postcode', ''),
            street      = cache.get('addr:street', ''),
            city        = cache.get('addr:city', '') if cache.get('addr:street') else cache.get('addr:place', ''),
            sym_ul      = cache.get('teryt:symul', ''),
            simc        = cache.get('teryt:simc', ''),
            source      = cache.get('source:addr', ''),
            location    = dict(zip(('lat', 'lon'), get_soup_center(obj))),
            soup        = obj
        )

        fixme = cache.get('fixme')
        if fixme:
            ret.addFixme(fixme)
        if obj.get('action'):
            ret.state = obj['action']
        return ret

    @staticmethod
    def from_address(address, soup):
        ret = OsmAddress(
            housenumber = address.housenumber,
            postcode    = address.postcode,
            street      = address.street,
            city        = address.city,
            sym_ul      = address.sym_ul,
            simc        = address.simc,
            source      = address.source,
            location    = address.location,
            soup        = soup
        )
        if address.getFixme():
            ret.addFixme(address.getFixme())
        return ret

    #def __setattr__(self, name, value):
    #    if not name.startswith('_'):
    #        self._changed = True
    #    return super(OsmAddress, self).__setattr__(name, value)

    def set_state(self, val):
        if val == 'visible' and self.state not in ('modify', 'delete'):
            self.state = val
        elif val == 'modify' and self.state != 'delete':
            self.state = val
        elif val == 'delete':
            self.state = val
        else:
            # mark change
            self.state = self.state

    def _resetChanges(self):
        self._changed = False

    @property
    def center(self):
        node = self._soup
        try:
            return Point(float(node['lon']), float(node['lat']))
        except KeyError:
            b = node.bounds
            return Point(
                    (sum(map(float, (b['minlon'], b['maxlon']))))/2,
                    (sum(map(float, (b['minlat'], b['maxlat']))))/2,
            )

    def distance(self, other):
        return distance(self.center, other.center)

    @property
    def objtype(self):
        return self._soup.name

    def _getTagVal(self, key):
        tag = self._soup.find('tag', k=key)
        return str(tag['v']) if tag else None

    def _setTagVal(self, key, val):
        """returns True if something was modified"""
        n = self._soup.find('tag', k=key)
        if n:
            if n['v'] == val.strip():
                return False
            n['v'] = val.strip()
        else:
            new = BeautifulSoup().new_tag(name='tag', k=key, v=val.strip())
            self._soup.append(new)
        return True

    def _removeTag(self, key):
        rmv = self._soup.find('tag', k = key)
        if rmv:
            return bool(rmv.extract())
        return False

    def getshape(self):
        raise NotImplementedError

    @property
    def osmid(self):
        return "%s:%s" % (self.objtype, self._soup['id'])

    def isEMUiAAddr(self):
        ret = False
        if self.source:
            ret |= ('EMUIA' in source.upper())
        source_addr = self._getTagVal('source')
        if source_addr:
            ret |= ('EMUIA' in source_addr.upper())
        return ret

    def only_address_node(self):
        # return true, if its a node, has a addr:housenumber,
        # and consists only of tags listeb below
        if self.objtype != 'node':
            return False
        return self._getTagVal('addr:housenumber') and all(map(
            lambda x: x in {'addr:housenumber', 'addr:street', 'addr:place',
                            'addr:city', 'addr:postcode', 'addr:country', 'teryt:sym_ul',
                            'teryt:simc', 'source', 'source:addr', 'fixme', 'addr:street:source'},
            (x['k'] for x in self._soup.find_all('tag'))
            )
        )

    def is_new(self):
        return self._soup['id'] < 0

    def get_tag_soup(self):
        return self._soup.find_all('tag')

    def updateFrom(self, entry):
        def update(name):
            old = getattr(self, name)
            new = getattr(entry, name)
            if old and new and old != new:
                setattr(self, name, new)
                return True
            return False

        ret = False
        for name in ('street', 'city', 'housenumber', 'sym_ul', 'simc', 'source'):
            ret |= update(name)
        if entry.getFixme():
            self.addFixme(entry.getFixme())
            self._changed = True
            return True
        return ret

    @property
    def changed(self):
        return self._changed

    def to_osm_soup(self):
        if not self.housenumber:
            return self._soup
        else:
            ret = False
            if self.street:
                ret |= self._setTagVal('addr:city', self.city)
                ret |= self._setTagVal('addr:street', self.street)
                ret |= self._removeTag('addr:place')
            else:
                ret |= self._setTagVal('addr:place', self.city)
                ret |= self._removeTag('addr:street')
                ret |= self._removeTag('addr:city')

            ret |= self._setTagVal('addr:housenumber', self.housenumber)

            if self.getFixme():
                ret |= self._setTagVal('fixme', self.getFixme())

            if ret:
                self._setTagVal('source:addr', self.source)
                if bool(self._getTagVal('source')) and (self._getTagVal('source') == self.source or 'EMUIA' in self._getTagVal('source').upper()):
                    self._removeTag('source')

            if ret or self._changed:
                self._soup['action'] = 'modify'

            if self.state in ('delete', 'modify'):
                self._soup['action'] = self.state

            return self._soup


class Merger(object):
    __log = logging.getLogger(__name__).getChild('Merger')

    def __init__(self, impdata, asis, terc):
        self.impdata = impdata
        if isinstance(asis, BeautifulSoup):
            self.asis = asis
        else:
            self.asis = BeautifulSoup(asis)
        self.osmdb = OsmDb(self.asis, valuefunc=OsmAddress.from_soup, indexes={'address': lambda x: x.get_index_key(), 'id': lambda x: x.osmid})
        self._new_nodes = []
        self._updated_nodes = []
        self._node_id = 0
        self.pre_func = []
        self.post_func = []
        self._soup_visible = []
        self._import_area_shape = get_boundary_shape(terc)
        self._state_changes = []

    def _create_index(self):
        self._get_all_changed_nodes() # update everything in html, before we dump OsmAddress objects
        self.osmdb.update_index()

    def merge(self):
        self.__log.debug("Starting premerger functinos")
        self._pre_merge()
        self._create_index()
        self.__log.debug("Starting merge functinos")
        self._do_merge()
        self.__log.debug("Starting postmerge functinos")
        self._post_merge()

    def set_state(self, node, value):
        self._state_changes.append(node)
        node.set_state(value)

    def _pre_merge(self):
        for entry in self.impdata:
            self._fix_similar_addr(entry)
            tuple(map(lambda x: x(self, entry), self.pre_func))

    def _fix_similar_addr(self, entry):
        # look for near same address
        # change street names to values from OSM
        # change housenumbers to values from import
        try:
            node = next(filter(lambda x: entry.similar_to(x), self.osmdb.nearest(entry.center, num_results=10)))
            how_far = node.distance(entry)
            if node and node.street and entry.street and node.street != entry.street and \
                ((node.objtype == 'node' and how_far < 5.0) or (node.objtype == 'way' and (node.contains(entry.center) or how_far < 10.0))):
                # there is some similar address nearby but with different street name
                self.__log.warning("Changing street name from %s in import to %s as in OSM (%s), distance=%.2fm",
                        entry.street, node.street, node.osmid, how_far)
                # update street name based on OSM data
                entry.addFixme('Street name in import source: %s' % (entry.street,))
                entry.street = node.street
            if node and node.street == entry.street and node.city == entry.city and node.housenumber != entry.housenumber and \
                ((node.objtype == 'node' and how_far < 5.0) or (node.objtype == 'way' and (node.contains(entry.center) or how_far < 10.0))):
                # there is only difference in housenumber, that is similiar
                self.__log.info("Updating housenumber from %s to %s", node.housenumber, entry.housenumber)
                node.housenumber = entry.housenumber
        except StopIteration: pass

    def _fix_obsolete_emuia(self, entry):
        existing = self.osmdb.getbyaddress(entry.get_index_key())
        if existing:
            # we have something with this address in db
            # sort by distance
            emuia_nodes = sorted(tuple(filter(lambda x: x.isEMUiAAddr() and x.only_address_node(), existing)), lambda x: x.distance(entry))

            # update location of first node if from EMUiA
            if emuia_nodes:
                emuia_nodes[0].location = entry.location

            # all the others mark for deletion
            if len(emuia_nodes)>1:
                for node in emuia_nodes[1:]:
                    self.set_state(node, 'delete')

    def _do_merge(self):
        for entry in self.impdata:
            self._do_merge_one(entry)

    def _do_merge_one(self, entry):
        self.__log.debug("Processing address: %s", entry)
        return any(map(lambda x: x(entry),
            (
                # first returning true will stop exection of the chain
                self._do_merge_by_existing,
                self._do_merge_by_within,
                self._do_merge_by_nearest,
                self._do_merge_create_point,
            )
        ))

    def _do_merge_by_existing(self, entry):
        existing = self.osmdb.getbyaddress(entry.get_index_key())
        self.__log.debug("Found %d same addresses", len(existing))
        # create tuples (distance, entry) sorted by distance
        existing = sorted(map(lambda x: (x.distance(entry), x), existing), key=lambda x: x[0])
        if existing:
            # report duplicates
            if len(existing) > 1:
                self.__log.warning("More than one address node for %s. %s",
                                entry,
                                ", ".join("Id: %s, dist: %sm" % (x[1].osmid, str(x[0])) for x in existing)
                             )

            if max(x[0] for x in existing) > 100:
                for (dist, node) in existing:
                    if dist > 100:
                        if not (node.objtype in ( 'way', 'relation') and node.contains(entry.center)):
                            # ignore the distance, if the point is within the node
                            self.__log.warning("Address (id=%s) %s is %d meters from imported point", node.osmid, entry, dist)
                            node.addFixme("Node is %d meters away from imported point"  % dist)
                    self.set_state(node, 'visible')
                if min(x[0] for x in existing) > 50:
                    if any(map(lambda x: x[1].objtype in ('way', 'relation') and x.contains(entry.center), existing)):
                        # if any of existing addreses is a way/relation within which we have our address
                        # then skip
                        pass
                    else:
                        self.__log.debug("Creating address node, as closest address is farther than 50m")
                        self._create_point(entry)
                        return True
            # update data only on first duplicate, rest - leave to OSM-ers
            self._update_node(existing[0][1], entry)
            return True
        return False

    def _do_merge_by_within(self, entry):
        # look for building nearby
        candidates = list(self.osmdb.nearest(entry.center, num_results=10))
        candidates_within = list(filter(lambda x: x.objtype in ('way', 'relation') and x.contains(entry.center), candidates))

        self.__log.debug("Found %d buildings containing address", len(candidates_within))

        if candidates_within:
            c = candidates_within[0]
            if not c.housenumber:
                # no address on way/relation -> add address
                # create a point, will be merged with building later
                self.__log.debug("Creating address node as building contains no address")
                self._create_point(entry)
                return True
            else:
                # WARNING - candidate has an address
                if c.similar_to(entry) and c.street == entry.street:
                    self.__log.debug("Updating OSM address: %s with import %s", c.entry, entry)
                    self._update_node(c, entry)
                    return True
                else:
                    if c.similar_to(entry):
                        self.__log.info("Different street names - import: %s, OSM: %s, address: %s, OSM: %s", entry.street, c.street, entry, c.osmid)
                    # address within a building that has different address, add a point, maybe building needs spliting
                    self.__log.debug("Adding new node within building with address: %s", entry)
                    self._create_point(entry)
                    return True
        return False

    def _do_merge_by_nearest(self, entry):
        candidates = list(self.osmdb.nearest(entry.center, num_results=10))
        candidates_same = list(filter(lambda x: x.housenumber == entry.housenumber and \
            x.distance(entry) < 2.0, candidates))
        if len(candidates_same) > 0:
            # same location, both are an address, and have same housenumber, can't be coincidence,
            # probably mapper changed something
            for node in candidates_same:
                found = False
                if node.similar_to(entry):
                    found = True
                    self.__log.debug("Updating near node from: %s to %s", node.entry, entry)
                    self._update_node(node, entry)
                if found:
                    return True
            self.__log.info("Found probably same address node at (%s, %s). Skipping. Import address is: %s, osm addresses: %s",
                entry.location['lon'], entry.location['lat'], entry, ", ".join(map(str, candidates_same))
            )
            return True
        return False

    def _do_merge_create_point(self, entry):
        self._create_point(entry)
        return True

    def _update_node(self, node, entry):
        if node.updateFrom(entry):
            self.__log.debug("Updating node %s using %s", node.osmid, entry)
            self._updated_nodes.append(node)

    def _create_point(self, entry):
        self.__log.debug("Creating new point")
        ret = BeautifulSoup()
        node = ret.new_tag(name='node', id=self._get_node_id(), action='modify',
            lat=entry.location['lat'], lon=entry.location['lon'])
        self._new_nodes.append(OsmAddress.from_address(entry, node))
        self.asis.osm.append(node)

    def _mark_soup_visible(self, obj):
        self._soup_visible.append(obj)

    def _get_node_id(self):
        self._node_id -= 1
        return self._node_id

    def _get_all_changed_nodes(self):
        ret = dict((x.osmid,x) for x in self._updated_nodes)
        ret.update(dict((x.osmid, x) for x in self._new_nodes))
        self.__log.info("Modified objects: %d", len(ret))
        ret.update(dict((x.osmid, x) for x in self._state_changes))

        for (_id, i) in ret.items():
            if i in self._updated_nodes:
                self.__log.debug("Processing updated node: %s", str(i))
            elif i in self._new_nodes:
                self.__log.debug("Processing new node: %s", str(i))
            elif i.changed or i.state in ('modify', 'delete'):
                self.__log.debug("Processing node - changed: %s, state: %s; %s", i.changed, i.state, str(i))

        return tuple(map(lambda x: x.to_osm_soup(), ret.values()))

    def _get_all_visible(self):
        return tuple(map(lambda x: x.to_osm_soup(), self._soup_visible))

    def _get_all_reffered_by(self, lst):
        ret = set()
        def getbyid(key):
            ret = self.osmdb.getbyid(key)
            if not ret:
                raise ValueError("No object found for key: %s" % (key,))
            return ret
        def get_reffered(node):
            if node.name == 'node':
                return set((('node', node['id']),))
            if node.name == 'nd':
                return set((('node', node['ref']),))
            if node.name == 'way':
                return itertools.chain(
                    itertools.chain.from_iterable(map(get_reffered, node.find_all('nd'))),
                    (('way', node['id']),)
                )
            if node.name == 'member':
                return get_reffered(getbyid("%s:%s" % (node['type'], node['ref']))[0].to_osm_soup())
            if node.name == 'relation':
                return itertools.chain(
                    itertools.chain.from_iterable(map(get_reffered, node.find_all('member'))),
                    (('relation', node['id']),)
                )
            raise ValueError("Unkown node type: %s" % (node.name))

        for i in lst:
            ret = ret.union(get_reffered(i))

        return tuple(map(
            lambda x: getbyid("%s:%s" % (x[0], x[1]))[0].to_osm_soup(),
            ret
        ))

    def _post_merge(self):
        # recreate index
        self._create_index()

        self.mark_not_existing()
        for i in self.post_func:
            i()

        self._get_all_changed_nodes()

    def mark_not_existing(self):
        imp_addr = set(map(lambda x: x.get_index_key(), self.impdata))
        # from all addresses in OsmDb remove those imported
        to_delete = set(filter(
            lambda x: any(
                map(lambda y: self._import_area_shape.contains(y.center), self.osmdb.getbyaddress(x))
            ),
            self.osmdb.getalladdress())) - imp_addr

        self.__log.debug("Marking %d not existing addresses", len(to_delete))
        for addr in to_delete:
            for node in self.osmdb.getbyaddress(addr):
                if node.only_address_node() and self._import_area_shape.contains(node.center):
                    # report only points within area of interest
                    self.__log.debug("Marking node to delete: %s, %s", node.osmid, str(node.entry))
                    node.addFixme('Check address existance')

    def merge_addresses(self):
        self._merge_addresses_buffer(0)
        # TODO: to_osm_soup?
        self._merge_addresses_buffer(2)
        self._merge_addresses_buffer(5)
        self._merge_addresses_buffer(10)

    def _merge_one_address(self, building, addr):
        # as we merge only address nodes, do not pass anything else
        for tag in addr.get_tag_soup():
            building.append(tag)
        self.set_state(addr, 'delete')
        self._updated_nodes.append(self.osmdb.getbyid("%s:%s" % (building.name, building['id']))[0])

    def _merge_addresses_buffer(self, buf=0):
        self.__log.info("Merging building with buffer: %d", buf)
        to_merge = self._prepare_merge_list(buf)
        buildings = dict(
            ("%s:%s" % (x.name, x['id']), x) for x in self.asis.osm.find_all(['way', 'relation'], recursive=False)
        )

        self.__log.info("Merging %d addresses with buildings", len(tuple(filter(lambda x: len(x[1]) == 1, to_merge.items()))))

        for (_id, nodes) in to_merge.items():
            building = buildings[_id]
            if len(nodes) > 0:
                self._mark_soup_visible(self.osmdb.getbyid(_id)[0])

            if len(nodes) == 1:
                if building.find('tag', k='addr:housenumber'):
                    self.__log.info("Skipping merging address: %s, as building already has an address: %s.", str(nodes[0].entry), OsmAddress.from_soup(building))
                    self._mark_soup_visible(nodes[0])
                else:
                    self.__log.debug("Merging address %s with building %s", str(nodes[0].entry), _id)
                    self._merge_one_address(building, nodes[0])

            if len(nodes) > 1:
                for node in nodes:
                    self._mark_soup_visible(node)

    def _prepare_merge_list(self, buf):
        ret = {}
        for node in self.asis.osm.find_all(lambda x: x.name == 'node' and x.get('action') != 'delete' and x.find('tag', k='addr:housenumber'), recursive=False):
            addr = self.osmdb.getbyid("%s:%s" % (node.name, node['id']))[0]
            self.__log.debug("Looking for candidates for: %s", str(addr.entry))
            if addr.only_address_node() and addr.state != 'delete' and self._import_area_shape.contains(addr.center):
                candidates = list(self.osmdb.nearest(addr.center, num_results=10))
                candidates_within = list(
                    filter(
                        lambda x: addr.osmid != x.osmid and x.objtype == 'relation' and addr.center.within(buffer(x.shape, buf)),
                        candidates
                    )
                )
                if not candidates_within:
                    candidates_within = list(
                        filter(
                            lambda x: addr.osmid != x.osmid and x.objtype == 'way' and addr.center.within(buffer(x.shape, buf)),
                            candidates
                        )
                   )
                if candidates_within:
                    c = candidates_within[0]
                    if c.housenumber:
                        self.set_state(c, 'visible')
                    else:
                        try:
                            lst = ret[c.osmid]
                        except KeyError:
                            lst = []
                            ret[c.osmid] = lst
                        lst.append(addr)
                        self.__log.debug("Found: %s", c.osmid)
        return ret

    def get_incremental_result(self, logIO=None):
        ret = getEmptyOsm(self.asis.meta)
        osm = ret.osm
        changes = self._get_all_changed_nodes()
        self.__log.info("Generated %d changes", len(changes))
        for i in self._get_all_reffered_by(changes + self._get_all_visible()):
            osm.append(i)

        if logIO:
            ret.osm.note.string += logIO.getvalue()

        return ret.prettify()

    def get_full_result(self, logIO=None):
        self._get_all_changed_nodes()
        if logIO:
            ret.osm.note.string += logIO.getvalue()
        return self.asis.prettify()


def getAddresses(bbox):
    bbox = ",".join(bbox)
    query = """
[out:xml]
[timeout:600]
;
(
  node
    (%s)
    ["addr:housenumber"]
    ["amenity"!~"."]
    ["shop"!~"."]
    ["tourism"!~"."]
    ["emergency"!~"."]
    ["company"!~"."];
  way
    (%s)
    ["addr:housenumber"];
  way
    (%s)
    ["building"];
  relation
    (%s)
    ["addr:housenumber"];
  relation
    (%s)
    ["building"];
);
out meta bb qt;
>;
out meta bb qt;
""" % (bbox, bbox, bbox, bbox, bbox,)
    return overpass.query(query)

def get_boundary_shape(terc):
    query = """
[out:xml]
[timeout:600];
relation
    ["teryt:terc"~"%s"];
out bb;
>;
out bb;
""" % (terc,)
    soup = BeautifulSoup(overpass.query(query))
    osmdb = OsmDb(soup)
    return osmdb.get_shape(soup.find('relation'))


def distance(a, b):
    """returns distance betwen a and b points in meters"""
    if isinstance(a, shapely.geometry.base.BaseGeometry):
        a = (a.y, a.x)
    if isinstance(b, shapely.geometry.base.BaseGeometry):
        b = (b.y, b.x)
    return __geod.inv(a[1], a[0], b[1], b[0])[2]

def buffer(shp, meters=0):
    # 0.0000089831528 is the 1m length in arc degrees of great circle
    return shp.buffer(meters*0.0000089831528)

def getEmptyOsm(meta):
    ret = BeautifulSoup()
    osm = ret.new_tag('osm', version="0.6", generator="import adresy merge.py")
    ret.append(osm)
    nt = ret.new_tag('note')
    nt.string = 'The data included in this document is from www.openstreetmap.org. The data is made available under ODbL.'
    ret.osm.append(nt)
    ret.osm.append(meta)
    return ret

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
    source_group.add_argument('--gugik', action='store_const', const=True, dest='gugik', default=False, help='Import address data from gugik. Select area by providing terc option')
    address_group = parser.add_argument_group()
    address_group.add_argument('--addresses-file', type=argparse.FileType("r", encoding='UTF-8'), help='OSM file with addresses and buildings for imported area', dest='addresses_file')
    address_group.add_argument('--terc', help='teryt:terc code, for which to download addresses from OSM using Overpass API')
    parser.add_argument('--output', type=argparse.FileType('w+', encoding='UTF-8'), help='output file with merged data (default: result.osm)', default='result.osm')
    parser.add_argument('--full', help='Use to output all address data for region, not only modified address data as per default', action='store_const', const=True, dest='full_mode', default=False)
    parser.add_argument('--log-level', help='Set logging level (debug=10, info=20, warning=30, error=40, critical=50), default: 20', dest='log_level', default=20, type=int)
    parser.add_argument('--import-wms', help='WMS address for address layer, ex: ' +
        'http://www.punktyadresowe.pl/cgi-bin/mapserv?map=/home/www/impa2/wms/luban.map . Bounding box is still fetched via iMPA', dest='wms')

    args = parser.parse_args()

    log_stderr = logging.StreamHandler()
    log_stderr.setLevel(args.log_level)

    logIO = io.StringIO()

    logging.basicConfig(level=10, handlers=[log_stderr, logging.StreamHandler(logIO)])

    dataFunc = None
    if args.impa:
        imp = iMPA(args.impa, wms=args.wms)
        terc = imp.terc
        dataFunc = lambda: imp.getAddresses()
    else:
        imp = GUGiK(args.terc)
        dataFunc = lambda: imp.getAddresses()

    if args.import_file:
        dataFunc = lambda: list(map(lambda x: Address.from_JSON(x), json.load(args.import_file)))

    data = dataFunc()

    if args.terc:
        terc = args.terc

    __log.info("Working with TERC: %s", terc)
    if args.addresses_file:
        addrFunc = lambda: args.addresses_file.read()
    else:
        # union with bounds of administrative boundary
        s = min(map(lambda x: x.center.y, data))
        w = min(map(lambda x: x.center.x, data))
        n = max(map(lambda x: x.center.y, data))
        e = max(map(lambda x: x.center.x, data))
        addrFunc = lambda: getAddresses(map(str,(s, w, n, e)))

    addr = addrFunc()

    if len(data) < 1:
        __log.warning("Warning - import data is empty. Check your import")
    __log.info('Processing %d addresses', len(data))

    if 'node' not in addr:
        __log.warning("Warning - address data is empty. Check your file/terc code")

    m = Merger(data, addr, terc)
    m.post_func.append(m.merge_addresses)
    m.merge()

    if args.full_mode:
        ret = m.get_full_result(logIO)
    else:
        ret = m.get_incremental_result(logIO)

    args.output.write(ret)

if __name__ == '__main__':
    main()
