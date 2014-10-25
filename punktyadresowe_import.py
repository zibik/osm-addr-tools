#!/usr/bin/env python3.4
#
from mapping import addr_map
from urllib.request import urlopen
from urllib.parse import urlencode
import json
from pyproj import Proj
from bs4 import BeautifulSoup
import urllib.request
from wand.image import Image, Color
import math
from tempfile import NamedTemporaryFile
import skimage
import skimage.io


# stałe
_BLUE = Color('#00f')
_EPSG2180 = Proj(init='epsg:2180')

# User-Agent dla requestów
__opener = urllib.request.build_opener()
__opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (Windows NT 5.1; rv:10.0.2) Gecko/20100101 Firefox/10.0.2'),
]

# setup
urllib.request.install_opener(__opener)
skimage.io.use_plugin('freeimage')
        
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
    offset = 4000 # wielkość kafelka
    ret = {}

    for x in range(
            math.floor(bbox['minx']),
            math.ceil(bbox['maxx']), #ponieważ to jest i tak zachodni brzeg kafelka, to nie muszę dodawać offset
            offset):
        for y in range(
                math.floor(bbox['miny']), 
                math.ceil(bbox['maxy']), # ponieważ jest to południowy brzeg kafelka, to nie muszę dodawać offset
                offset):
            blob = fetchImage(wms_addr, x, y, offset)
            if len(blob) > 56208: #pusty PNG 4000x4000
            #if True:
                with Image(blob=blob) as image:
                    image.resize(math.floor(offset/2), math.floor(offset/2), filter='point')
                    for (rown, row) in enumerate(image):
                        for (coln, col) in enumerate(row):
                            #if col == _BLUE:
                            if col.red_int8 < 100: #trochę szybsze, niż sprawdzenie, czy niebieski
                                point = analyzePoint(fetchPoint(wms_addr, x, y, offset, coln*2, rown*2))
                                # jeden x,y jeden adres
                                #if tuple(point['location'].values()) in ret:
                                ret[tuple(point['location'].values())] = point
            return ret
    return ret                                
                        
            
def imageToSkimage(img):
    with NamedTemporaryFile(suffix='.png') as temp:
        temp.write(img.make_blob(format='png'))
        return skimage.io.imread(temp.name)

def fetchImage(wms_addr, x, y, offset):
    params = {
        'VERSION': '1.1.1',
        'SERVICE': 'WMS',
        'REQUEST': 'GetMap',
        'LAYERS': 'punkty',
        'FORMAT': 'image/png',
        'SRS': 'EPSG:2180',
        'WIDTH': offset,
        'HEIGHT': offset,
        'BBOX': '%s,%s,%s,%s' % (x, y, x+offset, y+offset),
    }
    url = "%s&%s" % (wms_addr, urlencode(params))
    print(url)
    data = urlopen(url)
    return data.read()

def fetchPoint(wms_addr, x, y, offset, pointx, pointy):
    params = {
        'VERSION': '1.1.1',
        'SERVICE': 'WMS',
        'REQUEST': 'GetFeatureInfo',
        'LAYERS': 'punkty', # było: ulice,punkty
        'QUERY_LAYERS': 'punkty', # było: ulice, punkty
        'FORMAT': 'image/png',
        'INFO_FORMAT': 'text/html',
        'SRS': 'EPSG:2180',
        'WIDTH': offset,
        'HEIGHT': offset,
        'BBOX': '%s,%s,%s,%s' % (x, y, x+offset, y+offset),
        'X': pointx,
        'Y': pointy,
    }

    url = "%s&%s" % (wms_addr, urlencode(params))
    print(url)
    data = urlopen(url).read()
    return data

def analyzePoint(html):
    soup = BeautifulSoup(html)
    kv = dict(zip(
        map(lambda x: x.text, soup.table.find_all('th')),
        map(lambda x: x.text, soup.table.find_all('td'))
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
    json.dump(ret.values(), open("test.json", "w+"))

if __name__ == '__main__':
    main()
