from flask import Flask, make_response as _make_response
from merger import mergeInc, mergeFull, getAddresses
from punktyadresowe_import import iMPA
import utils
import logging

app = Flask(__name__)

def make_response(ret, code):
    resp = _make_response(ret, code)
    resp.mimetype ='text/xml; charset=utf-8'
    return resp

@app.route("/osm/adresy/iMPA/<name>.osm", methods=["GET", ])
def differentialImport(name):
    imp = iMPA(name)
    terc = imp.getConf()['terc']

    (addr, data) = utils.parallel_execution(lambda: getAddresses(terc), imp.fetchTiles)
    
    ret = mergeInc(addr, data)
    
    return make_response(ret, 200)

@app.route("/osm/adresy/iMPA_full/<name>.osm", methods=["GET", ])
def fullImport(name):
    imp = iMPA(name)
    terc = imp.getConf()['terc']

    (addr, data) = utils.parallel_execution(lambda: getAddresses(terc), imp.fetchTiles)
    
    ret = mergeFull(addr, data)
    
    return make_response(ret, 200)

if __name__ == '__main__':
    ADMINS = ['logi-osm@vink.pl']
    if not app.debug:
        from logging.handlers import SMTPHandler
        mail_handler = SMTPHandler('127.0.0.1',
                                   'server-error@vink.pl',
                                   ADMINS, 'OSM Rest-Server Failed')
        mail_handler.setLevel(logging.INFO)
        app.logger.addHandler(mail_handler)
    app.run(host='0.0.0.0', port=5001, debug=False)
