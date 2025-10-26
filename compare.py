import os
import sys
import json
import signal
import shutil
import pathlib
import argparse
import tempfile
import subprocess

from enum import Enum

import dataclasses
from dataclasses import dataclass

import colors as c

from ctrf_results import CTRFTest, CTRFResults

from pa_results import PaGradeEntry, PaRunner, PaConfig, PATestEntry, \
    PAResults, STATUS_PASS, STATUS_FAIL

class CompareTestStatus(Enum):
    RESULT_MISMATCH = "result"
    OUTPUT_MISMATCH = "output"
    MISSING = "missing"
    EXTRA = "extra"
    OK = "ok"


@dataclass(init=False)
class TestCompareEntry(CTRFTest):
    name: str
    status: str
    reason: CompareTestStatus
    output: str
    t_actual: PATestEntry
    t_expected: PATestEntry

    def __init__(self, name, status, reason, output,
                 t_actual, t_expected, **kwargs):
        super(TestCompareEntry, self).__init__(name, status, **kwargs)
        self.reason = reason
        self.output = output
        self.t_actual = t_actual
        self.t_expected = t_expected

    def is_passing(self):
        return self.status == STATUS_PASS

    def fmt_result(self):
        return c.color("PASS", c.OKGREEN) if self.is_passing() else c.color("FAIL ({})".format(str(self.reason)), c.FAIL)

    def build_ctrf_output(self, d):
        d["stdout"] = self.output.split("\n")
        d["message"] = self.reason.value

        self.add_extra_item("result_actual", self.t_actual.to_ctrf() if self.t_actual is not None else None)
        self.add_extra_item("result_expected", self.t_expected.to_ctrf() if self.t_expected is not None else None)

    @classmethod
    def add_from_ctrf(cls, d, kw):
        _output = cls._get(d, "stdout")
        kw["output"] = "\n".join(_output)

        _reason = cls._get(d, "message")
        kw["reason"] = CompareTestStatus(_reason)

        cls._add_from_extra(kw, d, "result_actual", "t_actual")
        cls._add_from_extra(kw, d, "result_expected", "t_expected")

    def to_json(self):
        d = {
            "name": self.name,
            "status": self.status,
            "reason": str(self.reason),
            "output": self.output,
            "result_actual": self.t_actual.to_json(),
            "result_expected": self.t_expected.to_json(),
        }
        return d

    @classmethod
    def _make_output(cls, actual_str, expected_str):
        if (expected_str == "") and (actual_str == ""):
            ret = ""
        elif actual_str == expected_str:
            ret = "Outputs from test are identical\n```{}\n```".format(expected_str)
        else:
            ret = "Expected:\n```{}\n```\n\nGot:\n```{}\n```".format(expected_str, actual_str)

        return ret

    @classmethod
    def from_tests(cls, t_actual: PATestEntry | None, t_expected: PATestEntry | None, suite: str|None=None):
        reason = CompareTestStatus.OK
        output = ""

        def _out(prefix, output):
            return "{}\n```\n{}\n```".format(prefix, output)

        assert((t_actual is not None)  or (t_expected is not None))
        test_name: str = None

        if t_actual is None:
            assert(t_expected is not None)
            test_name = t_expected.name
            reason = CompareTestStatus.MISSING
            output = _out("Expected test not found in expected results.  Expected output:", t_expected.output)
        elif t_expected is None:
            assert(t_actual is not None)
            test_name = t_actual.name
            reason = CompareTestStatus.EXTRA
            output = _out("Extra test found not in expected results.  Output:", t_actual.output)
        else:
            assert(t_actual is not None)
            assert(t_expected is not None)
            test_name = t_actual.name

            if t_actual.status != t_expected.status:
                reason = CompareTestStatus.RESULT_MISMATCH
                _comp = cls._make_output(t_actual.output, t_expected.output)
                output = "Expected test status '{}' but was '{}`\n{}".format(t_expected.status, t_actual.status, _comp)
            # elif t_actual.output != t_expected.output:
            #     reason = CompareTestStatus.OUTPUT_MISMATCH
            #     _comp = cls._make_output(t_actual.output, t_expected.output)
            #     output = "Test outputs differ significantly\n{}".format(t_expected.status, t_actual.status, _comp)
            else:
                reason = CompareTestStatus.OK
                output = t_actual.output

        status = STATUS_PASS if (reason == CompareTestStatus.OK) else STATUS_FAIL
        return cls(name=test_name,
                   status=status,
                   reason=reason,
                   output=output,
                   suite=suite,
                   t_actual=t_actual, t_expected=t_expected)

class CompareResult(CTRFResults):

    def __init__(self,
                 r_actual: PAResults|None=None,
                 r_expected: PAResults|None=None,
                 tests: list[TestCompareEntry]|None=None,
                 suite: str|None=None,
                 **kwargs):
        super(CompareResult, self).__init__(**kwargs)
        self._actual = r_actual
        self._expected = r_expected
        self.tests: list[TestCompareEntry] = [] if tests is None else tests
        self.suite = suite

        if len(self.tests) == 0:
            assert(self._actual is not None)
            assert(self._expected is not None)
            is_passing = self._build_results()
        else:
            is_passing = [t.is_passing() for t in self.tests]

        self.status = STATUS_PASS if is_passing else STATUS_FAIL

    def get_tests(self):
        return self.tests

    def is_passing(self):
        return self.status == STATUS_PASS

    def build_ctrf_output(self, d):
        pass

    @classmethod
    def add_from_ctrf(cls, d, kw):
        _tests = cls._get(d, "tests")
        tests = [TestCompareEntry.from_ctrf(t) for t in _tests]
        kw["tests"] = tests

    # def to_json(self):
    #     _tests = [t.to_json() for t in self.tests]
    #     d = {
    #         "tests": _tests,
    #     }
    #     return d

    def write_json(self, out_file):
        with open(str(out_file), "w") as fd:
            json.dump(self.to_ctrf(), fd,
                      indent=2)

    def _build_results(self):
        assert(self._actual is not None)
        assert(self._expected is not None)

        ok = True

        expected_test_names = self._expected.get_test_names()
        actual_test_names = self._actual.get_test_names()
        test_names = expected_test_names.copy()
        for t in actual_test_names:
            if t not in expected_test_names:
                test_names.append(t)

        for t in test_names:
            t_actual = self._actual.get_test(t, missing_ok=True)
            t_expected = self._expected.get_test(t, missing_ok=True)

            entry = TestCompareEntry.from_tests(t_actual=t_actual,
                                                t_expected=t_expected,
                                                suite=self.suite)

            self.tests.append(entry)

            ok = ok and entry.is_passing()

        return ok

    def print_summary(self, summary_only=False, print_passing=False, descr_on_fail=True, descr_on_pass=True):
        total = 0
        passed = 0
        failed = 0
        for test in self.tests:
            if not summary_only:
                print_test = print_passing or (not test.is_passing())
                if print_test:
                    #import pdb; pdb.set_trace()
                    print("{:10}  {}".format(test.name, test.fmt_result()))
                    show_output = (test.is_passing() and descr_on_pass) or ((not test.is_passing()) and descr_on_fail)
                    if show_output:
                        _output = test.output
                        output = _output.replace("\n", "\n\t")
                        print(f"\t{output}")

            if test.is_passing():
                passed += 1
            else:
                failed += 1

            total += 1

        if len(self.tests) > 0:
            failed_str = "({} failed)".format(failed) if failed > 0 else ""
            print("=== Check expected results ===\n  Matched: {} / {} tests {} {:>30}".format(passed, total, failed_str, c.fmt_status_bool(self.is_passing())))
        else:
            print("No tests found")

        ok = (failed == 0)
        return ok


    @classmethod
    def from_files(cls, actual_json, expected_json, suite=None):
        r_actual = PAResults.from_runner_json_file(actual_json)
        r_expected = PAResults.from_runner_json_file(expected_json)

        return cls(r_actual, r_expected, suite=suite)
