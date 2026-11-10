"""The browser dashboard must reflect actual database state, not mock counters."""
import json
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from transfer_flow.dashboard import DashboardServer

def call(url, method='GET', payload=None):
    data=json.dumps(payload or {}).encode() if method=='POST' else None
    with urlopen(Request(url,data=data,method=method,headers={'Content-Type':'application/json'}),timeout=4) as r:
        return r.status, r.read(), r.headers

def test_dashboard_loads_and_reconciles_real_ledger():
    with DashboardServer(port=0) as app:
        status,body,headers=call(app.url)
        assert status==200 and b'Transfer overview' in body and 'text/html' in headers['Content-Type']
        call(app.url+'/api/reset','POST')
        status,body,_=call(app.url+'/api/submit','POST')
        result=json.loads(body)
        assert result['status']=='DONE'
        assert len(result['results'])==12
        state=json.loads(call(app.url+'/api/state')[1])
        assert state['summary']==app.state.ledger.summary()
        assert state['summary']['currentTotal']=='17300.00'
        assert len(state['accounts'])==5
        assert len(state['batches'])==1
        assert sum(x['status']=='ACCEPTED' for x in result['results'])==4
        # Repeated submission reuses the same batch and doesn't move money.
        again=json.loads(call(app.url+'/api/submit','POST')[1])
        assert again['batchId']==result['batchId']
        assert len(json.loads(call(app.url+'/api/state')[1])['batches'])==1

def test_dashboard_rollback_is_real_and_reset_recovers():
    with DashboardServer(port=0) as app:
        call(app.url+'/api/reset','POST')
        call(app.url+'/api/fault','POST',{'failOnWrite':2})
        result=json.loads(call(app.url+'/api/submit','POST')[1])
        assert result['status']=='FAILED'
        state=json.loads(call(app.url+'/api/state')[1])
        assert state['summary']['currentTotal']=='17300.00'
        assert app.state.ledger.db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]==0
        call(app.url+'/api/reset','POST')
        assert json.loads(call(app.url+'/api/submit','POST')[1])['status']=='DONE'

def test_dashboard_rejects_bad_fault_configuration():
    with DashboardServer(port=0) as app:
        try: call(app.url+'/api/fault','POST',{'failOnWrite':0})
        except HTTPError as exc: assert exc.code==400
        else: assert False, 'Invalid fault must be rejected'
