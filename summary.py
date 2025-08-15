import os
import sys
import json
import html
import pathlib
import argparse
import subprocess

HAS_PYSTACHE = False
try:
    import pystache
    import ansi2html
    from ansi2html.style import get_styles
    HAS_PYSTACHE = True
except ImportError:
    print("Warning:  pystache not found, will not be able to generate summary info")

from dataclasses import dataclass

import colors as c
from colors import color, eprint

from collections import namedtuple, defaultdict

from stest import STest, SResults

STATUS_PASS = "passed"
STATUS_FAIL = "failed"

def _get(d, k, default=None):
    if k in d:
        return d[k]
    else:
        if default is not None:
            return default
        else:
            raise ValueError(f"{d} not in {d}")


TEMPLATE_HEAD = """
<DOCTYPE html>
<head>
<title>Results for run {{ run_id }}</title>
<style>
td {
    border-bottom: 1px solid #000000;
}

.fail {
    color: #ff0000;
}

.bar-outer {
    background-color: #eeeeee;
    border-radius: 1px;
    overflow: hidden;
    height: 1.2em;
    width: 100%;
}

.bar-inner {
    height: 100%;
    text-align: right;
    padding-right: 1px;
    color: red;
    font-weight: bold;
    line-height: 1.2em;
    white-space: no-wrap;
}

{{{ styles }}}
</style>
</head>
<body>
"""

TEMPLATE_END = """
</body>
</html>
"""

TEMPLATE_SUMMARY = """
<h1 id="summary">Test summary</h1>
<table>
<thead>
<tr>
<td>Test name</td><td>Passing</td><td>Failing</td><td>%</td>
</tr>
</thead>
<tbody>
{{ #tests }}
  <tr><td><a href="#test-{{name}}">{{ name }}</a></td><td>{{ count_passing }}</td><td>{{{ count_failing }}}</td><td>{{{ percent }}}</td></tr>
{{ /tests }}
</tbody>
</table>

<h1 id="per-test">Per-test summary</h1>
{{ #tests }}
<h2 id="test-{{name}}">{{name}}</h2>
Passing ({{{count_passing}}}):
<ul>
{{ #t_passing }}
<li><details><summary>{{ name }} {{score}}</summary><pre>{{{output}}}</pre></details></li>
{{ /t_passing }}
</ul>
<br />
Failing ({{{count_failing}}}):
<ul>
{{ #t_failing }}
<li><details><summary>{{ name }} {{score}}</summary><pre>{{{output}}}</pre></details></li>
{{ /t_failing }}
</ul>
{{ /tests }}
"""

TEMPLATE_STUDENT_HEAD = """
<h1 id="results">Test results</h1>
"""

TEMPLATE_STUDENT_SUMMARY = """
<h2 id="results-{{name}}">{{name}}</h2>
<table>
<tr><td>Total</td><td>{{total}}</td></tr>
<tr><td>Passed</td><td>{{passed}}</td></tr>
<tr><td>Failed</td><td class="fail">{{{failed}}}</td></tr>
<tr><td>Score</td><td>{{score}}/{{max_score}}</td></tr>
</table>
<br />
<br />
"""

TEMPLATE_STUDENT_TEST = """
<h3 id="results-bytest-{{name}}">Per-test results</h3>
<table>
<thead>
<tr>
<td>Test name</td><td>Score</td><td>Status</td>
</tr>
</thead>
<tbody>
{{ #tests }}
  <tr class="row-summary"><td>{{ name }}</td><td>{{ score }}</td><td>{{{ status }}}</td></tr>
  <tr class="row-output"><td colspan="3"><details><summary>Output</summary>
  <pre>
  {{{ output }}}
  </pre>
  </details>
  </td>
  </tr>
{{ /tests }}
</tbody>
</table>
<br />
<br />

"""



@dataclass
class SubmissionTest():
    name: str
    t: STest

    def is_passing(self):
        return self.t.is_passing()

@dataclass
class SubmissionResult():
    name: str
    results: SResults


class GSSummary():
    result_map: dict[str, SubmissionResult]
    test_map: dict[str, list[SubmissionTest]]
    all_tests: set[str]

    def __init__(self, run_id):
        self.run_id = run_id
        self.result_map = {}
        self.test_map = defaultdict(list)
        self.all_tests = set()

    def add(self, name, res: SResults):
        tests = res.get_tests()
        test_names = [t.name for t in tests]
        self.all_tests.update(test_names)

        sr = SubmissionResult(name=name, results=res)
        self.result_map[name] = sr

        for t in tests:
            t_name = t.name
            tr = SubmissionTest(name=name, t=t)
            self.test_map[t_name].append(tr)


    def get_test_results(self, test_name):
        if test_name not in self.all_tests:
            raise ValueError("Test not found:  {}".format(test_name))
        return self.test_map[test_name]

    def _add_header(self, fd):
        styles = get_styles()
        styles_as_css = "\n".join([f"{x.klass} {{ {x.kw}  }}" for x in styles])

        out = pystache.render(TEMPLATE_HEAD, {
            "run_id": self.run_id,
            "styles": styles_as_css,
        })
        fd.write(out)

    def _add_footer(self, fd):
        out = pystache.render(TEMPLATE_END, {"run_id": self.run_id})
        fd.write(out)

    def _ffc(self, n, proc=lambda x: x != 0):
        return "<span class=\"fail\">{}</span>".format(n) if proc(n) else str(n)

    def _ffs(self, n, proc=lambda x: x != 0):
        return self._ffc(n, proc=lambda x: x != "PASS")

    def _prepare_output(self, output):
        _output = html.escape(output + c.ENDC)
        conv = ansi2html.Ansi2HTMLConverter()
        _output = conv.convert(c.ENDC + _output + c.ENDC, full=False)
        return _output

    def _make_bar(self, count, total, figs=1):
        perc = round((count / total) * 100, figs)
        return "<div class=\"bar-outer\"><div class=\"bar-inner\" style=\"width:{}%\">{} %</div></div>".format(perc, perc)

    def _add_summary_table(self, fd):
        to_render = {
            "tests": [
                {
                    "name": t_name,
                    "count_passing": len([t for t in t_infos if t.is_passing()]),
                    "count_failing": self._ffc(len([t for t in t_infos if not t.is_passing()])),
                    "percent": self._make_bar(len([t for t in t_infos if not t.is_passing()]),
                                              len([t for t in t_infos])),
                    "t_passing": [{
                        "name": t.name,
                        "score": t.t.get_score_str(),
                        "output": self._prepare_output(t.t.get_output()),
                    } for t in t_infos if t.is_passing()],
                    "t_failing": [{
                        "name": t.name,
                        "score": t.t.get_score_str(),
                        "output": self._prepare_output(t.t.get_output()),
                    } for t in t_infos if not t.is_passing()],
                 } for t_name, t_infos in self.test_map.items()
            ]
        }
        out = pystache.render(TEMPLATE_SUMMARY, to_render)
        fd.write(out)

    def _add_test_table(self, fd):
        to_render = {
            "results": [
                {
                    "name": r_name,
                    "total": sr.results.get_total_tests(),
                    "passed": sr.results.get_total_passed(),
                    "failed": self._ffc(sr.results.get_total_failed()),
                    "score": sr.results.get_score(),
                    "max_score": sr.results.get_max_score(),
                    "tests": [{
                        "name": t.get_name(),
                        "status": self._ffs("PASS" if t.is_passing() else "FAIL"),
                        "score": t.get_score_str(),
                        "output": self._prepare_output(t.get_output()),
                    }
                              for t in sr.results.get_tests()]
                } for r_name, sr in self.result_map.items()
            ],
        }
        header = {}
        self._render(TEMPLATE_STUDENT_HEAD, header)

        for r_name, sr in self.result_map.items():
            summary = {
                "name": r_name,
                "total": sr.results.get_total_tests(),
                "passed": sr.results.get_total_passed(),
                "failed": self._ffc(sr.results.get_total_failed()),
                "score": sr.results.get_score(),
                "max_score": sr.results.get_max_score(),
            }
            self._render(TEMPLATE_STUDENT_SUMMARY, summary)

            tests_to_print = [t for t in sr.results.get_tests() if not t.is_passing()]
            test_data = {
                "tests": [{
                    "name": t.get_name(),
                    "status": self._ffs("PASS" if t.is_passing() else "FAIL"),
                    "score": t.get_score_str(),
                    "output": self._prepare_output(t.get_output()),
                } for t in tests_to_print],
            }
            self._render(TEMPLATE_STUDENT_TEST, test_data)

    def _render(self, template, data):
        assert(self.fd)
        out = pystache.render(template, data)
        self.fd.write(out)

    def _set_fd(self, fd):
        self.fd = fd

    def do_summary(self, output_file: str):
        with open(output_file, "w") as fd:
            self._set_fd(fd)
            self._add_header(fd)
            self._add_summary_table(fd)
            self._add_test_table(fd)
            self._add_footer(fd)
