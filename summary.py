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


def order_by_dict(d, sort="value", reverse=True):
    if sort == "value":
        proc = lambda k: d[k]
    elif sort == "len":
        proc = lambda k: len(d[k])
    elif sort == "key":
        proc = lambda k: k
    else:
        proc = sort

    ordering = sorted(list(d.keys()), key=proc,
                      reverse=reverse)
    for key in ordering:
        value = d[key]
        yield key, value


TEMPLATE_HEAD = """
<DOCTYPE html>
<head>
<title>Results for run {{ run_id }}</title>
<style>

table {
  border-collapse: collapse;
  width: 100%;
}

td, th {
  padding: 0.3em;
  border: 1px solid #dddddd;
}

tr:nth-child(even) {
    background-color: #f2f2f2;
}

.fill-fail {
   background-color: #ffcfc9 !important;
}

.fill-pass {
   background-color: #d4edbc !important;
}

.link-btn {
  color: black;
  text-decoration: none;
}

.link-btn-underline {
  color: black;
  text-decoration: underline;
}


tr:hover {
   background-color: #dddddd;
}

th {
  padding-top: 0.5em;
  padding-bottom: 0.5em;
  text-align: left;
  background-color: #2286f4;
  color: white;
}

.table-pertest-summary th {
    background-color: #220022;
}

.table-pertest-submissions th {
    background-color: #222222;
}

.test-row-status {
    vertical-align: top;
}

.fail {
    color: #ff0000;
}

.pass  {
    color: #4caf50;
}

.bar-outer {
    background-color: #eeeeee;
    border-radius: 1px;
    overflow: hidden;
    border: 1px solid #2222;
    height: 1.2em;
    width: 150px;
    position: relative;
}

.bar-inner {
    height: 100%;
    text-align: right;
    padding-right: 1px;
    font-weight: bold;
    line-height: 1.2em;
}

.bar-text {
    height: 100%;
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    display: flex;
    justify-content: center;
    align-items: center;
    color: #212121;
}

.bar-green  { background-color: #4caf50; }
.bar-yellow { background-color: #ffeb3b; }
.bar-orange { background-color: #ff9800; }
.bar-red    { background-color: #f44336; }
.bar-grey   { background-color: #eeeeee; }

.container {
    width: 800px;
}

.test-summary {
     width: 100%;
}

.test-output summary {
    font-size: 0.9em;
}

.test-output pre {
    color: white;
    background-color: black;
}

th {
  cursor: pointer;
}

{{{ styles }}}
</style>
<script type="text/javascript">
function expandAllDetails(divId) {
    section = document.getElementById(divId);
    if (!section) {
        console.log("Could not find a div named " + divId);
        return;
    }
    section.querySelectorAll('details')
    .forEach((e) => {(e.hasAttribute('open')) ?
        e.removeAttribute('open') : e.setAttribute('open',true);
        console.log(e.hasAttribute('open'))
    })
}
</script>
</head>
<body>
<div class="container">
<h1 class="title">Results for run {{ run_id }}</h1>
"""

TEMPLATE_END = """
</div>
<script type="text/javascript">
// https://stackoverflow.com/questions/14267781/sorting-html-table-with-javascript

const getCellValue = (tr, idx) => tr.children[idx].innerText || tr.children[idx].textContent;

const comparer = (idx, asc) => (a, b) => ((v1, v2) =>
    v1 !== '' && v2 !== '' && !isNaN(v1) && !isNaN(v2) ? v1 - v2 : v1.toString().localeCompare(v2)
    )(getCellValue(asc ? a : b, idx), getCellValue(asc ? b : a, idx));

// do the work...
document.querySelectorAll('th').forEach(th => th.addEventListener('click', (() => {
    const table = th.closest('table');
    Array.from(table.querySelectorAll('tr:nth-child(n+2)'))
        .sort(comparer(Array.from(th.parentNode.children).indexOf(th), this.asc = !this.asc))
        .forEach(tr => table.appendChild(tr) );
})));

console.log("yo");
</script>
</body>
</html>
"""

@dataclass
class SubmissionTest():
    name: str
    t: STest
    result: 'SubmissionResult'

    def is_passing(self):
        return self.t.is_passing()

@dataclass
class SubmissionResult():
    name: str
    results: SResults


class SummaryDocument():

    def __init__(self):
        self._fd = None

    @property
    def fd(self):
        if self._fd is None:
            raise AttributeError("File descriptor not set")

        return self._fd

    def add_header(self):
        pass

    def add_footer(self):
        pass

    def get_passes(self):
        raise NotImplementedError("subclass must implement")

    def _render(self, template, data):
        out = pystache.render(template, data)
        self.fd.write(out)

    def _write(self, data):
        self.fd.write(data)

    def do_summary(self, output_file: str):
        with open(output_file, "w") as fd:
            self._fd = fd
            self.add_header()

            passes = self.get_passes()
            for p in passes:
                p.write(self)

            self.add_footer()

class SummaryPass():

    def __init__(self):
        pass

    def write(self, doc):
        raise NotImplementedError("Subclass must implement")


class GSSummary(SummaryDocument):
    result_map: dict[str, SubmissionResult]
    test_map: dict[str, list[SubmissionTest]]
    all_tests: set[str]
    passes: list['GSSummaryPass']
    flagged_results: dict[str, list[SubmissionResult]]
    excluded_names: set[str]

    TEST_ERROR_NO_PASSING = "No passing tests"
    TEST_ERROR_RUN_FAILURE = "Run failure"

    def __init__(self, run_id):
        super(GSSummary, self).__init__()

        self.run_id = run_id
        self.result_map = {}
        self.test_map = defaultdict(list)
        self.all_tests = set()
        self.flagged_results = defaultdict(list)
        self.excluded_names = set()


        self.passes = [
            AllTestSummary(),
            PerTestSummary(),
            PerStudentResults(),
        ]
        self.style_blocks = []

        self._add_ansi_styles()


    def get_passes(self):
        return self.passes

    def add(self, name, res: SResults):
        tests = res.get_tests()
        test_names = [t.name for t in tests]
        self.all_tests.update(test_names)

        sr = SubmissionResult(name=name, results=res)
        self.result_map[name] = sr

        if res.is_all_failing():
            self.flagged_results[self.TEST_ERROR_NO_PASSING].append(sr)
            self.excluded_names.add(name)
            return

        for t in tests:
            t_name = t.name
            tr = SubmissionTest(name=name, t=t, result=sr)
            self.test_map[t_name].append(tr)


    def total_submissions_with_failing(self):
        return len(self.result_map)

    def total_submissions(self):
        return len(self.result_map) - len(self.excluded_names)

    def total_failing(self):
        return len(self.excluded_names)

    def get_test_results(self, test_name):
        if test_name not in self.all_tests:
            raise ValueError("Test not found:  {}".format(test_name))
        return self.test_map[test_name]

    def _add_style(self, block):
        self.style_blocks.append(block)

    def _add_ansi_styles(self):
        styles = get_styles()
        styles_as_css = "\n".join([f"{x.klass} {{ {x.kw}  }}" for x in styles])
        self._add_style(styles_as_css)

    def add_header(self):
        all_styles = "\n".join(self.style_blocks)
        self._render(TEMPLATE_HEAD, {
            "run_id": self.run_id,
            "styles": all_styles,
        })

    def add_footer(self):
        self._render(TEMPLATE_END, {"run_id": self.run_id})

    def _ffc(self, n, proc=lambda x: x != 0):
        return "<span class=\"fail\">{}</span>".format(n) if proc(n) else str(n)

    def _ffs(self, n, proc=lambda x: x != 0):
        if n == "PASS":
            return "<span class=\"pass\">{}</span>".format(str(n))
        else:
            return self._ffc(n, proc=lambda x: x != "PASS")

    def _prepare_output(self, output):
        _output = html.escape(output + c.ENDC)
        conv = ansi2html.Ansi2HTMLConverter()
        _output = conv.convert(c.ENDC + _output + c.ENDC, full=False)
        return _output

    def _get_bar_color(self, val):
        if val < 1:
            return "bar-grey"
        elif val < 70:
            return "bar-red"
        elif val < 80:
            return "bar-orange"
        elif val < 90:
            return "bar-yellow"
        else:
            return "bar-green"

    def _perc(self, count, total, figs=1):
        perc = round((count / total) * 100, figs)
        return perc

    def _make_bar(self, value, max=100, perc=True, text=None, color_proc=None):
        p = round((value / max) * 100, 0)
        width = p
        _text = text if text is not None else str("{}%".format(self._perc(value, max) if perc else value))
        _color_proc = color_proc if color_proc is not None else \
            lambda x: self._get_bar_color(x)

        return "<div class=\"bar-outer\"><div class=\"bar-inner {}\" style=\"width:{}%\"></div><div class=\"bar-text\">{}</div></div>".format(_color_proc(p if perc else value), width, _text)

class GSSummaryPass(SummaryPass):

    def write(self, sd: GSSummary):
        raise NotImplementedError("Subclass must implement")


class AllTestSummary(GSSummaryPass):

    STYLES = """
    """

    TEMPLATE = """
    <h1 id="summary">Test summary</h1>
    <table id="counts">
    <tr>
    <th>Category</th><th>Count</th><th>%</th>
    </tr>
    {{ #counts }}
    <tr><td>{{ name }}</td><td>{{ count }}</td><td>{{{ percent }}}</td></tr>
    {{ /counts }}
    </table>

    <br />
    <br />
    <table id="test-summary">
    <tr>
    <th>Test name</th><th>Passing</th><th>Failing</th><th>%</th>
    </tr>
    {{ #tests }}
    <tr><td><a href="#test-{{name}}">{{ name }}</a></td><td>{{ count_passing }}</td><td>{{{ count_failing }}}</td><td>{{{ percent }}}</td></tr>
    {{ /tests }}
    </table>

    <h2 id="flagged">Flagged submissions</h2>
    {{ #counts }}
    <h4>{{ name }} ({{ count }})</h4>
    <ul>
    {{ #submissions }}
    <li><a href="#results-{{ . }}">{{ . }}</a></li>
    {{ /submissions }}
    </ul>
    {{ /counts }}
    """

    def __init__(self):
        pass

    def write(self, doc: GSSummary):
        to_render = {
            "tests": [
                {
                    "name": t_name,
                    "count_passing": len([t for t in t_infos if t.is_passing()]),
                    "count_failing": doc._ffc(len([t for t in t_infos if not t.is_passing()])),
                    "percent": doc._make_bar(len([t for t in t_infos if t.is_passing()]),
                                             max=len([t for t in t_infos])),
                 } for t_name, t_infos in doc.test_map.items()
            ]
        }

        count_info = []
        def _add(name, submissions, count=0):
            total = doc.total_submissions_with_failing()
            if submissions is None or len(submissions) == 0:
                _count = count
            else:
                _count = len(submissions)

            count_info.append({
                "name": name,
                "count": _count,
                "submissions": [s.name for s in submissions] if submissions is not None else [],
                "percent": doc._make_bar(_count, max=total),

            })

        _add("Submissions with >1 passing test", None, count=doc.total_submissions())

        for name, submissions in doc.flagged_results.items():
            _add(name, submissions)

        _add("All failures", None, count=doc.total_failing())
        _add("Total submissions", None, count=doc.total_submissions_with_failing())

        to_render["counts"] = count_info

        doc._render(self.TEMPLATE, to_render)

class PerTestSummary(GSSummaryPass):

    TEMPLATE_ORIG = """
    <h1 id="per-test">Per-test summary</h1>
    {{ #tests }}
    <div id="c-pertest-{{ name }}">
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
    </div>
    {{ /tests }}
    """

    TEMPLATE = """
    <h1 id="per-test">Per-test summary</h1>

    {{ #tests }}
    <h2 id="test-{{ name }}">{{ name }}</h2>
    <table class="table-pertest-summary">
    <tr>
    <th>Test name</th><th>Passing</th><th>Failing</th><th>%</th>
    </tr>
    <tr><td>{{ name }}</td><td>{{ count_passing }}</td><td>{{{ count_failing }}}</td><td>{{{ percent }}}</td></tr>
    </table>
    <br /><br />
    <div id="c-pertest-{{ name }}">
    <a href="javascript:;" onclick="expandAllDetails('c-pertest-{{name}}');" class="link-btn-underline">[Show/hide output]</a>
    <table class="table-pertest-submissions">
    <tr>
    <th width="5%">S</th><th width="80%">Name</th><th>Score</th>
    </tr>
    {{ #t_failing }}
    <tr class="row-fail">
    <td class="test-row-status fill-fail">❌</td>
    <td>
      {{ name }}  <a href="#results-{{name}}" class="link-btn">🔍</a><br /><details class="test-output"><summary class="test-output">Output</summary>
      <pre>
      {{{ output }}}
      </pre>
      </details>
    </td>
    <td>{{{ score }}} {{{ percent }}}</td>
    </tr>
    {{ /t_failing }}
    {{ #t_passing }}
    <tr>
    <td class="test-row-status fill-pass">✅</td>
    <td>
      {{ name }}<a href="#results-{{name}}" class="link-btn">🔍</a><br /><details class="test-output"><summary class="test-output">Output</summary>
      <pre>
      {{{ output }}}
      </pre>
      </details>
    </td>
    <td>{{{ score }}} {{{ percent }}}</td>
    </tr>
    {{ /t_passing }}
    </table>
    </div>
    {{ /tests }}
    """

    def __init__(self):
        pass

    def write(self, doc: GSSummary):
        def _order(x: list[SubmissionTest], reverse=True):
            return sorted(x, key=lambda x: x.result.results.get_total_passed(), reverse=reverse)

        to_render = {
            "tests": [
                {
                    "name": t_name,
                    "count_passing": len([t for t in t_infos if t.is_passing()]),
                    "count_failing": doc._ffc(len([t for t in t_infos if not t.is_passing()])),
                    "percent": doc._make_bar(len([t for t in t_infos if t.is_passing()]),
                                             max=len([t for t in t_infos])),
                    "t_passing": [{
                        "name": t.name,
                        "score": t.t.get_score_str(),
                        "percent": doc._make_bar(t.result.results.get_total_passed(),
                                                 max=t.result.results.get_total_tests()),
                        "output": doc._prepare_output(t.t.get_output()),
                    } for t in _order(t_infos) if t.is_passing()],
                    "t_failing": [{
                        "name": t.name,
                        "score": t.t.get_score_str(),
                        "output": doc._prepare_output(t.t.get_output()),
                        "percent": doc._make_bar(t.result.results.get_total_passed(),
                                                 max=t.result.results.get_total_tests()),
                    } for t in _order(t_infos) if not t.is_passing()],
                 } for t_name, t_infos in doc.test_map.items()
            ]
        }
        doc._render(self.TEMPLATE, to_render)


class PerStudentResults(GSSummaryPass):

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
    <div id="c-perstudent-{{ name }}">
    <a href="javascript:;" onclick="expandAllDetails('c-perstudent-{{ name }}');" class="link-btn-underline">[Show/hide output]</a>
    <table>
    <tr>
    <th>Test</th><th width="15%">Score</td><th width="15%">Status</td>
    </tr>
    {{ #tests }}
    <tr><td>{{ name }} <br /><details class="test-output"><summary class="test-output">Output</summary>
    <pre>
    {{{ output }}}
    </pre>
    </details>
    </td>
    <td>{{ score }}</td><td>{{{ status }}}</td></tr>
    {{ /tests }}
    </table>
    </div>
    <br />
    <br />

    """

    def __init__(self):
        pass

    def write(self, doc: GSSummary):
        to_render = {
            "results": [
                {
                    "name": r_name,
                    "total": sr.results.get_total_tests(),
                    "passed": sr.results.get_total_passed(),
                    "failed": doc._ffc(sr.results.get_total_failed()),
                    "score": sr.results.get_score(),
                    "max_score": sr.results.get_max_score(),
                    "tests": [{
                        "name": t.get_name(),
                        "status": doc._ffs("PASS" if t.is_passing() else "FAIL"),
                        "score": t.get_score_str(),
                        "output": doc._prepare_output(t.get_output()),
                    }
                              for t in sr.results.get_tests()]
                } for r_name, sr in doc.result_map.items()
            ],
        }
        header = {}
        doc._render(self.TEMPLATE_STUDENT_HEAD, header)

        for r_name, sr in doc.result_map.items():
            d = {
                "name": r_name,
                "total": sr.results.get_total_tests(),
                "passed": sr.results.get_total_passed(),
                "failed": doc._ffc(sr.results.get_total_failed()),
                "score": sr.results.get_score(),
                "max_score": sr.results.get_max_score(),
            }
            doc._render(self.TEMPLATE_STUDENT_SUMMARY, d)

            tests_to_print = [t for t in sr.results.get_tests()]
            test_data = {
                "tests": [{
                    "name": t.get_name(),
                    "status": doc._ffs("PASS" if t.is_passing() else "FAIL"),
                    "score": t.get_score_str(),
                    "output": doc._prepare_output(t.get_output()),
                } for t in tests_to_print],
            }
            d.update(test_data)
            doc._render(self.TEMPLATE_STUDENT_TEST, d)
