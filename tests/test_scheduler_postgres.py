from contextlib import nullcontext, redirect_stderr
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import TestCase, mock

import wallet_quota_scheduler
from walletquota.scheduler import IST, SchedulerLock


class SchedulerPostgresTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = self.root / 'config.env'
        self.env.write_text('ENABLED_PROVIDERS=atgpay\nREPORT_DIRECTORY=' + str(self.root / 'reports') + '\n')

    def run_scheduler(self, extra):
        pipeline = mock.Mock(side_effect=(0, 3))
        query = mock.Mock(side_effect=(0, 3))
        captured = {}

        class FakeScheduler:
            def __init__(self, report_directory, callback, clock, **unused):
                captured['root'] = report_directory
                self.callback = callback

            def run(self):
                self.callback()
                self.callback()

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, {'wallet_quota_pipeline': SimpleNamespace(main=pipeline)}), mock.patch.object(
            wallet_quota_scheduler, 'StandaloneScheduler', FakeScheduler
        ), mock.patch.object(wallet_quota_scheduler.SchedulerLock, 'acquire', return_value=nullcontext()), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, 'main', query
        ), mock.patch.object(wallet_quota_scheduler.signal, 'signal'), redirect_stderr(stderr):
            try:
                code = wallet_quota_scheduler.main(['--env-file', str(self.env), *extra], stdout, stderr)
            except SystemExit as error:
                code = error.code
        return code, pipeline, query, captured, stdout, stderr

    def test_postgres_mode_dispatches_every_cycle_and_forwards_hoyo(self):
        code, pipeline, query, captured, stdout, stderr = self.run_scheduler(['--postgres', '--include-hoyo', '--verbose'])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(pipeline.call_count, 2)
        query.assert_not_called()
        self.assertEqual(captured['root'], (self.root / 'reports').resolve())
        for call in pipeline.call_args_list:
            self.assertEqual(call.args[0], ['--env-file', str(self.env), '--output-dir', str((self.root / 'reports').resolve()), '--verbose', '--include-hoyo'])
            self.assertIs(call.args[1], stdout)
            self.assertIs(call.args[2], stderr)

    def test_default_mode_keeps_existing_read_only_query(self):
        code, pipeline, query, unused, stdout, stderr = self.run_scheduler([])

        self.assertEqual(code, 0, stderr.getvalue())
        pipeline.assert_not_called()
        self.assertEqual(query.call_count, 2)

    def test_hoyo_switch_requires_postgres_mode_before_starting(self):
        code, pipeline, query, unused, stdout, stderr = self.run_scheduler(['--include-hoyo'])

        self.assertEqual(code, 2)
        pipeline.assert_not_called()
        query.assert_not_called()

    def test_bounded_full_schedule_retries_failed_database_and_preserves_reports(self):
        import wallet_quota_pipeline as pipeline
        from tests.test_continuous_pipeline import StaticProvider, hoyo_result

        self.env.write_text(
            'ENABLED_PROVIDERS=atgpay,millerpay,penguinpay\n'
            'HOYOPAY_PHONE=fictional\nHOYOPAY_PASSWORD=FIXTURE_ONLY\n'
            'PGDATABASE=fixture_db\nREPORT_DIRECTORY=' + str(self.root / 'reports') + '\n'
        )
        factories = {name: (lambda values, overrides, selected=name: StaticProvider(selected))
                     for name in ('atgpay', 'millerpay', 'penguinpay')}
        times = iter((datetime(2026, 10, 21, 12, 1, tzinfo=IST),
                      datetime(2026, 10, 21, 12, 21, tzinfo=IST)))
        waits = iter((True, False))
        original_scheduler = wallet_quota_scheduler.StandaloneScheduler
        schedulers = []

        def bounded_scheduler(*args, **kwargs):
            kwargs['wait'] = lambda target: next(waits)
            scheduler = original_scheduler(*args, **kwargs)
            schedulers.append(scheduler)
            return scheduler

        stdout, stderr = io.StringIO(), io.StringIO()
        results = [subprocess.CompletedProcess([], code, b'', b'') for code in (1, 0, 0)]
        with mock.patch.object(pipeline, 'PROVIDER_FACTORIES', factories), mock.patch.object(
            pipeline.hoyo, 'collect', return_value=hoyo_result()
        ), mock.patch.object(pipeline.hoyo, 'SignedPoolClient'), mock.patch.object(
            pipeline.subprocess, 'run', side_effect=results
        ) as psql, mock.patch.object(wallet_quota_scheduler, 'StandaloneScheduler', bounded_scheduler), mock.patch.object(
            wallet_quota_scheduler, 'datetime', SimpleNamespace(now=lambda zone: next(times))
        ), mock.patch.object(wallet_quota_scheduler.signal, 'signal'):
            code = wallet_quota_scheduler.main(
                ['--env-file', str(self.env), '--postgres', '--include-hoyo'], stdout, stderr
            )

        self.assertEqual(code, 0)
        self.assertEqual(schedulers[0].query_exit_codes, (3, 0))
        self.assertEqual(psql.call_count, 3)
        reports = self.root / 'reports'
        bundles = sorted(reports.glob('*/aggregate_*.json'))
        self.assertEqual(len(bundles), 2)
        for source in bundles:
            self.assertEqual(json.loads(source.read_text())['total']['order_count'], 4)
            self.assertTrue(source.with_name('postgres_{}.data.sql'.format(source.parent.name)).is_file())
            self.assertTrue(source.with_name('postgres_{}.imported'.format(source.parent.name)).is_file())
            self.assertFalse(source.with_name('postgres_{}.pending'.format(source.parent.name)).exists())
        self.assertNotIn('FIXTURE_ONLY', stdout.getvalue() + stderr.getvalue())
        with SchedulerLock.acquire(reports):
            pass
