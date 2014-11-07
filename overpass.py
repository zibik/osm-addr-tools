from urllib.request import urlopen
from urllib.parse import urlencode


# these below server as documentation
__query_terc = """
<osm-script output="xml" timeout="600">
  <query type="area" into="boundryarea">
    <has-kv k="boundary" v="administrative"/>
    <has-kv k="admin_level" v="7"/> <!-- gmina -->
    <has-kv k="teryt:terc" regv="%s"/>
    <has-kv k="type" v="boundary"/>
  </query>
  <!-- gather results -->
  <union>
    <query type="node">
      <area-query from="boundryarea" />
      <has-kv k="addr:housenumber" modv="" v="" />
    </query>
    <query type="way">
      <area-query from="boundryarea" />
      <has-kv k="addr:housenumber" modv="" v=""/>
    </query>
    <query type="way">
      <area-query from="boundryarea" />
      <has-kv k="building" modv="" v=""/>
    </query>
    <query type="relation">
      <area-query from="boundryarea" />
      <has-kv k="addr:housenumber" modv="" v=""/>
    </query>
    <query type="relation">
      <area-query from="boundryarea" />
      <has-kv k="building" modv="" v=""/>
    </query>
  </union>
  <!-- print results -->
  <print mode="meta" order="quadtile" geometry="bounds" />
  <recurse type="down" />
  <print mode="meta" order="quadtile" />
</osm-script>
"""

# don't know why Overpass API converter leaves out geometry="bounds" (bb) after conversion
# remember to add it (bb before qt) by hand 
__overpass_ql_terc = """[out:xml][timeout:600];area["boundary"="administrative"]["admin_level"="7"]["teryt:terc"~"%s"]["type"="boundary"]->.boundryarea;(node(area.boundryarea)["addr:housenumber"];way(area.boundryarea)["addr:housenumber"];way(area.boundryarea)["building"];relation(area.boundryarea)["addr:housenumber"];relation(area.boundryarea)["building"];);out meta bb qt;>;out meta qt;"""

def getAddresses(terc):
    return query(__overpass_ql_terc % (terc,))


__query_ql_tag = """
[out:xml]
[timeout:600]
[maxsize:1073741824]
;
area
  ["boundary"="administrative"]
  ["admin_level"="2"]
  ["name"="Polska"]
  ["type"="boundary"]
->.boundryarea;
(
  node
    (area.boundryarea)
    %(tags)s;
  way
    (area.boundryarea)
    %(tags)s;
);
out;
"""

def getNodesWaysWithTags(taglist):
    tags = "\n\t".join(map(lambda x: '["' + x + '"]', taglist))
    return query(__query_ql_tag % {'tags': tags})

def getNodesWaysWithTag(tagname):
    return getNodesWaysWithTags([tagname, ])



__overpassurl = "http://overpass-api.de/api/interpreter"
__overpassurl = "http://overpass.osm.rambler.ru/cgi/interpreter"

def query(qry):
    # TODO - check if the query succeeded
    url = __overpassurl + '?' + urlencode({'data': qry.replace('\t', '').replace('\n', '')})
    return urlopen(url).read().decode('utf-8')

def main():
    ret = getAddresses("301204")
    with open("adresy.osm", "w+") as f:
        f.write(ret)

if __name__ == '__main__':
    main()
