#!/usr/bin/env python3.4
#
from mapping import addr_map
from urllib.parse import urlencode, urlparse
import urllib.request
from urllib.request import urlopen
import json
from bs4 import BeautifulSoup
from pyproj import Proj


# stałe
_EPSG2180 = Proj(init='epsg:2180')

# User-Agent dla requestów
__opener = urllib.request.build_opener()
__headers = { 
    'User-Agent': 'Mozilla/5.0 (Windows NT 5.1; rv:10.0.2) Gecko/20100101 Firefox/10.0.2',
}
__opener.addheaders = __headers.items()

# setup
urllib.request.install_opener(__opener)

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
        (lat, lng) = map(lambda x: x[2:], kv['GPS (WGS 84)'].split(', '))
        (str_name, str_id) = kv['Nazwa ulicy(Id GUS)'].rsplit('(')
        (city_name, city_id) = kv['Miejscowość(Id GUS)'].rsplit('(')

        ret = {
            'location': {'lat': lat, 'lng': lng},
            'addr:househumber': kv['Numer'],
            'source:addr': kv['Źródło danych'],
        }
        if kv['Kod pocztowy'].strip():
            ret['addr:postcode'] = kv['Kod pocztowy'],

        if str_name.strip():
            ret['addr:street'] = str_name.strip()
            ret['addr:city'] = city_name.strip()
            ret['teryt:sym_ul'] = str_id[:-1]
        else:
            ret['addr:place'] = city_name.strip()
        
        ret['teryt:simc'] = city_id[:-1]

        return ret
    except KeyError:
        print(soup)
        print(kv)
        raise
    

def main():
    conf = getInit('http://milawa.e-mapa.net')
    ret = fetchTiles(conf['wms_addr'], conf['bbox'])
    json.dump(list(ret.values()), open("test.json", "w+"))

if __name__ == '__main__':
    main()
