"""The shared run loop: jobs, store reuse, labels and failures that stay local to their setting."""

from __future__ import annotations

import pickle
from pathlib import Path

from abmfact.modes.batch import Job, JobResult, _BatchTask, engine_status, run_batch

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
SHORT = {"experiment.max_time": 120, "experiment.save_interval": 60}


def test_jobs_run_in_order_and_a_store_reuses_them(tmp_path):
    jobs = [Job("reference", s, SHORT) for s in (2, 0, 1)]
    first = run_batch(MODEL, jobs, workdir=tmp_path / "w", store=tmp_path / "store")
    assert [r.job.seed for r in first] == [2, 0, 1]
    assert all(r.ok and r.record.status == "ok" for r in first)
    again = run_batch(MODEL, jobs, workdir=tmp_path / "w2", store=tmp_path / "store")
    assert all(r.record.status == "cached" for r in again)
    assert [r.record.outputs.scalars["cells"] for r in again] == [r.record.outputs.scalars["cells"] for r in first]


def test_parameters_reach_the_engine_and_change_the_run(tmp_path):
    low = {**SHORT, "cell_types[epithelial].cycle.rates[0]": 0.0}
    a, b = run_batch(MODEL, [Job("reference", 0, SHORT), Job("reference", 0, low)], workdir=tmp_path)
    assert a.ok and b.ok
    assert b.record.outputs.scalars["cells"][-1] <= a.record.outputs.scalars["cells"][-1]
    assert a.record.request.key() != b.record.request.key()


def test_a_setting_that_cannot_prepare_fails_only_its_own_jobs(tmp_path):
    bad = {**SHORT, "cell_types[nobody].volume": 1.0}
    out = run_batch(MODEL, [Job("reference", 0, SHORT), Job("reference", 0, bad), Job("reference", 1, bad)],
                    workdir=tmp_path)  # fmt: skip
    assert out[0].ok
    assert not out[1].ok and out[1].problem.startswith("could not prepare a run")
    assert out[2].problem == out[1].problem


def test_an_unknown_engine_is_an_error_not_a_crash(tmp_path):
    (r,) = run_batch(MODEL, [Job("nosuch", 0)], workdir=tmp_path)
    assert not r.ok and "unknown engine" in r.problem
    reason, _ = engine_status("nosuch", MODEL)
    assert reason and "unknown engine" in reason


def test_labels_give_each_setting_its_own_folder(tmp_path):
    assert Job("e", 3, label="p7").folder(tmp_path) == tmp_path / "e" / "p7" / "seed-3"
    assert Job("e", 3).folder(tmp_path) == tmp_path / "e" / "seed-3"


def test_the_task_pickles_without_its_engine_cache(tmp_path):
    task = _BatchTask(str(MODEL), tmp_path, None)
    task(Job("reference", 0, SHORT))
    assert task._engines
    assert pickle.loads(pickle.dumps(task))._engines == {}


def test_a_result_without_a_record_explains_itself():
    r = JobResult(Job("e", 0), error="boom")
    assert not r.ok and r.problem == "boom"
