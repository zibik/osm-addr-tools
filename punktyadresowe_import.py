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
import json
from bs4 import BeautifulSoup
#from pyproj import Proj
#from mapping import addr_map


# stałe
#_EPSG2180 = Proj(init='epsg:2180')

# User-Agent dla requestów
__opener = urequest.build_opener()
__headers = { 
    'User-Agent': 'Mozilla/5.0 (Windows NT 5.1; rv:10.0.2) Gecko/20100101 Firefox/10.0.2',
}
__opener.addheaders = __headers.items()

# setup
urequest.install_opener(__opener)



def getInit(gmina_url):
    url = gmina_url + '/application/system/init.php'
    print(url)
    data = urlopen(url).read().decode('utf-8')
    init_data = json.loads(data)
    
    # konwersja z EPSG:2180 na lon/lat
    #(w, s) = _EPSG2180(init_data['spatialExtent'][0], init_data['spatialExtent'][1], inverse=True)
    #(e, n) = _EPSG2180(init_data['spatialExtent'][2], init_data['spatialExtent'][3], inverse=True)

    bbox = {
        'minx': init_data['spatialExtent'][0], 'miny': init_data['spatialExtent'][1],
        'maxx': init_data['spatialExtent'][2], 'maxy': init_data['spatialExtent'][3],
    }

    address = list(
                filter(
                    lambda x: x['title'] == 'Adresy i ulice',
                    init_data['map']['services']
                )
        )[0]['address']
    
    #getBBoxCap(address)
    return {
        'bbox': bbox,
        'wms_addr': address,
        'terc': init_data['teryt'],
    }

# zwraca bbox w EPSG:2180
def getBBox(wms_addr):
    params = {
        'VERSION': '1.1.1',
        'SERVICE': 'WMS',
        'REQUEST': 'GetCapabilities',
        'FORMAT': 'application/vnd.ogc.wms_xml',
    }
    url = "%s&%s" % (wms_addr, urlencode(params),)
    data = urlopen(url).read()
    soup = BeautifulSoup(data)
    bbox = soup.wmt_ms_capabilities.find(name='boundingbox', srs='EPSG:2180')

    return {
        'minx': bbox['minx'], 'miny': bbox['miny'], 
        'maxx': bbox['maxx'], 'maxy': bbox['maxy']
    }


def fetchTiles(wms_addr, bbox):
    return analyzePoints(
                fetchPoint(
                    wms_addr, 
                    bbox['minx'], bbox['miny'], bbox['maxx'], bbox['maxy'], 
                    0, 0)) # sprawdź punkt (0,0) i tak powinno zostać zwrócone wszystko
                        

def fetchPoint(wms_addr, w, s, e, n, pointx, pointy):
    params = {
        'VERSION': '1.1.1',
        'SERVICE': 'WMS',
        'REQUEST': 'GetFeatureInfo',
        'LAYERS': 'punkty', # było: ulice,punkty
        'QUERY_LAYERS': 'punkty', # było: ulice, punkty
        'FORMAT': 'image/png',
        'INFO_FORMAT': 'text/html',
        'SRS': 'EPSG:2180',
        'FEATURE_COUNT': '10000000', # wystarczająco dużo, by ogarnąć każdą sytuację
        'WIDTH': 2,
        'HEIGHT': 2,
        'BBOX': '%s,%s,%s,%s' % (w, s, e, n),
        'X': pointx,
        'Y': pointy,
    }

    url = "%s&%s" % (wms_addr, urlencode(params))
    print(url)
    data = urlopen(url).read()
    return data

def analyzePoints(html):
    soup = BeautifulSoup(html)
    ret = {}
    for i in soup.find_all('table'):
        point = analyzePoint(i)
        ret[tuple(point['location'].values())] = point
    return ret

def analyzePoint(soup):
    kv = dict(zip(
        map(lambda x: x.text, soup.find_all('th')),
        map(lambda x: x.text, soup.find_all('td'))
    ))
    try:
        (lat, lng) = map(lambda x: x[2:], kv[str_normalize('GPS (WGS 84)')].split(', '))
        (str_name, str_id) = kv[str_normalize('Nazwa ulicy(Id GUS)')].rsplit('(')
        (city_name, city_id) = kv[str_normalize('Miejscowość(Id GUS)')].rsplit('(')

        ret = {
            'location': {'lat': lat, 'lng': lng},
            'addr:housenumber': kv[str_normalize('Numer')],
            'source:addr': kv[str_normalize('Źródło danych')],
        }
        if kv[str_normalize('Kod pocztowy')].strip():
            ret['addr:postcode'] = kv[str_normalize('Kod pocztowy')],

        if str_name.strip():
            ret['addr:street'] = str_name.strip()
            ret['teryt:sym_ul'] = str_id[:-1]
        else:
            ret['addr:place'] = city_name.strip()
        
        ret['addr:city'] = city_name.strip()
        ret['teryt:simc'] = city_id[:-1]
        return ret
    except KeyError:
        print(soup)
        print(kv)
        raise
    
def convertToOSM(dct):
    ret = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' upload='false' generator='punktyadresowe_import.php'>
"""
    for (node_id, val) in enumerate(dct.values()):
        ret += '<node id="-%s" action="modify" visible="true" lat="%s" lon="%s">\n' % (node_id+1, 
                                                                        val['location']['lat'], 
                                                                        val['location']['lng'])
        for i in ('addr:housenumber', 'source:addr', 'addr:postcode', 'addr:street', 'addr:city',
                    'teryt:sym_ul', 'addr:place', 'teryt:simc'):
            tagval = val.get(i)
            if tagval:
                ret += '<tag k="%s" v="%s" />\n' % (i, tagval)
        ret += '</node>\n'
    return ret

def main():
    if len(sys.argv) != 2:
        print("""punktyadresowe_import.py CC-BY-SA 3.0@WiktorN

Usage:
    punktyadresowe_importy.py [gmina]

Example:
    punktyadresowe_import.py milawa

Creates file [gmina].osm with result
""")
        sys.exit(1)
    gmina = sys.argv[1]
    conf = getInit('http://%s.e-mapa.net' % (gmina,))
    ret = fetchTiles(conf['wms_addr'], conf['bbox'])
    osm = convertToOSM(ret)
    with open(gmina+'.osm', "w+b") as f:
        f.write(osm.encode('utf-8'))

    with open(gmina+'.json', "w+") as f:
        json.dump(list(ret.values()), f)

if __name__ == '__main__':
    main()
