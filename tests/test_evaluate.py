"""pipelines/evaluate.py: the code behind every number in the report's evaluation section.

Pure functions on small hand-made rows, report() on temporary files, the senior runner with a
fake adapter, and a regression test that recomputes the committed results from the committed
data. No MLServer, MLflow, network or API key is needed."""

import asyncio
import csv
import json
import math
from pathlib import Path

import pytest

from common.metrics import wilson
from common.splits import split_of
from pipelines import evaluate as ev


def row(qid, gold="A", j1="A", j2="A", task="choice", agreed=None, stage_ms=1000.0, group=None):
    return {
        "question_id": qid,
        "group": group or f"{task}:{qid}",
        "task_type": task,
        "gold": gold,
        "j1_answer": j1,
        "j2_answer": j2,
        "agreed": (j1 is not None and j1 == j2) if agreed is None else agreed,
        "junior_stage_ms": stage_ms,
        "n_options": 4 if task == "choice" else 77,
        "prompt_tokens": 120,
        "j1_tokens": 2,
        "j2_tokens": 2,
    }


def senior(answer="A", ok=True, cost=0.002, ms=900.0):
    return {"ok": ok, "answer": answer if ok else None, "cost_usd": cost, "latency_ms": ms}


# ------------------------------------------------------------------ outcomes


def test_senior_outcome_counts_failures_as_abstained_and_keeps_their_cost():
    assert ev.senior_outcome(senior("A"), "A") == (1, 0.002, False)
    assert ev.senior_outcome(senior("B"), "A") == (0, 0.002, False)
    assert ev.senior_outcome(senior(ok=False, cost=0.001), "A") == (0, 0.001, True)
    assert ev.senior_outcome({"ok": True, "answer": None, "cost_usd": 0.003}, "A") == (
        0,
        0.003,
        True,
    )
    assert ev.senior_outcome({"ok": False, "answer": None}, "A") == (0, 0.0, True)


def test_pick_falls_back_to_junior_2_when_junior_1_is_unreadable():
    assert ev.pick(row("q", j1="B", j2="C")) == "B"
    assert ev.pick(row("q", j1=None, j2="C")) == "C"
    assert ev.pick(row("q", j1=None, j2=None)) is None


def test_an_accepted_question_is_free_and_returns_the_junior_answer():
    assert ev.routed(row("q", gold="A"), True, {}) == {
        "correct": 1,
        "cost": 0.0,
        "esc": False,
        "abst": False,
        "acc_junior": True,
        "ms": 1001.0,
    }


def test_a_rejected_or_unreadable_question_goes_to_opus_with_its_cost_and_latency():
    opus = {"q": senior("B", cost=0.003, ms=500.0)}
    o = ev.routed(row("q", gold="B", j1="A", j2="A"), False, opus)
    assert (o["correct"], o["cost"], o["esc"], o["acc_junior"], o["ms"]) == (
        1,
        0.003,
        True,
        False,
        1501.0,
    )
    # accepted, but neither junior answer is readable: it escalates anyway
    assert ev.routed(row("q", gold="B", j1=None, j2=None), True, opus)["esc"] is True
    # Opus fails: abstained and not correct
    failed = ev.routed(row("q"), False, {"q": senior(ok=False)})
    assert failed["abst"] is True and failed["correct"] == 0


def test_a_model_alone():
    assert ev.single(row("q", gold="A"), senior("A", ms=700.0))["ms"] == 700.0
    unreadable = row("q", gold="A", j1=None, j2="A")
    j1 = ev.junior(unreadable, "j1")
    assert j1["abst"] is True and j1["correct"] == 0 and j1["cost"] == 0.0
    assert ev.junior(unreadable, "j2")["correct"] == 1


# ---------------------------------------------------------------- strategies


def test_baseline_strategies_cover_every_question_in_table_order():
    rows = [row("a"), row("b", j1="A", j2="B")]
    opus = {"a": senior("A"), "b": senior("A")}
    haiku = {"a": senior("A", cost=0.0001), "b": senior("C", cost=0.0001)}
    s = ev.baseline_strategies(rows, opus, haiku)
    assert list(s) == [ev.JUNIOR1_ALONE, ev.JUNIOR2_ALONE, ev.HAIKU_ALONE, ev.OPUS_ALONE, ev.RULE]
    assert all(len(outs) == 2 for outs in s.values())
    assert [o["esc"] for o in s[ev.RULE]] == [False, True]  # b's juniors disagree, so Opus


def test_policy_strategy_accepts_at_or_above_the_threshold():
    rows = [row("a"), row("b")]
    opus = {"a": senior("A"), "b": senior("A")}
    outs = ev.policy_strategy(rows, opus, {"a": 0.6, "b": 0.59}, 0.6)
    assert [o["esc"] for o in outs] == [False, True]
    assert ev.policy_label(0.567) == "Router, policy v2 (champion, P >= 0.57)"


def test_summarize_accuracy_cost_false_acceptance_and_task_types():
    rows = [
        row("a", gold="A"),  # juniors agree and are right
        row("b", gold="A", j1="B", j2="B"),  # juniors agree and are wrong
        row("c", gold="x", task="label", j1="x", j2="y"),  # disagree, Opus right
    ]
    opus = {"c": senior("x", cost=0.004, ms=1000.0)}
    s = ev.summarize([ev.routed(r, r["agreed"], opus) for r in rows], rows)
    assert s["acc"] == pytest.approx(2 / 3) and s["ci"] == wilson(2, 3)
    assert (s["choice"], s["label"]) == (0.5, 1.0)
    assert s["esc"] == pytest.approx(1 / 3) and s["abst"] == 0.0
    assert s["cost_k"] == pytest.approx(1000 * 0.004 / 3)
    assert s["fa"] == 0.5  # one of the two accepted junior answers is wrong
    assert s["fa_hi"] == pytest.approx(wilson(1, 2)[1])
    assert s["p50"] == 1001.0 and s["p95"] == pytest.approx(1901.0)


def test_summarize_without_junior_answers_or_a_task_type_gives_nan_not_a_crash():
    rows = [row("a")]
    s = ev.summarize([ev.single(rows[0], senior("A"))], rows)
    assert math.isnan(s["fa"]) and math.isnan(s["fa_hi"]) and math.isnan(s["label"])
    assert s["choice"] == 1.0


def test_random_routing_is_the_expected_value_of_mixing_juniors_and_opus():
    rows = [row("a", gold="A"), row("b", gold="A", j1="B", j2="B")]  # junior accuracy 0.5
    opus = {"a": senior("A", cost=0.002), "b": senior("A", cost=0.004)}  # Opus accuracy 1.0
    acc, cost_k = ev.random_routing(rows, opus, 0.25)
    assert acc == pytest.approx(0.75 * 0.5 + 0.25 * 1.0)
    assert cost_k == pytest.approx(1000 * 0.25 * 0.003)


# --------------------------------------------------------------- comparisons


def test_paired_comparison_of_identical_strategies_is_exactly_zero():
    outs = [{"correct": 1, "cost": 0.001, "esc": True}] * 10
    c = ev.paired(outs, outs)
    assert (c["n"], c["differ"], c["acc"], c["acc_ci"], c["cost_k"]) == (10, 0, 0, (0, 0), 0)


def test_paired_comparison_sees_a_strategy_that_is_better_everywhere():
    a = [{"correct": 1, "cost": 0.0, "esc": False}] * 20
    b = [{"correct": 0, "cost": 0.002, "esc": True}] * 20
    c = ev.paired(a, b)
    assert c["differ"] == 20 and c["acc"] == 1.0 and c["acc_ci"] == (1.0, 1.0)
    assert c["cost_k"] == pytest.approx(-2.0) and c["cost_ci"][1] < 0


def test_threshold_curve_lists_every_threshold_then_the_baselines():
    rows = [row("a"), row("b", gold="x", task="label", j1="x", j2="x")]
    opus = {"a": senior("A"), "b": senior("x")}
    stats = {k: ev.summarize(v, rows) for k, v in ev.baseline_strategies(rows, opus, opus).items()}
    curve = ev.threshold_curve(rows, opus, {"a": 0.2, "b": 0.8}, stats)
    assert curve[0][0] == "strategy"
    assert len(curve) == 1 + len(ev.THRESHOLDS) + len(ev.CURVE_BASELINES)
    esc = [r[2] for r in curve[1 : 1 + len(ev.THRESHOLDS)]]
    assert esc[0] == 0.0 and esc[-1] == 1.0 and esc == sorted(esc)
    assert [r[0] for r in curve[-len(ev.CURVE_BASELINES) :]] == list(ev.CURVE_BASELINES)


def test_audit_check_counts_what_an_opus_audit_would_see():
    rows = [
        row("a", gold="A"),
        row("b", gold="A", j1="B", j2="B"),
        row("c", gold="A", j1="C", j2="C"),
        row("d"),
    ]
    opus = {"a": senior("A"), "b": senior("A"), "c": senior(ok=False), "d": senior("A")}
    p = {"a": 0.9, "b": 0.9, "c": 0.9, "d": 0.1}  # d is not accepted
    assert ev.audit_check(rows, opus, p, 0.5) == {
        "accepted": 3,
        "audited": 2,  # Opus failed on c
        "true_fa": 2,  # b and c are wrong
        "disagree": 1,  # Opus disagrees with b only
        "opus_right": 1,
    }


def test_senior_facts():
    rows = [row("a", gold="A"), row("b", gold="B")]
    opus = {"a": senior("A", cost=0.002), "b": senior("C", cost=0.004)}
    wrong, cost = ev.senior_facts(rows, opus)
    assert wrong == 1 and cost == pytest.approx(0.003)


def test_strategy_rows_show_false_acceptance_only_where_junior_answers_are_returned():
    s = {
        "acc": 0.5,
        "ci": (0.4, 0.6),
        "choice": 0.5,
        "label": 0.5,
        "esc": 1.0,
        "abst": 0.0,
        "cost_k": 2.0,
        "fa": 0.1,
        "fa_hi": 0.2,
        "p50": 900.0,
        "p95": 1500.0,
    }
    assert "| – |" in ev.strategy_row(ev.OPUS_ALONE, s)
    assert "| 10.0% (20.0%) |" in ev.strategy_row(ev.JUNIOR1_ALONE, s)
    assert "| 10.0% (20.0%) |" in ev.strategy_row(ev.RULE, s)


# ------------------------------------------------------- report() and files


def split_rows(n_choice=4, n_label=4):
    """Rows whose groups fall in the TEST split, half agreeing, half disagreeing."""
    out, i = [], 0
    while len(out) < n_choice + n_label:
        task = "choice" if sum(r["task_type"] == "choice" for r in out) < n_choice else "label"
        group = f"{task}:question {i}"
        i += 1
        if split_of(group) != "test":
            continue
        k = len(out)
        gold = "A" if task == "choice" else "card_arrival"
        other = "B" if task == "choice" else "card_linking"
        out.append(
            row(
                f"{task}-{k}",
                gold=gold,
                j1=gold,
                j2=gold if k % 2 else other,
                task=task,
                group=group,
            )
        )
    return out


@pytest.fixture
def files(tmp_path, monkeypatch):
    """A tiny pool, senior answers and saved probabilities, with evaluate's paths pointed at them."""
    rows = split_rows()
    results = tmp_path / "results"
    results.mkdir()
    pool = tmp_path / "pool.jsonl"
    pool.write_text("".join(json.dumps(r) + "\n" for r in rows))
    for tag, cost in (("opus", 0.0025), ("haiku", 0.00005)):
        (results / f"eval_senior_{tag}.jsonl").write_text(
            "".join(
                json.dumps({"question_id": r["question_id"], **senior(r["gold"], cost=cost)}) + "\n"
                for r in rows
            )
        )
    probs = results / "eval_policy_probs_saved.jsonl"
    ev.save_probabilities(
        probs, {r["question_id"]: 0.9 if r["agreed"] else 0.2 for r in rows}, "run-test", 0.5
    )
    monkeypatch.setattr(ev, "POOL", pool)
    monkeypatch.setattr(ev, "RESULTS", results)
    return {"rows": rows, "results": results, "probs": probs}


def test_saved_probabilities_round_trip(files):
    p, name, thr = ev.load_probabilities(files["probs"])
    assert len(p) == len(files["rows"]) and name == "run-test" and thr == 0.5


def test_loading_an_empty_probability_file_stops(tmp_path):
    (tmp_path / "empty.jsonl").write_text("")
    with pytest.raises(SystemExit):
        ev.load_probabilities(tmp_path / "empty.jsonl")


def test_report_from_saved_probabilities_writes_the_summary_and_the_curve(files):
    out = ev.report("http://unused", files["probs"])
    text = out.read_text()
    assert "probabilities from the saved file eval_policy_probs_saved.jsonl" in text
    assert "**8 questions** (4 MMLU choice, 4 Banking77 label)" in text
    table = [line for line in text.splitlines() if line.startswith("| ")]
    assert len(table) == 1 + 6 + 1  # header, six strategies, random routing
    assert "Router, policy v2 (champion, P >= 0.50)" in text
    curve = list(csv.reader((files["results"] / text.split("`")[1]).open()))
    assert len(curve) == 1 + len(ev.THRESHOLDS) + len(ev.CURVE_BASELINES)


def test_report_from_mlserver_saves_the_probabilities_it_used(files, monkeypatch):
    p = {r["question_id"]: 0.7 for r in files["rows"]}

    async def fake_probabilities(rows, url):
        assert url == "http://policy:8090"
        return p, "run-live (policy-features-v2, threshold 0.600)", 0.6

    monkeypatch.setattr(ev, "policy_probabilities", fake_probabilities)
    out = ev.report("http://policy:8090")
    assert "probabilities from the running MLServer" in out.read_text()
    saved = sorted(files["results"].glob("eval_policy_probs_2*.jsonl"))
    assert len(saved) == 1 and ev.load_probabilities(saved[0])[0] == p


def test_report_refuses_to_run_without_every_senior_answer(files):
    (files["results"] / "eval_senior_haiku.jsonl").write_text("")
    with pytest.raises(SystemExit, match="lack senior answers"):
        ev.report("http://unused", files["probs"])


def test_policy_probabilities_ask_the_routers_own_policy_client(monkeypatch):
    import router.policy as rp

    class FakeMeta:
        model_version, feature_version, threshold_balanced = "run-test", "policy-features-v2", 0.5

        def vector(self, *features):
            return [float(len(features))]

    class FakeClient:
        def __init__(self, client, url, meta, timeout_s):
            assert url == "http://x" and timeout_s == 2.0

        async def p_correct(self, x):
            assert x == [7.0]  # all seven features were passed
            return 0.25

    monkeypatch.setattr(rp.PolicyMeta, "load", classmethod(lambda cls, path: FakeMeta()))
    monkeypatch.setattr(rp, "PolicyClient", FakeClient)
    p, name, thr = asyncio.run(ev.policy_probabilities([row("a"), row("b")], "http://x"))
    assert p == {"a": 0.25, "b": 0.25} and thr == 0.5
    assert name == "run-test (policy-features-v2, threshold 0.500)"


# ------------------------------------------------------------ senior runner


@pytest.fixture
def senior_files(files, tmp_path, monkeypatch):
    import router.adapters as ra

    questions = tmp_path / "questions.jsonl"
    lines = []
    for k, r in enumerate(files["rows"]):
        q = {"question_id": r["question_id"], "task_type": r["task_type"]}
        q["question"] = "FAIL please" if k == 0 else f"Question {k}?"
        if r["task_type"] == "choice":
            q["options"] = ["one", "two", "three", "four"]
        lines.append(json.dumps(q) + "\n")
    questions.write_text("".join(lines))

    class FakeSenior:
        def __init__(self, client, url, model, key, **kwargs):
            assert key == "sk-ant-test" and model == "claude-haiku-5-5"

        async def ask(self, system, user, max_tokens):
            if "FAIL" in user:
                raise ra.ModelUnavailable("senior_timeout")
            return ra.ModelReply("The answer is B", 100, 10, 12.34, "end_turn")

    monkeypatch.setattr(ev, "QUESTIONS", questions)
    monkeypatch.setattr(ev, "ROOT", tmp_path)  # the runner prints its output path relative to ROOT
    monkeypatch.setattr(ev, "api_key", lambda: "sk-ant-test")
    monkeypatch.setattr(ra, "AnthropicSenior", FakeSenior)
    return files


def test_seniors_records_answers_costs_failures_and_resumes(senior_files):
    out = senior_files["results"] / "eval_senior_fake.jsonl"
    asyncio.run(ev.seniors("claude-haiku-5-5", "fake", "low", None, max_usd=1.0))
    recs = ev.read_jsonl(out)
    assert len(recs) == len(senior_files["rows"])
    failed = [r for r in recs if not r["ok"]]
    assert len(failed) == 1 and failed[0]["error"] == "senior_timeout"
    ok = [r for r in recs if r["ok"]]
    assert all(r["cost_usd"] == pytest.approx((100 * 0.10 + 10 * 0.50) / 1e6) for r in ok)
    assert {r["answer"] for r in ok if r["question_id"].startswith("choice")} == {"B"}
    assert all(r["latency_ms"] == 12.3 and r["stop_reason"] == "end_turn" for r in ok)
    # a second run asks only the question that failed
    asyncio.run(ev.seniors("claude-haiku-5-5", "fake", "low", None, max_usd=1.0))
    assert len(ev.read_jsonl(out)) == len(senior_files["rows"]) + 1


def test_seniors_stop_at_the_spend_limit(senior_files):
    asyncio.run(ev.seniors("claude-haiku-5-5", "capped", None, "disabled", max_usd=0.0))
    recs = ev.read_jsonl(senior_files["results"] / "eval_senior_capped.jsonl")
    assert recs and all(r["error"] == "spend_limit" for r in recs)


def test_api_key_comes_from_the_environment_or_env_file_and_must_be_anthropic(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    monkeypatch.delenv("SENIOR_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        ev.api_key()
    (tmp_path / ".env").write_text("OTHER=1\nSENIOR_API_KEY=sk-ant-test\n")
    assert ev.api_key() == "sk-ant-test"
    monkeypatch.setenv("SENIOR_API_KEY", "not-an-anthropic-key")
    with pytest.raises(SystemExit):
        ev.api_key()


def test_main_dispatches_both_commands(monkeypatch):
    seen = {}
    monkeypatch.setattr(ev, "report", lambda url, probs: seen.update(report=(url, probs)))

    async def fake_seniors(model, tag, effort, thinking, max_usd):
        seen["seniors"] = (model, tag, effort, thinking, max_usd)

    monkeypatch.setattr(ev, "seniors", fake_seniors)
    monkeypatch.setattr("sys.argv", ["evaluate", "report", "--policy-probs", "results/x.jsonl"])
    ev.main()
    argv = ["evaluate", "seniors", "--model", "claude-haiku-5-5", "--tag", "t", "--effort", "none"]
    monkeypatch.setattr("sys.argv", [*argv, "--thinking", "disabled"])
    ev.main()
    assert seen["report"] == ("http://localhost:8090", Path("results/x.jsonl"))
    assert seen["seniors"] == ("claude-haiku-5-5", "t", None, "disabled", 3.0)


# ---------------------------------------------------------------- regression

COMMITTED = "20261112-1728"  # the run the report quotes


def test_committed_results_reproduce_from_the_committed_data():
    """Every number in results/eval_summary_<COMMITTED>.md that needs no policy server is
    recomputed from data/replay and results/eval_senior_*.jsonl and must match to the character."""
    rows = ev.test_rows()
    opus = {r["question_id"]: r for r in ev.read_jsonl(ev.senior_file("opus"))}
    haiku = {r["question_id"]: r for r in ev.read_jsonl(ev.senior_file("haiku"))}
    summary = (ev.RESULTS / f"eval_summary_{COMMITTED}.md").read_text(encoding="utf-8")
    committed = summary.splitlines()
    stats = {k: ev.summarize(v, rows) for k, v in ev.baseline_strategies(rows, opus, haiku).items()}
    for name, s in stats.items():
        assert ev.strategy_row(name, s) in committed, name
    esc = stats[ev.RULE]["esc"]
    assert ev.random_row(esc, *ev.random_routing(rows, opus, esc)) in committed
    wrong, cost = ev.senior_facts(rows, opus)
    assert ev.facts_text(wrong, len(rows), cost) in committed
    assert ev.TABLE_HEADER[0] in committed
    with (ev.RESULTS / f"eval_curve_{COMMITTED}.csv").open(encoding="utf-8") as f:
        curve = list(csv.reader(f))
    for name in ev.CURVE_BASELINES:
        s = stats[name]
        expected = [name, "", str(round(s["esc"], 4)), str(round(s["acc"], 4))]
        assert [*expected, str(round(s["cost_k"], 3))] in curve, name
