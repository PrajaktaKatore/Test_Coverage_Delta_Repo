"""
EXPLORE · KPI #20 · Test Coverage Delta (AI-Assisted)  (console-only, standalone)
 
Self-contained: needs only `httpx` + the standard library. Reads config from
.secrets/project.json (or $KPILAB_CONFIG), tools.sonarqube. Prints, at a granular
level, every in-scope piece of SonarQube coverage data for this KPI (current overall
and new-code coverage measures, the coverage trend over time with monthly buckets,
and per-PR new-code coverage where branch/PR analysis is available), then the delta.
 
Definition (Excel): Change in automated test coverage % for AI-assisted code vs the
pre-AI coverage baseline.
Formula:  (coverage % for AI-assisted code)  -  (pre-AI baseline coverage %).
Cadence: per PR / monthly.
 
Where each piece comes from (SonarQube Web API):
  - Overall coverage     GET /api/measures/component       metricKeys=coverage,...
  - AI-assisted slice    new_coverage = coverage on the NEW-CODE period (the recent /
                         changed code). On a PR, new_coverage = coverage of that PR's
                         changed lines. Sonar has NO "written by AI" flag, so new-code
                         coverage is the closest proxy for "AI-assisted code coverage".
  - Trend / history      GET /api/measures/search_history  metrics=coverage,new_coverage
  - Per-PR coverage      GET /api/project_pull_requests/list  +  measures?pullRequest=N
                         (needs Developer edition / SonarCloud Team — branch/PR analysis)
  - Baseline             tools.sonarqube.baselineCoverage (config; no API records pre-AI).
                         If unset, the earliest history point is used as a fallback.
 
IMPORTANT: SonarQube does NOT compute coverage — it imports a coverage report that
your CI uploads at analysis time. If CI never uploads one, coverage is null and this
KPI has no data (flagged clearly).
 
No token? Public SonarCloud projects are readable anonymously:
  python explore/kpi_20_test_coverage_delta.py --host https://sonarcloud.io \
      --org <org> --project <projectKey>
 
RUN (from the kpi-verify directory):
  python explore/kpi_20_test_coverage_delta.py
  python explore/kpi_20_test_coverage_delta.py --baseline 68.0
  python explore/kpi_20_test_coverage_delta.py --from 2026-01-01
"""
from __future__ import annotations
 
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
 
import httpx
 
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass
 
 
# --------------------------------------------------------------------------
# tiny standalone config + SonarQube GET (no _toolkit dependency)
# --------------------------------------------------------------------------
_CFG = None
 
 
def cfg(tool, key, default=""):
    global _CFG
    if _CFG is None:
        p = os.environ.get("KPILAB_CONFIG") or str(
            Path(__file__).resolve().parent.parent / ".secrets" / "project.json")
        with open(p, encoding="utf-8") as f:
            _CFG = json.load(f)
    v = (_CFG.get("tools", {}).get(tool, {}) or {}).get(key, default)
    return v.strip() if isinstance(v, str) else v
 
 
def arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default
 
 
HOST = (arg("--host") or cfg("sonarqube", "baseUrl") or "https://sonarcloud.io").rstrip("/")
TOKEN = arg("--token") or cfg("sonarqube", "token")
ORG = arg("--org") or cfg("sonarqube", "organization")
PROJECT = arg("--project") or cfg("sonarqube", "projectKey")
AI_AUTHORS = [a.lower() for a in (cfg("sonarqube", "aiAuthors") or []) if a]
_b = arg("--baseline") or cfg("sonarqube", "baselineCoverage", None)
BASELINE = float(_b) if _b not in (None, "", "None") else None
FROM = arg("--from")    # history start YYYY-MM-DD
 
 
def sget(client, path, params):
    if ORG:
        params = {**params, "organization": ORG}
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        r = client.get(HOST + path, params=params, headers=headers)
    except Exception as e:
        return 0, {"_error": str(e)}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, None
 
 
def hr(ch="-"):
    print(ch * 78)
 
 
def fnum(v):
    try:
        return float(v)
    except Exception:
        return None
 
 
def band(delta):
    """Interpretation band from the Excel sample-value column."""
    if delta is None:
        return "-"
    if delta < 0:
        return "Critical (AI reducing coverage)"
    if delta <= 5:
        return "Neutral (0-5%)"
    if delta <= 10:
        return "Good (+5-10%, L3 target)"
    if delta <= 20:
        return "Excellent (+10-20%)"
    return "L5 full AI test generation (+20%+)"
 
 
# --------------------------------------------------------------------------
# data pulls
# --------------------------------------------------------------------------
COVERAGE_KEYS = ("coverage,new_coverage,line_coverage,new_line_coverage,"
                 "branch_coverage,new_branch_coverage,lines_to_cover,uncovered_lines,"
                 "new_lines_to_cover,new_uncovered_lines,tests,test_success_density,"
                 "test_failures,test_errors,skipped_tests")
 
 
def fetch_measures(client, params_extra=None):
    p = {"component": PROJECT, "metricKeys": COVERAGE_KEYS}
    if params_extra:
        p.update(params_extra)
    st, raw = sget(client, "/api/measures/component", p)
    measures = {}
    if st == 200 and isinstance(raw, dict):
        for m in (raw.get("component", {}).get("measures") or []):
            val = m.get("value")
            if val is None and m.get("period"):
                val = m["period"].get("value")
            measures[m.get("metric")] = val
    return st, raw, measures
 
 
def fetch_history(client):
    metrics = "coverage,new_coverage,line_coverage,branch_coverage"
    p = {"component": PROJECT, "metrics": metrics, "ps": 1000}
    if FROM:
        p["from"] = FROM
    points = OrderedDict()   # date -> {metric: value}
    page = 1
    while True:
        st, raw = sget(client, "/api/measures/search_history", {**p, "p": page})
        if st != 200 or not isinstance(raw, dict):
            return st, raw, points
        for m in (raw.get("measures") or []):
            mk = m.get("metric")
            for h in (m.get("history") or []):
                d = h.get("date", "")[:10]
                points.setdefault(d, {})[mk] = h.get("value")
        paging = raw.get("paging", {})
        if page * paging.get("pageSize", 1000) >= paging.get("total", 0):
            break
        page += 1
    return 200, None, points
 
 
def fetch_prs(client):
    st, raw = sget(client, "/api/project_pull_requests/list", {"project": PROJECT})
    if st != 200 or not isinstance(raw, dict):
        return st, raw, []
    return 200, None, raw.get("pullRequests") or []
 
 
# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    hr("=")
    print("KPI #20 · TEST COVERAGE DELTA (AI-ASSISTED)")
    print("Formula: coverage%(AI-assisted / new code)  -  pre-AI baseline coverage%")
    hr("=")
    print(f"Host        : {HOST}")
    print(f"Organization: {ORG or '(none / Server)'}")
    print(f"Project     : {PROJECT or '(NOT SET)'}")
    print(f"Auth        : {'Bearer token' if TOKEN else 'ANONYMOUS (public projects only)'}")
    print(f"Baseline    : {BASELINE if BASELINE is not None else '(unset — will fall back to earliest history point)'}")
 
    if not PROJECT:
        hr()
        print("NO PROJECT CONFIGURED. Set tools.sonarqube.projectKey (+organization for")
        print("SonarCloud), or pass --project <key> [--org <org>] [--host <url>].")
        hr("=")
        print("  KPI VALUE: NOT COMPUTABLE — no project to read.")
        hr("=")
        return
 
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        st, raw, m = fetch_measures(client)
        if st != 200:
            hr()
            print(f"measures/component -> HTTP {st}  "
                  f"{(raw.get('errors') or raw.get('_error')) if isinstance(raw, dict) else ''}")
            print("  (401/403 = private project needs a token; 404 = wrong key/org)")
            hr("=")
            print("  KPI VALUE: NOT COMPUTABLE — could not read project measures.")
            hr("=")
            return
 
        overall = fnum(m.get("coverage"))
        new_cov = fnum(m.get("new_coverage"))
 
        # ---- CURRENT COVERAGE --------------------------------------------
        hr()
        print("CURRENT COVERAGE (WHAT the test suite covers now)")
        hr()
        if overall is None and new_cov is None:
            print("  *** NO COVERAGE DATA ***")
            print("  Sonar imports coverage from a CI-uploaded report; none is present for")
            print("  this project. Wire coverage upload into the pipeline (sonar.<lang>.coverage")
            print("  reportPaths) and re-run. Everything below depends on it.")
        else:
            def show(label, key, suffix="%"):
                v = m.get(key)
                if v is not None:
                    print(f"  {label:<34}: {v}{suffix}")
            show("Overall coverage", "coverage")
            show("New-code coverage (AI-assisted slice)", "new_coverage")
            show("Line coverage", "line_coverage")
            show("New-code line coverage", "new_line_coverage")
            show("Branch coverage", "branch_coverage")
            show("New-code branch coverage", "new_branch_coverage")
            show("Lines to cover", "lines_to_cover", "")
            show("Uncovered lines", "uncovered_lines", "")
            show("New lines to cover", "new_lines_to_cover", "")
            show("New uncovered lines", "new_uncovered_lines", "")
            show("Tests", "tests", "")
            show("Test success density", "test_success_density")
            show("Test failures / errors", "test_failures", "")
 
        # ---- HISTORY / TREND ---------------------------------------------
        h_st, h_raw, points = fetch_history(client)
        first_pt = last_pt = None
        if points:
            dated = [(d, v) for d, v in points.items() if v.get("coverage") is not None]
            hr()
            print(f"COVERAGE TREND (WHEN it changed)   {len(dated)} analysis points with coverage")
            hr()
            if dated:
                first_pt, last_pt = dated[0], dated[-1]
                # monthly buckets: last value within each YYYY-MM
                monthly = OrderedDict()
                for d, v in dated:
                    monthly[d[:7]] = fnum(v.get("coverage"))
                print("  Monthly (last analysis in month):")
                prev = None
                for ym, cov in monthly.items():
                    delta = f"{cov - prev:+.2f}" if (prev is not None and cov is not None) else "   -  "
                    print(f"    {ym}   coverage={cov:.2f}%   Δ vs prev month={delta}")
                    prev = cov
                fc = fnum(first_pt[1].get("coverage"))
                lc = fnum(last_pt[1].get("coverage"))
                print(f"  First point {first_pt[0]}: {fc:.2f}%   ->   Last point {last_pt[0]}: {lc:.2f}%"
                      f"   (history Δ {lc - fc:+.2f} pts)")
            else:
                print("  (history has analyses but no coverage values — CI not uploading coverage)")
 
        # ---- PER-PR COVERAGE ---------------------------------------------
        p_st, p_raw, prs = fetch_prs(client)
        if prs:
            hr()
            print(f"PER-PR NEW-CODE COVERAGE (per-PR cadence)   {len(prs)} open PRs analysed")
            print("  (new_coverage on a PR = coverage of that PR's changed lines)")
            hr()
            for pr in prs[:40]:
                key = pr.get("key")
                title = (pr.get("title") or "")[:54]
                br = pr.get("branch", "")
                _, _, pm = fetch_measures(client, {"pullRequest": key})
                pcov = fnum(pm.get("new_coverage"))
                covs = f"{pcov:.1f}%" if pcov is not None else "no coverage"
                d = (f"{pcov - BASELINE:+.1f}" if (pcov is not None and BASELINE is not None) else "-")
                print(f"  PR {key}  new_coverage={covs}  Δ vs baseline={d}  [{br}] {title}")
        elif p_st == 200:
            hr()
            print("PER-PR NEW-CODE COVERAGE: no open PRs analysed (or branch/PR analysis not")
            print("  enabled — needs SonarQube Developer edition / SonarCloud Team).")
 
        # ---- KPI VALUE ----------------------------------------------------
        hr("=")
        print("KPI VALUE · TEST COVERAGE DELTA")
        hr("=")
        if overall is None and new_cov is None and not (points and first_pt):
            print("  NOT COMPUTABLE — no coverage data anywhere (CI must upload coverage to Sonar).")
            hr("=")
            return
 
        # The "AI-assisted code coverage" term: prefer new_coverage; fall back to overall.
        ai_cov = new_cov if new_cov is not None else overall
        ai_label = "new-code coverage" if new_cov is not None else "overall coverage (no new-code value)"
 
        # The baseline term: config, else earliest history point.
        base = BASELINE
        base_src = "config baselineCoverage"
        if base is None and first_pt:
            base = fnum(first_pt[1].get("coverage"))
            base_src = f"earliest history point {first_pt[0]} (fallback — NOT a true pre-AI baseline)"
 
        print(f"  AI-assisted coverage  = {ai_cov:.2f}%   ({ai_label})"
              if ai_cov is not None else "  AI-assisted coverage  = (none)")
        if base is None:
            print("  Baseline coverage     = NOT AVAILABLE — set tools.sonarqube.baselineCoverage")
            print("  DELTA: NOT COMPUTABLE without a baseline.")
        elif ai_cov is not None:
            delta = ai_cov - base
            print(f"  Baseline coverage     = {base:.2f}%   ({base_src})")
            print(f"  DELTA                 = {ai_cov:.2f} - {base:.2f} = {delta:+.2f} pts")
            print(f"  Interpretation        = {band(delta)}")
        # trend delta as a secondary signal
        if points and first_pt and last_pt:
            fc, lc = fnum(first_pt[1].get("coverage")), fnum(last_pt[1].get("coverage"))
            if fc is not None and lc is not None:
                print(f"  Trend delta (history) = {lc - fc:+.2f} pts "
                      f"({first_pt[0]} -> {last_pt[0]})  ->  {band(lc - fc)}")
        hr()
        print("  Note: 'AI-assisted' here = new-code coverage. Sonar has no AI-authorship flag;")
        print("  to isolate strictly AI-authored code, attribute via PR author / SCM (see KPI #14).")
        hr("=")
 
 
if __name__ == "__main__":
    main()