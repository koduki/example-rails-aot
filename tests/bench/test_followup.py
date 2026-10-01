"""Verify follow-up isolation, real invocation settings and incomplete evidence."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/bench'))
import observe
import retest_plan
import run


def measurement(p99=20):
    return {'driver':'k6-open-arrival','elapsed':120,'rps':10,'p95_ms':p99,
        'rate_offered':10,'rps_successful':10,'requests_total':1200,'requests_successful':1200,
        'requests_failed':0,'latency_ms':{'p99':p99},'iterations_dropped':0,
        'client_saturated':False,'tester_cpu_pct':2,'tester_network_errors':0}


class FollowupTests(unittest.TestCase):
    def test_pool_bounds_and_instrumentation_are_validated(self):
        p=retest_plan.plan('matched-rate')[0]['profile']
        for change in ({'preallocated_vus':True},{'max_vus':1}, {'socket_interval_seconds':float('nan')},
                       {'jfr':True},{'socket_observations':'yes'},{'recovery_health_p99_ms':float('nan')}):
            with self.subTest(change=change),self.assertRaises(ValueError):run.config(dict(p,**change))

    def test_experiments_have_complete_counts_and_separate_costs(self):
        counts={}
        for experiment in retest_plan.EXPERIMENTS:
            cells=retest_plan.plan(experiment)
            counts[experiment]=sum(len(c['profile']['targets'])*c['profile']['repetitions'] for c in cells)
            for cell in cells:
                p=cell['profile'];self.assertEqual(p['warmup_connections'],4)
                self.assertEqual(p['measurement_seconds'],120);self.assertFalse(p['allow_unstable'])
                self.assertNotIn('emit-jruby-off',p['targets'])
                self.assertEqual(p['diagnostics'],experiment.startswith('trace-'))
        self.assertEqual(counts,{'matched-rate':18,'capacity-cruby':20,'capacity-jruby':10,
                                'spinel-connections':18,'trace-cruby':12,'trace-jruby':6})
        cells=retest_plan.plan('spinel-connections')
        self.assertEqual({c['profile']['max_vus'] for c in cells},{4096})
        self.assertEqual({c['profile']['preallocated_vus'] for c in cells},{10,512})
        self.assertTrue(all(c['profile']['socket_observations'] for c in cells))
        self.assertTrue(retest_plan.plan('trace-jruby')[0]['profile']['jfr'])

    def test_plan_only_and_execution_gate(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(retest_plan,'execute') as execute:
            out=Path(tmp)/'plan'
            self.assertEqual(retest_plan.main(['--experiment','spinel-connections','--output',str(out)]),0)
            self.assertFalse(execute.called)
            saved=json.loads((out/'retest-plan.json').read_text());self.assertFalse(saved['executed'])
            self.assertEqual(len(saved['commands']),6)
            with self.assertRaises(SystemExit):
                retest_plan.main(['--experiment','matched-rate','--output',str(Path(tmp)/'bad'),'--execute'])
            self.assertFalse((Path(tmp)/'bad').exists())

    def test_budget_keeps_unstarted_cells(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(retest_plan,'execute',return_value=0), \
                patch.object(retest_plan.time,'monotonic',side_effect=[0,1,14401]):
            out=Path(tmp)/'plan'
            code=retest_plan.main(['--experiment','spinel-connections','--output',str(out),'--execute',
                '--preflight-file','checks.json','--remote-loadgen','tester','--target-host','10.0.0.1',
                '--gce-project','project','--gce-zone','zone'])
            results=json.loads((out/'retest-results.json').read_text())
            self.assertEqual(code,1);self.assertEqual(results[0]['status'],'completed')
            self.assertEqual([x['status'] for x in results[1:]],['not_run']*5)

    def test_child_gets_graceful_stop_before_kill(self):
        child=MagicMock();child.wait.side_effect=[subprocess.TimeoutExpired('bench',1),0]
        with patch.object(retest_plan.subprocess,'Popen',return_value=child),self.assertRaises(subprocess.TimeoutExpired):
            retest_plan.execute(['bench'],1)
        child.terminate.assert_called_once();child.kill.assert_not_called()

    def test_interruption_retains_all_planned_trials(self):
        p=retest_plan.plan('matched-rate')[0]['profile']
        manager=MagicMock();manager.__enter__.side_effect=KeyboardInterrupt
        checks={t:{'eligible_endpoints':['/articles?page=1']} for t in p['targets']}
        with tempfile.TemporaryDirectory() as tmp,patch.object(run,'Server',return_value=manager):
            with self.assertRaises(KeyboardInterrupt):run.trials(p,{},Path(tmp),checks)
            rows=json.loads((Path(tmp)/'per-run.json').read_text())
            self.assertEqual(len(rows),18)
            self.assertEqual(rows[0]['status'],'interrupted')
            self.assertTrue(all(row['status']=='not_run' for row in rows[1:]))

    def test_health_is_separate_from_performance_slo(self):
        p=retest_plan.plan('capacity-jruby')[0]['profile']
        with tempfile.TemporaryDirectory() as tmp,patch.object(run,'sample',return_value=measurement(600)):
            run.recover(MagicMock(),'/articles?page=1',p,{},Path(tmp))
            d=json.loads((Path(tmp)/'recovery.json').read_text())
            self.assertEqual(d['status'],'healthy_probe');self.assertIsNone(d['server_queue_drained'])
            self.assertEqual(d['attempts'][0]['performance_slo_decision'],'slo_fail')

    def test_phase_error_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(run,'_sample',side_effect=ValueError('bad summary')):
            with self.assertRaises(ValueError):run.sample(MagicMock(),'/articles',120,{}, {},tmp,phase='measurement')
            d=json.loads((Path(tmp)/'phase.json').read_text())
            self.assertEqual(d['status'],'failed');self.assertIn('bad summary',d['error'])
            self.assertGreaterEqual(d['wall_seconds'],0)

    def test_phase_is_retained_when_socket_finalization_fails(self):
        collector=MagicMock();collector.stop.side_effect=OSError('observer write error')
        with tempfile.TemporaryDirectory() as tmp,patch('observe.SocketCollector',return_value=collector), \
                patch.object(run,'_sample',return_value=measurement()):
            with self.assertRaises(OSError):
                run.sample(MagicMock(),'/articles',120,{'socket_observations':True},{},tmp,phase='measurement')
            d=json.loads((Path(tmp)/'phase.json').read_text())
            self.assertEqual(d['measurement_elapsed_seconds'],120)
            self.assertEqual(d['status'],'artifact_error')
            self.assertIn('observer write error',d['error'])
            self.assertGreaterEqual(d['wall_seconds'],0)

    def test_actual_remote_invocation_retains_pool_and_mode(self):
        for capacity_mode,pool in [(False,10),(True,512)]:
            with self.subTest(capacity_mode=capacity_mode),tempfile.TemporaryDirectory() as tmp:
                p=run.config(ROOT/'bench/profiles/gce-c3-capacity.yml')
                p.update(capacity_search=capacity_mode,remote_loadgen='tester',gce_zone='zone',gce_project='project')
                def fake_run(script,env,output,timeout):run.save(output/'k6-summary.json',measurement())
                loadgen=MagicMock();loadgen.run.side_effect=fake_run
                server=MagicMock();server.url='http://10.0.0.1:3000'
                with patch.object(run,'RemoteLoadGenerator',return_value=loadgen):
                    run.sample(server,'/articles?page=1',120,p,{'client':[0,1,2,3]},tmp,phase='measurement')
                d=json.loads((Path(tmp)/'k6-invocation.json').read_text())
                self.assertEqual(d['environment']['PRE_ALLOCATED_VUS'],str(pool))
                self.assertEqual(d['load_mode'],'open')

    def test_fixed_rate_p99_failure_is_not_passed(self):
        for p99,cpu_stat,expected in [(150,'nr_throttled 0','failed'),(20,'nr_throttled 0','passed'),
                                      (20,None,'failed')]:
            p=dict(retest_plan.plan('matched-rate')[0]['profile'],targets=['rails-cruby-off'],repetitions=1)
            server=MagicMock();server.record_cpu.return_value={'cpu.stat':cpu_stat}
            manager=MagicMock();manager.__enter__.return_value=server
            collector=MagicMock();collector.stop.return_value={'summary':{'sample_count':1,'oom_killed':False}}
            warm=dict(measurement(),elapsed=180)
            with tempfile.TemporaryDirectory() as tmp,patch.object(run,'Server',return_value=manager), \
                    patch.object(run,'sample',side_effect=[warm,measurement(p99)]), \
                    patch.object(run,'stable',return_value=True), \
                    patch('collect.ResourceCollector',return_value=collector):
                rows=run.trials(p,{},Path(tmp),{'rails-cruby-off':{'eligible_endpoints':['/articles?page=1']}})
                self.assertEqual(rows[0]['status'],expected)
                self.assertEqual(rows[0]['telemetry']['summary']['throttled_periods_delta'],0 if cpu_stat else None)

    def test_tcp_ipv4_ipv6_and_listen_are_not_http_queues(self):
        raw=''' sl local_address rem_address st tx_queue:rx_queue
0: 00000000:0BB8 00000000:0000 0A 00000000:00000003
1: 0100007F:0BB8 0200007F:1234 01 0000000A:00000014
2: 00000000000000000000000000000000:0BB8 00000000000000000000000000000000:1234 08 00000001:00000002
3: 0100007F:0016 0200007F:1234 01 000000AA:000000AA
bad line
'''
        d=observe.parse_tcp(raw)
        self.assertEqual(d['state_counts'],{'LISTEN':1,'ESTABLISHED':1,'CLOSE_WAIT':1})
        self.assertEqual(d['non_listen_rx_queue_bytes'],22)
        self.assertEqual(d['listen_queue_raw'][0]['rx_queue_raw'],3)
        self.assertEqual(d['malformed_lines'],1)
        collector=observe.SocketCollector('container');collector.event=MagicMock()
        collector.event.is_set.side_effect=[False,True]
        result=MagicMock();result.stdout=raw
        with patch.object(observe.subprocess,'run',return_value=result):collector._run()
        self.assertEqual(collector.stop()['sample_count'],1)
        self.assertIsNone(collector.stop()['server_queue_drained'])

    def test_jfr_dump_records_actual_file_and_removes_container(self):
        p=retest_plan.plan('trace-jruby')[0]['profile']
        with tempfile.TemporaryDirectory() as tmp:
            server=run.Server('rails-jruby',tmp,p,{'app':[0,1,2,3]})
            server.database.parent.mkdir();(server.database.parent/'bench.jfr').write_bytes(b'FLR-test')
            with patch.object(server,'record_cpu'),patch.object(run,'command',return_value='JFR dumped') as command:
                server.__exit__()
            d=json.loads((Path(tmp)/'jfr-capture.json').read_text())
            self.assertEqual(d['status'],'captured');self.assertEqual(d['size_bytes'],8)
            self.assertTrue(any(c.args[0][:3]==['docker','rm','-f'] for c in command.call_args_list))
