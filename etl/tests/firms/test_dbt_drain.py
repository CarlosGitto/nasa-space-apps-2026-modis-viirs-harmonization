"""The drain loop: it stops when nothing is pending, when a run fails, or after max_iterations (with a fake `dbt`)."""

import stat

import pytest

from include.firms.dbt_drain import drain, remaining_after_run

ANSI_LINE = '\x1b[0m03:49:10  "lake"."silver"."int_detections_sp": 5 pending blocks of 7 days, this run: 2019-06-03, 2019-06-10'


def test_remaining_after_run_counts_what_is_left_per_model():
    out = "\n".join([
        ANSI_LINE,
        '03:49:12  "lake"."gold"."fct_hexagon_monthly": 1 pending months, this run: 2019-08-01',
        '03:49:13  "lake"."silver"."int_detections_nrt": 0 pending blocks of 7 days, this run: none',
        "03:49:14  something else that is not a pending line",
    ])
    assert remaining_after_run(out) == {"int_detections_sp": 3, "fct_hexagon_monthly": 0, "int_detections_nrt": 0}


def _fake_dbt(tmp_path, body):
    script = tmp_path / "dbt"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def project(tmp_path):
    profiles = tmp_path / "profiles.yml"
    profiles.write_text("firms: {}\n")
    return tmp_path, profiles


def test_drain_runs_until_nothing_is_pending(project):
    tmp, profiles = project
    counter = tmp / "n"
    counter.write_text("0")
    # 3 blocks pending in total, one taken per run: after run 3 nothing is left
    dbt = _fake_dbt(tmp, f'n=$(cat {counter}); n=$((n+1)); echo $n > {counter}; left=$((4-n))\n'
                         'echo "\\"lake\\".\\"silver\\".\\"int_detections_sp\\": $left pending blocks of 7 days, this run: 2019-06-03"\n')
    assert drain(dbt, tmp, profiles, max_iterations=10) == 3


def test_drain_stops_with_an_error_when_dbt_fails(project):
    tmp, profiles = project
    dbt = _fake_dbt(tmp, 'echo "boom: Out of Memory Error"\nexit 1\n')
    with pytest.raises(RuntimeError, match="failed in run 1"):
        drain(dbt, tmp, profiles, max_iterations=10)


def test_drain_gives_up_when_the_backlog_does_not_shrink(project):
    tmp, profiles = project
    dbt = _fake_dbt(tmp, 'echo "\\"lake\\".\\"silver\\".\\"int_detections_sp\\": 9 pending blocks of 7 days, this run: 2019-06-03"\n')
    with pytest.raises(RuntimeError, match="still pending after 3 runs"):
        drain(dbt, tmp, profiles, max_iterations=3)
