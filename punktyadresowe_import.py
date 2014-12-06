#!/usr/bin/env python3.4
# # -*- coding: UTF-8 -*-
#
# punktyadresowe_import.py CC-BY-NC-SA 3.0 WiktorN
#
# Based on work by Grzegorz Sapijaszko (http://wiki.openstreetmap.org/wiki/User:Gsapijaszko/punktyadresowe_import)
#
# dependencies:
# Beautiful-Soup (http://www.crummy.com/software/BeautifulSoup/)
#       pip install beautifulsoup4 
#       easy_install beautifulsoup4
#       apt-get install python-beautifulsoup4
#       portmaster www/py-beautifulsoup
#
# TODO:
# - extract validations (mixed addressing scheme) to external module

import sys
if sys.version_info.major == 2:
    from urllib import urlencode
    from urllib2 import urlparse, urlopen
    import urllib2 as urequest
    str_normalize = lambda x: x.decode('utf-8')
else:
    from urllib.parse import urlencode, urlparse
    import urllib.request as urequest
    from urllib.request import urlopen
    str_normalize = lambda x: x
import logging
import argparse
import json
from bs4 import BeautifulSoup
from mapping import mapstreet, mapcity
from utils import parallel_execution
from functools import partial
from collections import Counter, namedtuple


# stałe
#_EPSG2180 = Proj(init='epsg:2180')

__log = logging.getLogger(__name__)
# User-Agent dla requestów
__opener = urequest.build_opener()
__headers = { 
    'User-Agent': 'Mozilla/5.0 (Windows NT 5.1; rv:10.0.2) Gecko/20100101 Firefox/10.0.2',
}
__opener.addheaders = __headers.items()


# setup
urequest.install_opener(__opener)

def _filterOnes(lst):
    return list(filter(lambda x: x > 0, lst))

def markSuspiciousAddr(dct):
    dups = {}
    for addr in dct.values():
        v = bool(addr.get('addr:street', '').strip())
        try:
            lst = dups[addr['teryt:simc']]
        except KeyError:
            lst =[]
            dups[addr['teryt:simc']] = lst
        lst.append(v)
    
    dups_count = dict((k, len(_filterOnes(v))) for k, v in dups.items())
    dups = dict((k, len(_filterOnes(v))/len(v)) for k, v in dups.items())
    dups = dict((k,v) for k, v in filter(lambda x: 0 < x[1] and x[1] < 1, dups.items()))

    for i in filter(
            lambda x: bool(x.get('addr:place')),
            filter(
                lambda x: x['teryt:simc'] in dups.keys(), 
                dct.values()
                )
            ):
        i['fixme'] = 'Mixed addressing scheme in city - with streets and without. %.1f%% (%d) with streets.' % (dups[i['teryt:simc']]*100, dups_count[i['teryt:simc']])

    return dct

def analyzePoints(html):
    soup = BeautifulSoup(html)
    ret = {}
    for i in soup.find_all('table'):
        point = analyzePoint(i)
        ret[tuple(point['location'].values())] = point
    
    return markSuspiciousAddr(ret)

def analyzePoint(soup):
    kv = dict(zip(
        map(lambda x: x.text, soup.find_all('th')),
        map(lambda x: x.text, soup.find_all('td'))
    ))
    try:
        (lon, lat) = map(lambda x: x[2:], kv[str_normalize('GPS (WGS 84)')].split(', ', 1))
        (str_name, str_id) = kv[str_normalize('Nazwa ulicy(Id GUS)')].rsplit('(', 1)
        (city_name, city_id) = kv[str_normalize('Miejscowość(Id GUS)')].rsplit('(', 1)

        if float(lon) < 14 or float(lon) > 25 or float(lat) < 49 or float(lat) > 56:
            __log.warning("Point out of Polish borders: (%s, %s), %s, %s, %s", lat, lon, city_name, str_name, kv[str_normalize('Numer')])

        ret = {
            'location': {'lat': lat, 'lon': lon},
            'addr:housenumber': kv[str_normalize('Numer')],
            'source:addr': kv[str_normalize('Źródło danych')],
        }
        if kv[str_normalize('Kod pocztowy')].strip():
            ret['addr:postcode'] = kv[str_normalize('Kod pocztowy')]

        if str_name.strip():
            ret['addr:street'] = mapstreet(str_name.strip().replace('  ', ' '), str_id[:-1])
            ret['teryt:sym_ul'] = str_id[:-1]
            ret['addr:city'] = mapcity(city_name.strip(), city_id[:-1])
        else:
            ret['addr:place'] = mapcity(city_name.strip(), city_id[:-1])
        
        ret['teryt:simc'] = city_id[:-1]
        return ret
    except KeyError:
        __log.error(soup)
        __log.error(kv)
        __log.error("Exception during point analysis", exc_info=True)
        raise
    except ValueError:
        __log.error(soup)
        __log.error(kv)
        __log.error("Exception during point analysis", exc_info=True)
        raise
    
def convertToOSM(lst):
    ret = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' upload='false' generator='punktyadresowe_import.php'>
"""
    ret = BeautifulSoup("", "xml")
    osm = ret.new_tag('osm', version='0.6', upload='false', generator='punktyadresowe_import.py')
    ret.append(osm)

    for (node_id, val) in enumerate(lst):
        osm.append(val.asOsmSoup(-1 * (node_id + 1)))
    return ret.prettify()

class Address(object): #namedtuple('BaseAddress', ['housenumber', 'postcode', 'street', 'city', 'sym_ul', 'simc', 'source', 'location'])):
    def __init__(self, housenumber='', postcode='', street='', city='', sym_ul='', simc='', source='', location=''):
        #super(Address, self).__init__(*args, **kwargs)
        self.housenumber = housenumber
        self.postcode = postcode
        self.street = mapstreet(street.replace('  ', ' '), sym_ul)
        self.city = mapcity(city, simc)
        self.sym_ul = sym_ul
        self.simc = simc
        self.source = source
        self.location = location
        self._fixme = []
        assert all(map(lambda x: isinstance(x, str), (self.housenumber, self.postcode, self.street, self.city, self.sym_ul, self.simc, self.source)))
        assert isinstance(self.location, dict)
        assert 'lon' in self.location
        assert 'lat' in self.location

    def asOsmSoup(self, node_id):
        ret = BeautifulSoup("", "xml")
        node = ret.new_tag('node', id=node_id, action='modify', visible='true', lat=self.location['lat'], lon=self.location['lon'])
        node.append(ret.new_tag('tag', k='addr:housenumber', v=self.housenumber))
        if self.postcode:
            node.append(ret.new_tag('tag', k='addr:postcode', v=self.postcode))
        if self.street:
            node.append(ret.new_tag('tag', k='addr:street', v=self.street))
            node.append(ret.new_tag('tag', k='addr:city', v=self.city))
        else:
            node.append(ret.new_tag('tag', k='addr:place', v=self.city))

        node.append(ret.new_tag('tag', k='addr:simc', v=self.simc))
        node.append(ret.new_tag('tag', k='addr:source', v=self.source))
        if self._fixme:
            node.append(ret.new_tag('tag', k='fixme', v=" ".join(self.fixme)))
        return node

    def osOsmXML(self, node_id):
        return asOsmSoup.prettify()

class iMPA(object):
    __log = logging.getLogger(__name__).getChild('iMPA')

    def _getInit(self, gmina_url):
        url = gmina_url + '/application/system/init.php'
        self.__log.info(url)
        data = urlopen(url).read().decode('utf-8')
        init_data = json.loads(data)
        
        # konwersja z EPSG:2180 na lon/lat
        #(w, s) = _EPSG2180(init_data['spatialExtent'][0], init_data['spatialExtent'][1], inverse=True)
        #(e, n) = _EPSG2180(init_data['spatialExtent'][2], init_data['spatialExtent'][3], inverse=True)

        bbox = {
            'minx': init_data['spatialExtent'][0], 'miny': init_data['spatialExtent'][1],
            'maxx': init_data['spatialExtent'][2], 'maxy': init_data['spatialExtent'][3],
        }



        ret = {
            'bbox': bbox,
            'terc': init_data['teryt'],
            'srs': 'EPSG:2180',
        }

        address_layers = list(
                    filter(
                        lambda x: x['title'] and x['title'].upper() == 'ADRESY I ULICE',
                        init_data['map']['services']
                    )
            )
        if len(address_layers) == 0:
            self.__log.warning('No information about address layer in init.php')
            self.__log.debug(data)
        else:
            ret['wms_addr'] = address_layers[0]['address']
        return ret

    def __init__(self, gmina=None, wms=None, bbox=None, srs=None):
        if gmina:
            self.conf = self._getInit('http://%s.e-mapa.net' % (gmina,))
        else:
            if not wms and not bbox and not srs:
                raise ValueError("If no gmina provided then wms and bbox are required")
            self.conf = {}
        if wms:
            self.conf['wms_addr'] = wms
        
        if bbox:
            self.conf['bbox'] = dict(zip(('minx', 'miny', 'maxx', 'maxy'), bbox.split(",")))

        if srs:
            self.conf['srs'] = srs

        if 'wms_addr' not in self.conf:
            raise ValueError("No WMS address found")

    def getConf(self):
        return self.conf

    def fetchPoint(self, wms_addr, w, s, e, n, pointx, pointy, srs="EPSG:2180", layer="punkty"):
        params = {
            'VERSION': '1.1.1',
            'SERVICE': 'WMS',
            'REQUEST': 'GetFeatureInfo',
            'LAYERS': layer, # było: ulice,punkty
            'QUERY_LAYERS': layer, # było: ulice, punkty
            'FORMAT': 'image/png',
            'INFO_FORMAT': 'text/html',
            'SRS': srs,
            'FEATURE_COUNT': '10000000', # wystarczająco dużo, by ogarnąć każdą sytuację
            'WIDTH': 2,
            'HEIGHT': 2,
            'BBOX': '%s,%s,%s,%s' % (w, s, e, n),
            'X': pointx,
            'Y': pointy,
        }
        
        josm_wms = {
            'VERSION': '1.1.1',
            'SERVICE': 'WMS',
            'REQUEST': 'GetMap',
            'LAYERS': layer,
            'FORMAT': 'image/png',
            'TRANSPARENT': 'true',
        }    

        #TODO: do proper URL parsing
        if '?' in wms_addr:
            url = "%s&%s" % (wms_addr, urlencode(params))
            self.__log.warning("JOSM layer: %s&%s&SRS={proj}&WIDTH={width}&HEIGHT={height}&BBOX={bbox}" % (wms_addr, urlencode(josm_wms)))
        else:
            url = "%s?%s" % (wms_addr, urlencode(params))
            self.__log.warning("JOSM layer: %s?%s&SRS={proj}&WIDTH={width}&HEIGHT={height}&BBOX={bbox}" % (wms_addr, urlencode(josm_wms)))
        self.__log.info(url)
        data = urlopen(url).read()
        return data

    def _convertToAddress(self, soup):
        kv = dict(zip(
            map(lambda x: x.text, soup.find_all('th')),
            map(lambda x: x.text, soup.find_all('td'))
        ))
        try:
            (lon, lat) = map(lambda x: x[2:], kv[str_normalize('GPS (WGS 84)')].split(', ', 1))
            (str_name, str_id) = kv[str_normalize('Nazwa ulicy(Id GUS)')].rsplit('(', 1)
            (city_name, city_id) = kv[str_normalize('Miejscowość(Id GUS)')].rsplit('(', 1)

            if float(lon) < 14 or float(lon) > 25 or float(lat) < 49 or float(lat) > 56:
                self.__log.warning("Point out of Polish borders: (%s, %s), %s, %s, %s", lat, lon, city_name, str_name, kv[str_normalize('Numer')])

            return Address(
                kv[str_normalize('Numer')],
                kv[str_normalize('Kod pocztowy')].strip(),
                str_name.strip(),
                city_name.strip(),
                str_id[:-1], # sym_ul
                city_id[:-1], # simc
                kv[str_normalize('Źródło danych')],
                {'lat': lat, 'lon': lon} # location
            )
        except KeyError:
            self.__log.error(soup)
            self.__log.error(kv)
            self.__log.error("Exception during point analysis", exc_info=True)
            raise
        except ValueError:
            self.__log.error(soup)
            self.__log.error(kv)
            self.__log.error("Exception during point analysis", exc_info=True)
            raise

    def fetchTiles(self):
        html = self.fetchPoint(
            self.conf['wms_addr'], 
            self.conf['bbox']['minx'], 
            self.conf['bbox']['miny'], 
            self.conf['bbox']['maxx'], 
            self.conf['bbox']['maxy'], 
            0, 0, # sprawdź punkt (0,0) i tak powinno zostać zwrócone wszystko
            self.conf['srs']
        )

        ret = list(map(self._convertToAddress, BeautifulSoup(html).find_all('table')))
                    
        for (addr, occurances) in Counter(map(
                lambda x: tuple((getattr(x, z) for z in ('city', 'housenumber', 'postcode', 'street'))),
                ret
            )).items():
            if occurances > 1:
                self.__log.warning("Duplicte addresses in import: %s", addr)
        return ret

def main():
    parser = argparse.ArgumentParser(description="Downloads data from iMPA and saves in OSM or JSON format. CC-BY-SA 3.0 @ WiktorN. Filename is <gmina>.osm or <gmina>.json")
    parser.add_argument('--output-format', choices=['json', 'osm'],  help='output file format - "json" or "osm", default: osm', default="osm", dest='output_format')
    parser.add_argument('--log-level', help='Set logging level (debug=10, info=20, warning=30, error=40, critical=50), default: 20', dest='log_level', default=20, type=int)
    parser.add_argument('--no-mapping', help='Disable mapping of streets and cities', dest='no_mapping', default=False, action='store_const', const=True)
    parser.add_argument('--wms', help='Override WMS address with address points', dest='wms', default=None)
    parser.add_argument('--bbox', 
        help='Provide bbox, where to look for addresses in format w,s,e,n. BBOX for Poland: 14.1229707,49.0020305,24.1458511,55.0336963',
        dest='bbox', default=None)
    parser.add_argument('--srs', help='SRS for bbox, defalts to EPSG:2180, use EPSG:4326 if you provide standard lon,lat', dest='srs', default='EPSG:2180')
    parser.add_argument('gmina', nargs='*',  help='list of iMPA services to download, it will use at most 4 concurrent threads to download and analyse')
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level)

    if args.no_mapping:
        global mapstreet, mapcity
        mapstreet = lambda x, y: x
        mapcity = lambda x, y: x

    impa_gen = partial(iMPA, wms=args.wms, bbox=args.bbox, srs=args.srs)
    if args.gmina:
        rets = parallel_execution(*map(lambda x: lambda: impa_gen(x).fetchTiles(), args.gmina))
        #rets = list(map(lambda x: impa_gen(x).fetchTiles(), args.gmina)) # usefull for debugging
    else:
        rets = [impa_gen().fetchTiles(),]
    if args.output_format == 'json':
        write_conv_func = lambda x: json.dumps(list(x))
        file_suffix = '.json'
    else:
        write_conv_func = convertToOSM
        file_suffix = '.osm'

    if args.gmina:
        for (ret, gmina) in zip(rets, args.gmina):
            with open(gmina+file_suffix, "w+", encoding='utf-8') as f:
                f.write(write_conv_func(ret))
    else:
        with open('result.osm', 'w+', encoding='utf-8') as f:
            f.write(write_conv_func(ret[0]))

if __name__ == '__main__':
    main()
