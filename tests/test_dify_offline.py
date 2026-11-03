"""Exercise the real Dify adapter through an in-memory HTTP transport; no sockets or account quota."""
import io,json,math,sys,tempfile,time,unittest,uuid
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from urllib.error import HTTPError,URLError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import dify

class Transport:
 def __init__(self,value):self.value=value;self.requests=[]
 def open(self,req,timeout):
  self.requests.append((req,timeout))
  if isinstance(self.value,Exception):raise self.value
  return io.BytesIO(json.dumps(self.value).encode())
class DifyOfflineTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.data=Path(self.tmp.name)
  for item in [patch.object(dify,'DATA',self.data),patch.object(dify,'credentials',return_value='synthetic-local-test-key'),patch.object(dify.truststore,'inject_into_ssl')]:item.start();self.addCleanup(item.stop)
  self.dataset=str(uuid.uuid4());self.doc=str(uuid.uuid4())
  (self.data/'free-policy.json').write_text(json.dumps({'model':dify.MODEL,'provider':dify.PROVIDER,'free_only':True,'observed_used_credits':35,'local_total_credit_ceiling':60,'guard_start':0}))
  with closing(dify.connect()) as c:
   c.execute('INSERT INTO collections VALUES (?,?,?)',(self.dataset,'合成知识库',time.time()))
   c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?)',(self.dataset,'a'*64,'local-fixture','规则.md',self.doc,'fixture','completed'));c.commit()
 def transport(self,value):
  transport=Transport(value);p=patch.object(dify.request,'build_opener',return_value=transport);p.start();self.addCleanup(p.stop);return transport
 def result(self,doc=None,score=.8):return {'records':[{'score':score,'segment':{'id':'segment-1','content':'合成验收规则','position':1,'document':{'id':doc or self.doc,'name':'规则.md'}}}]}
 def test_cache_hit_does_not_make_a_second_request_or_reserve_more_credits(self):
  transport=self.transport(self.result());first=dify.capture([self.dataset],'验收');second=dify.capture([self.dataset],'验收')
  self.assertEqual(first['chunks'],second['chunks']);self.assertEqual(len(transport.requests),1)
  with closing(dify.connect()) as c:self.assertEqual(c.execute('SELECT COUNT(*),SUM(credit_reserve) FROM calls').fetchone()[:],(1,5))
 def test_expired_cache_is_not_reused(self):
  transport=self.transport(self.result());dify.capture([self.dataset],'验收')
  with closing(dify.connect()) as c:c.execute('UPDATE cache SET at=?',(time.time()-86401,));c.commit()
  dify.capture([self.dataset],'验收');self.assertEqual(len(transport.requests),2)
 def test_auth_payment_and_rate_errors_pause_without_retry_or_body_leak(self):
  for code in [401,402,403,429]:
   with self.subTest(code=code):
    with closing(dify.connect()) as c:c.execute('DELETE FROM calls');c.execute('DELETE FROM controls');c.commit()
    transport=self.transport(HTTPError('https://api.dify.ai/v1/datasets',code,'private provider body',{},io.BytesIO(b'synthetic-local-test-key PRIVATE')))
    with self.assertRaises(dify.DifyError) as caught:dify.capture([self.dataset],'失败'+str(code))
    self.assertNotIn('PRIVATE',str(caught.exception));self.assertNotIn('synthetic-local-test-key',str(caught.exception))
    with self.assertRaises(dify.DifyError):dify.capture([self.dataset],'再次失败')
    self.assertEqual(len(transport.requests),1)
 def test_timeout_keeps_uncertain_credit_reservation_and_does_not_retry(self):
  transport=self.transport(URLError('synthetic timeout'))
  with self.assertRaises(dify.DifyError):dify.capture([self.dataset],'超时')
  with closing(dify.connect()) as c:
   row=c.execute('SELECT status,credit_reserve FROM calls').fetchone();self.assertEqual(tuple(row),('uncertain',5))
  self.assertEqual(len(transport.requests),1)
 def test_local_ceiling_stops_before_transport(self):
  policy=json.loads((self.data/'free-policy.json').read_text());policy['observed_used_credits']=59;(self.data/'free-policy.json').write_text(json.dumps(policy))
  transport=self.transport(self.result())
  with self.assertRaises(dify.DifyError):dify.capture([self.dataset],'额度')
  self.assertFalse(transport.requests)
 def test_empty_and_unregistered_sources_are_explicitly_empty(self):
  for result in [{'records':[]},self.result(str(uuid.uuid4()))]:
   self.transport(result);value=dify.capture([self.dataset],str(uuid.uuid4()));self.assertEqual(value['status'],'empty');self.assertFalse(value['chunks'])
 def test_malformed_results_are_not_used_as_evidence(self):
  for value in [[],{'records':'bad'},{'records':[{'segment':{'content':123,'document':{}}}]}]:
   self.transport(value)
   with self.subTest(value=value),self.assertRaises(dify.DifyError):dify.capture([self.dataset],str(uuid.uuid4()))
 def test_duplicate_segments_are_deduplicated_and_scores_bounded(self):
  value=self.result(score=3);value['records']*=2;self.transport(value);result=dify.capture([self.dataset],'重复')
  self.assertEqual(len(result['chunks']),1);self.assertEqual(result['chunks'][0]['score'],1)
 def test_unknown_dataset_is_refused_without_transport(self):
  transport=self.transport(self.result())
  with self.assertRaises(dify.DifyError):dify.capture([str(uuid.uuid4())],'未知')
  self.assertFalse(transport.requests)
if __name__=='__main__':unittest.main()
