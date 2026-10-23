"""Large DAG validation must not depend on Python's recursion limit."""
import time
import unittest

from holding.autonomy import Mission, StandingGrant, choose_next

REPO = "r9327/Dof-Atlas"


def graph(length: int, *, cycle: bool = False):
    jobs = []
    for i in range(length):
        depends = (f"task-{i-1}",) if i else ()
        if i == 0 and cycle:
            depends = (f"task-{length-1}",)
        jobs.append(Mission(f"task-{i}", REPO, dependencies=depends))
    return jobs


class LargeDagTests(unittest.TestCase):
    def test_long_acyclic_dependency_chain_does_not_recurse(self):
        decision = choose_next(
            graph(2500),
            (StandingGrant(REPO, frozenset({"code"}), int(time.time()) + 3600),),
            now_epoch=int(time.time()), online=False)
        self.assertEqual((decision.action, decision.mission_id), ("RUN", "task-0"))

    def test_long_cycle_is_reported_as_blocked_not_stack_overflow(self):
        decision = choose_next(graph(2500, cycle=True), (), now_epoch=100, online=False)
        self.assertEqual((decision.action, decision.reason), ("BLOCKED", "Dependency cycle"))

    def test_shared_ancestors_are_not_false_cycles(self):
        nodes = [Mission("root", REPO)]
        nodes += [Mission(f"leaf-{i}", REPO, dependencies=("root",)) for i in range(1200)]
        choice = choose_next(
            nodes, (StandingGrant(REPO, frozenset({"code"}), 200),),
            now_epoch=100, online=False)
        self.assertEqual((choice.action, choice.mission_id), ("RUN", "root"))


if __name__ == "__main__":
    unittest.main()
