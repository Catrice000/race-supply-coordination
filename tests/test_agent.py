"""Agent顺序、权限、幂等与确认回归。模型由脚本替身控制，不调用计费API。"""
import json
import unittest
import test_web as base
from race_supply.agent import AgentError,BatchInput,RuleInput,TOOLS,run_agent


class AgentTests(unittest.TestCase):
    setUpClass=classmethod(base.WebTests.setUpClass.__func__)
    tearDownClass=classmethod(base.WebTests.tearDownClass.__func__)
    tearDown=base.WebTests.tearDown
    client=base.WebTests.client

    def setUp(self):
        base.WebTests.setUp(self)
        self.service=self.app.state.agent_service
        self.user=self.alice.get('/api/me').json();self.admin_user=self.admin.get('/api/me').json()
        self.store=self.app.state.store
        self.doc=self.store.add_document(self.admin_user['id'],'换算演练','换算','shared','scenario','饮用水每箱24份；签收核对批次与数量。')
        self.store.change_status(self.doc,'published')
        self.service.searcher=lambda q,u:{'sources':[{'document_id':self.doc,'id':'S1','title':'演练','filename':'规则.md','version':1,'lines':[1,1],'excerpt':'饮用水每箱24份'}]}
        self.batch=BatchInput(batch_id='B001',event_id='EVT1',station_id='S01',employee_ids=[self.user['id']],records=[
            {'record_id':'D1','kind':'demand','material_id':'water','quantity':'24','unit':'份'},
            {'record_id':'O1','kind':'outbound','material_id':'water','quantity':'1','unit':'箱'},
            {'record_id':'R1','kind':'receipt','material_id':'water','quantity':'20','unit':'份'}])
        self.rules=RuleInput(document_id=self.doc,event_id='EVT1',confirmed=True,conversions=[{'material_id':'water','base_unit':'份','factors':{'份':'1','箱':'24'},'source_quote':'饮用水每箱24份'}])
        self.bs=self.service.import_batch(self.batch,self.admin_user)['record_snapshot_id']
        self.rs=self.service.import_rules(self.rules,self.admin_user)['rule_snapshot_id']
        self.cid=self.store.conversation(self.user['id'])
        self.rid=self.service.new_run(self.user,self.cid,'核对B001并生成复核草稿',True,1)

    def call(self,name,args,rid=None,user=None):return self.service.dispatch(name,args,rid or self.rid,user or self.user)
    def reads(self):
        self.call('search_knowledge',{'query':'换算规则'})
        self.call('get_batch_records',{'batch_id':'B001'})
    def validate(self):
        self.reads()
        return self.call('validate_batch',{'record_snapshot_id':self.bs,'rule_snapshot_id':self.rs})
    def draft(self):
        result=self.validate()
        return self.call('create_review_draft',{'validation_id':result['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid})

    def test_tools_have_strict_object_schemas(self):
        self.assertEqual(6,len(TOOLS))
        self.assertTrue(all(t['function']['parameters']['additionalProperties'] is False for t in TOOLS))

    def test_cannot_skip_reads_before_validation(self):
        result=self.call('validate_batch',{'record_snapshot_id':self.bs,'rule_snapshot_id':self.rs})
        self.assertEqual('ORDER_VIOLATION',result['error']['code'])

    def test_cannot_forge_validation_or_extra_fields(self):
        result=self.call('create_review_draft',{'validation_id':'fake','draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid,'validated':True})
        self.assertEqual('INVALID_ARGUMENTS',result['error']['code'])
        result=self.call('create_review_draft',{'validation_id':'fake','draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid})
        self.assertEqual('VALIDATION_EXPIRED',result['error']['code'])

    def test_decimal_conversion_and_difference(self):
        result=self.validate()
        self.assertTrue(result['checks_passed'])
        self.assertEqual('24',result['totals']['water']['outbound'])
        self.assertEqual('discrepancy',result['business_result'])
        self.assertIn('create_review_draft',result['allowed_actions'])

    def test_missing_conversion_blocks_draft(self):
        self.batch.records[1].unit='托盘'
        self.bs=self.service.import_batch(self.batch,self.admin_user)['record_snapshot_id']
        result=self.validate()
        self.assertFalse(result['checks_passed'])
        result=self.call('create_review_draft',{'validation_id':result['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid})
        self.assertEqual('VALIDATION_NOT_PASSED',result['error']['code'])

    def test_user_must_authorize_draft(self):
        with self.store.connection() as db:db.execute('UPDATE agent_runs SET allow_draft=0 WHERE id=?',(self.rid,))
        result=self.draft()
        self.assertEqual('DRAFT_NOT_AUTHORIZED',result['error']['code'])

    def test_cannot_use_other_users_snapshot(self):
        bob=self.bob.get('/api/me').json();cid=self.store.conversation(bob['id'])
        rid=self.service.new_run(bob,cid,'查询',False,1)
        result=self.call('get_batch_records',{'batch_id':'B001'},rid,bob)
        self.assertIn('error',result)

    def test_idempotent_creation(self):
        v=self.validate();args={'validation_id':v['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid}
        a=self.call('create_review_draft',args);b=self.call('create_review_draft',args)
        self.assertEqual(a['draft_id'],b['draft_id']);self.assertTrue(b['reused'])

    def test_new_batch_invalidates_old_validation(self):
        v=self.validate();self.service.import_batch(self.batch,self.admin_user)
        r=self.call('create_review_draft',{'validation_id':v['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid})
        self.assertEqual('STALE_RECORDS',r['error']['code'])

    def test_rule_withdrawal_blocks_submission(self):
        did=self.draft()['draft_id'];self.store.change_status(self.doc,'archived')
        with self.assertRaises(AgentError):self.service.issue_confirmation(did,self.user)

    def test_model_cannot_submit_even_with_real_token(self):
        did=self.draft()['draft_id'];proof=self.service.issue_confirmation(did,self.user)
        result=self.call('submit_for_review',{'draft_id':did,'confirmation_token':proof['confirmation_token']})
        self.assertEqual('UI_CONFIRMATION_REQUIRED',result['error']['code'])

    def test_confirmation_one_use_and_other_user_denied(self):
        did=self.draft()['draft_id'];token=self.service.issue_confirmation(did,self.user)['confirmation_token']
        with self.assertRaises(AgentError):self.service.submit(did,token,self.bob.get('/api/me').json())
        self.assertEqual('pending_review',self.service.submit(did,token,self.user)['state'])
        with self.assertRaises(AgentError):self.service.submit(did,token,self.user)

    def test_expired_validation_blocks_draft(self):
        v=self.validate()
        with self.store.connection() as db:db.execute('UPDATE agent_validations SET expires=0 WHERE id=?',(v['validation_id'],))
        r=self.call('create_review_draft',{'validation_id':v['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid})
        self.assertEqual('VALIDATION_EXPIRED',r['error']['code'])

    def test_multiple_stations_require_disambiguation(self):
        self.batch.station_id='S02';self.service.import_batch(self.batch,self.admin_user)
        r=self.call('get_batch_records',{'batch_id':'B001'})
        self.assertEqual('BATCH_AMBIGUOUS',r['error']['code'])
        r=self.call('get_batch_records',{'batch_id':'B001','station_id':'S01'})
        self.assertEqual(self.bs,r['record_snapshot_id'])

    def test_wrong_event_rules_blocked(self):
        self.rules.event_id='OTHER';self.rs=self.service.import_rules(self.rules,self.admin_user)['rule_snapshot_id']
        r=self.validate();self.assertEqual('RULE_SCOPE_MISMATCH',r['error']['code'])

    def test_agent_http_end_to_end_with_scripted_model(self):
        steps=[0]
        def step(messages,tools):
            index=steps[0];steps[0]+=1
            latest=[json.loads(m['content']) for m in messages if m['role']=='tool']
            if index==0:name,args='search_knowledge',{'query':'换算规则'}
            elif index==1:name,args='get_batch_records',{'batch_id':'B001'}
            elif index==2:name,args='validate_batch',{'record_snapshot_id':self.bs,'rule_snapshot_id':self.rs}
            elif index==3:name,args='create_review_draft',{'validation_id':latest[-1]['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':json.loads(messages[1]['content'])['idempotency_key']}
            else:return {'content':'模型无法伪造已提交状态'}
            return {'tool_calls':[{'id':str(index),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        self.app.state.agent_step=step
        response=self.alice.post('/agent/chat',json={'question':'核对B001并生成复核草稿','allow_draft':True})
        self.assertEqual(200,response.status_code,response.text)
        did=response.json()['draft_ids'][0]
        self.assertEqual(422,self.alice.post(f'/agent/drafts/{did}/confirmation',json={'confirmed':False}).status_code)
        token=self.alice.post(f'/agent/drafts/{did}/confirmation',json={'confirmed':True}).json()['confirmation_token']
        self.assertEqual('pending_review',self.alice.post(f'/agent/drafts/{did}/submit',json={'confirmation_token':token}).json()['state'])

    def test_admin_import_permissions_and_page(self):
        self.assertEqual(403,self.alice.post('/admin/agent/batches',json=self.batch.model_dump()).status_code)
        self.assertEqual(200,self.admin.get('/admin/agent').status_code)

    def test_new_specific_rule_invalidates_generic_validation(self):
        v=self.validate()
        self.rules.station_id='S01';self.service.import_rules(self.rules,self.admin_user)
        r=self.call('create_review_draft',{'validation_id':v['validation_id'],'draft_type':'receipt_discrepancy','idempotency_key':'run:'+self.rid})
        self.assertEqual('STALE_RULES',r['error']['code'])

    def test_loop_limit_and_hidden_submit_tool(self):
        def step(messages,tools):
            self.assertNotIn('submit_for_review',[t['function']['name'] for t in tools])
            self.assertNotIn('create_review_draft',[t['function']['name'] for t in tools])
            return {'tool_calls':[{'id':'repeat','type':'function','function':{'name':'get_batch_records','arguments':'{"batch_id":"B001"}'}}]}
        result=run_agent(self.service,self.user,self.cid,'查询批次',False,{'id':1,'content':'只读'},step=step)
        self.assertEqual('limited',result['status'])
        self.assertEqual(6,len(result['trace']))

    def test_function_call_transport(self):
        from unittest.mock import patch
        from race_supply.agent import model_step
        with patch.dict('os.environ',{'LLM_MODEL':'test-only','LLM_BASE_URL':'https://api.deepseek.com'}):
            with patch('race_supply.agent.rag.post_api',return_value={'choices':[{'message':{'tool_calls':[]},'finish_reason':'stop'}]}) as request:
                model_step([{'role':'user','content':'test'}],TOOLS)
                body=request.call_args.args[2]
                self.assertEqual('auto',body['tool_choice'])
                self.assertEqual({'type':'disabled'},body['thinking'])

    def test_knowledge_answer_requires_real_quote(self):
        def step(messages,tools):
            if not any(m['role']=='tool' for m in messages):
                return {'tool_calls':[{'id':'s','type':'function','function':{'name':'search_knowledge','arguments':'{"query":"换算"}'}}]}
            return {'content':json.dumps({'status':'answered','claims':[{'text':'演练中饮用水每箱24份','citations':['S1'],'quotes':[{'id':'S1','text':'饮用水每箱24份'}]}],'missing':[]})}
        result=run_agent(self.service,self.user,self.cid,'演练换算是多少',False,{'id':1,'content':'只读'},step=step)
        self.assertIn('[S1]',result['answer'])
        self.assertEqual([],result['draft_ids'])

    def test_partial_delivery_is_not_silently_matched(self):
        self.batch.records[0].quantity='30';self.batch.records[2].quantity='24'
        self.bs=self.service.import_batch(self.batch,self.admin_user)['record_snapshot_id']
        result=self.validate()
        self.assertEqual('discrepancy',result['business_result'])
        self.assertEqual([],result['allowed_draft_types'])


if __name__=='__main__':unittest.main()
