import sys
import os
import pytest

class ExactReporter:
    def __init__(self):
        self.modules = {}
        self.totals = {'passed': 0, 'failed': 0, 'skipped': 0, 'xfail': 0, 'error': 0}

    def pytest_collection_modifyitems(self, session, config, items):
        for item in items:
            mod = item.nodeid.split('::')[0].replace('\\', '/')
            if mod not in self.modules:
                self.modules[mod] = {'collected': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'xfail': 0, 'error': 0}
            self.modules[mod]['collected'] += 1

    def pytest_runtest_logreport(self, report):
        mod = report.nodeid.split('::')[0].replace('\\', '/')
        if mod not in self.modules:
            self.modules[mod] = {'collected': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'xfail': 0, 'error': 0}
        
        if report.when == 'call':
            if report.passed:
                self.modules[mod]['passed'] += 1
                self.totals['passed'] += 1
            elif report.failed:
                self.modules[mod]['failed'] += 1
                self.totals['failed'] += 1
            elif report.skipped:
                self.modules[mod]['skipped'] += 1
                self.totals['skipped'] += 1
        elif report.when in ('setup', 'teardown') and report.failed:
            self.modules[mod]['error'] += 1
            self.totals['error'] += 1
        elif report.when == 'setup' and report.skipped:
            self.modules[mod]['skipped'] += 1
            self.totals['skipped'] += 1

reporter = ExactReporter()
evidence_dir = os.path.join(os.path.dirname(__file__))
exit_code = pytest.main(['tests', '-q'], plugins=[reporter])

out_path = os.path.join(evidence_dir, 'pytest_baseline_output.txt')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"PYTEST INVOCATION: .venv/Scripts/python.exe -m pytest tests -q\n")
    f.write(f"WORKING DIRECTORY: {os.getcwd()}\n")
    f.write(f"PYTEST EXIT CODE: {int(exit_code)}\n")
    f.write(f"PYTHON VERSION: {sys.version}\n")
    f.write(f"PYTEST VERSION: {pytest.__version__}\n")
    f.write(f"TOTAL STATS: {reporter.totals}\n")
    total_coll = sum(m['collected'] for m in reporter.modules.values())
    f.write(f"TOTAL COLLECTED: {total_coll}\n\n")
    f.write(f"{'Module':<55} | {'Collected':<9} | {'Passed':<6} | {'Failed':<6} | {'Skipped':<7} | {'Error':<5}\n")
    f.write('-'*100 + '\n')
    for mod in sorted(reporter.modules.keys()):
        m = reporter.modules[mod]
        f.write(f"{mod:<55} | {m['collected']:>9} | {m['passed']:>6} | {m['failed']:>6} | {m['skipped']:>7} | {m['error']:>5}\n")

print(f"Done. Collected: {total_coll}, Passed: {reporter.totals['passed']}, Failed: {reporter.totals['failed']}")
