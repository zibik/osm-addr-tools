#!/usr/bin/env python3.4

from bs4 import BeautifulSoup
import sys

def makeEntry(soup):
    return {
        'id': soup['id'],
        'addr:city': soup.get('addr:city'),
        'addr:place': soup.get('addr:place'),
        'addr:street': soup.get('addr:street'),
        'addr:housenumber': soup.get('addr:housenumber'),
    }

def makeKey(dct):
    return (dct.get('addr:city'), dct.get('addr:place'), dct.get('addr:street'), dct.get('addr:housenumber'))

def markDuplicate(soup, dct):
    print("marking dups")
    node = soup.find(id=dct['id'])
    nt = soup.new_tag('tag', k='fixme', value='duplicate')
    node.append(nt)

def main():
    with open(sys.argv[1]) as f:
        soup = BeautifulSoup(f)

        ret = {}
        for i in soup.find_all(lambda x: int(x.get('id', 1)) < 0 and x.find('tag', k='addr:housenumber')):
            entry = makeEntry(i)
            key = makeKey(entry)
            try:
                lst = ret[key]
            except KeyError:
                lst = []
                ret[key] = lst
            lst.append(entry)

        for i in filter(lambda x: len(x) > 1, ret.values()):
            for j in i:
                markDuplicate(soup, j)
            
    with open("output.osm", "w+") as f:
        f.write(soup.prettify())

if __name__ == '__main__':
    main()
