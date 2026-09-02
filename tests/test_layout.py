"""Layout engines, asserted on properties rather than on coordinates."""

from __future__ import annotations

import unittest

import importlib

from kilix_graphs import layout, parse
from kilix_graphs.model import Graph

# `kilix_graphs.layout.layered` names the engine function, not the module it
# lives in -- the package re-exports it. Reach the module explicitly.
layered_module = importlib.import_module("kilix_graphs.layout.layered")


def ranks(graph: Graph) -> dict[str, float]:
    return {name: node.y for name, node in graph.nodes.items()}


class LayeredTests(unittest.TestCase):
    def test_a_chain_ranks_in_order(self) -> None:
        graph = parse.loads("a -> b -> c -> d")
        layout.run(graph, "layered")
        y = ranks(graph)
        self.assertLess(y["a"], y["b"])
        self.assertLess(y["b"], y["c"])
        self.assertLess(y["c"], y["d"])

    def test_a_cycle_is_broken_and_marked(self) -> None:
        graph = parse.loads("a -> b -> c -> a")
        layout.run(graph, "layered")
        self.assertEqual(sum(1 for e in graph.edges if e.reversed), 1)
        # The drawing still has three ranks: breaking the cycle must not
        # collapse it into one.
        self.assertEqual(len({round(y, 3) for y in ranks(graph).values()}), 3)

    def test_a_long_edge_is_routed_through_its_ranks(self) -> None:
        graph = parse.loads("a -> b -> c -> d\na -> d")
        layout.run(graph, "layered")
        long_edge = next(e for e in graph.edges if (e.tail, e.head) == ("a", "d"))
        # Two intermediate ranks are crossed, so the route has interior points
        # and is not a straight line through b and c.
        self.assertGreaterEqual(len(long_edge.points), 4)

    def test_a_long_edge_comes_out_straight(self) -> None:
        """Brandes-Kopf's whole purpose: keep long edges vertical."""
        graph = parse.loads("a -> b -> c -> d -> e\na -> e")
        layout.run(graph, "layered")
        long_edge = next(e for e in graph.edges if (e.tail, e.head) == ("a", "e"))
        interior = [x for x, _ in long_edge.points[1:-1]]
        self.assertGreater(len(interior), 1)
        self.assertLess(max(interior) - min(interior), 1.0)

    def test_direction_exchanges_the_axes(self) -> None:
        source = "a -> b -> c"
        down = parse.loads(source)
        layout.run(down, "layered", direction="TB")
        across = parse.loads(source)
        layout.run(across, "layered", direction="LR")
        self.assertLess(down.nodes["a"].y, down.nodes["c"].y)
        self.assertLess(across.nodes["a"].x, across.nodes["c"].x)
        self.assertAlmostEqual(across.nodes["a"].y, across.nodes["c"].y, delta=1.0)

    def test_reversed_direction_flips_the_drawing(self) -> None:
        graph = parse.loads("a -> b -> c")
        layout.run(graph, "layered", direction="BT")
        self.assertGreater(graph.nodes["a"].y, graph.nodes["c"].y)

    def test_self_loops_get_a_lobe_and_do_not_add_a_rank(self) -> None:
        graph = parse.loads("a -> a\na -> b")
        layout.run(graph, "layered")
        loop = next(e for e in graph.edges if e.tail == e.head)
        self.assertGreaterEqual(len(loop.points), 4)
        self.assertEqual(len({round(y, 3) for y in ranks(graph).values()}), 2)

    def test_disconnected_pieces_are_all_placed(self) -> None:
        graph = parse.loads("a -> b\nc -> d")
        layout.run(graph, "layered")
        self.assertEqual(len({(n.x, n.y) for n in graph.nodes.values()}), 4)

    def test_clusters_enclose_their_members(self) -> None:
        graph = parse.loads("group g: G\n    a -> b\nc\n")
        layout.run(graph, "layered")
        box = graph.clusters["g"]
        for name in ("a", "b"):
            node = graph.nodes[name]
            self.assertGreaterEqual(node.x - node.w / 2, box.x)
            self.assertLessEqual(node.x + node.w / 2, box.x + box.w)
            self.assertGreaterEqual(node.y - node.h / 2, box.y)
            self.assertLessEqual(node.y + node.h / 2, box.y + box.h)

    def test_nodes_on_a_rank_do_not_overlap(self) -> None:
        graph = parse.loads("r -> a\nr -> b\nr -> c\nr -> d\nr -> e")
        layout.run(graph, "layered")
        children = sorted(
            (graph.nodes[name] for name in "abcde"), key=lambda n: n.x
        )
        for left, right in zip(children, children[1:]):
            self.assertGreaterEqual(
                right.x - right.w / 2, left.x + left.w / 2 - 0.001
            )

    def test_an_empty_graph_is_not_an_error(self) -> None:
        graph = Graph()
        layout.run(graph, "layered")
        self.assertEqual(len(graph.nodes), 0)


class RankingTests(unittest.TestCase):
    def test_slack_reduction_never_lengthens_the_drawing(self) -> None:
        """The ranking objective must not go up; that is what makes it stop."""
        graph = parse.loads("a -> c\nb -> c\nc -> d\na -> d")
        work = layered_module._W()
        for name in graph.nodes:
            work.nodes[name] = layered_module._N(id=name, w=10.0, h=10.0)
        for edge in graph.edges:
            work.edges.append((edge.tail, edge.head, 1.0, 1))
        work.index()
        layered_module._longest_path(work)
        before = layered_module.total_edge_length(work)
        layered_module._reduce_slack(work, 8)
        after = layered_module.total_edge_length(work)
        self.assertLessEqual(after, before)
        for tail, head, _, minlen in work.edges:
            self.assertGreaterEqual(
                work.nodes[head].rank - work.nodes[tail].rank, minlen
            )


class CrossCountTests(unittest.TestCase):
    def _work(self, edges: list[tuple[str, str]]) -> layered_module._W:
        work = layered_module._W()
        for name in {n for pair in edges for n in pair}:
            work.nodes[name] = layered_module._N(id=name, w=10.0, h=10.0)
        for tail, head in edges:
            work.edges.append((tail, head, 1.0, 1))
        work.index()
        return work

    def test_a_planar_pair_of_layers_has_no_crossings(self) -> None:
        work = self._work([("a", "c"), ("b", "d")])
        self.assertEqual(layered_module.cross_count([["a", "b"], ["c", "d"]], work), 0)

    def test_one_crossing_is_counted_once(self) -> None:
        work = self._work([("a", "d"), ("b", "c")])
        self.assertEqual(layered_module.cross_count([["a", "b"], ["c", "d"]], work), 1)

    def test_the_count_matches_the_quadratic_definition(self) -> None:
        """The accumulator tree is only worth having if it agrees with the
        obvious O(E^2) count it replaces."""
        edges = [("a", "f"), ("a", "e"), ("b", "d"), ("c", "e"), ("c", "f"), ("b", "f")]
        work = self._work(edges)
        north, south = ["a", "b", "c"], ["d", "e", "f"]
        position = {name: i for i, name in enumerate(north + south)}
        pairs = [(position[t], position[h]) for t, h in edges]
        naive = sum(
            1
            for i, (t1, h1) in enumerate(pairs)
            for t2, h2 in pairs[i + 1 :]
            if (t1 - t2) * (h1 - h2) < 0
        )
        self.assertEqual(layered_module.cross_count([north, south], work), naive)


class OtherEnginesTests(unittest.TestCase):
    def test_force_is_deterministic(self) -> None:
        source = "a -- b\nb -- c\nc -- a\nc -- d"
        first = parse.loads(source)
        second = parse.loads(source)
        layout.run(first, "force", ticks=60)
        layout.run(second, "force", ticks=60)
        for name in first.nodes:
            self.assertAlmostEqual(first.nodes[name].x, second.nodes[name].x, places=6)
            self.assertAlmostEqual(first.nodes[name].y, second.nodes[name].y, places=6)

    def test_force_separates_coincident_starts(self) -> None:
        graph = parse.loads("a -- b\nc -- d")
        layout.run(graph, "force", ticks=80)
        positions = [(round(n.x, 3), round(n.y, 3)) for n in graph.nodes.values()]
        self.assertEqual(len(set(positions)), len(positions))

    def test_tree_puts_children_below_their_parent(self) -> None:
        graph = parse.loads("root -> a\nroot -> b\na -> c")
        layout.run(graph, "tree")
        self.assertLess(graph.nodes["root"].y, graph.nodes["a"].y)
        self.assertLess(graph.nodes["a"].y, graph.nodes["c"].y)

    def test_circular_puts_every_node_on_one_ring(self) -> None:
        graph = parse.loads("a -- b\nb -- c\nc -- d\nd -- a")
        layout.run(graph, "circular")
        min_x, min_y, max_x, max_y = graph.bounds()
        centre = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        radii = [
            ((n.x - centre[0]) ** 2 + (n.y - centre[1]) ** 2) ** 0.5
            for n in graph.nodes.values()
        ]
        self.assertLess(max(radii) - min(radii), 1.0)

    def test_grid_fills_rows(self) -> None:
        graph = parse.loads("a\nb\nc\nd\ne\nf")
        layout.run(graph, "grid", columns=3)
        rows = {round(n.y, 3) for n in graph.nodes.values()}
        self.assertEqual(len(rows), 2)

    def test_an_unknown_engine_and_option_are_both_rejected(self) -> None:
        with self.assertRaises(ValueError):
            layout.run(parse.loads("a -> b"), "spiral")
        with self.assertRaises(ValueError) as caught:
            layout.run(parse.loads("a -> b"), "layered", ranksepp=10)
        self.assertIn("ranksepp", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
