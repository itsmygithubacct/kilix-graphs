"""The model and its measurements."""

from __future__ import annotations

import unittest

from kilix_graphs.model import Graph, Node, Shape, measure_label


class MeasureTests(unittest.TestCase):
    def test_a_label_sets_the_size(self) -> None:
        narrow = measure_label("ab")
        wide = measure_label("abcdefgh")
        self.assertGreater(wide.w, narrow.w)
        self.assertEqual(wide.h, narrow.h)

    def test_multiline_grows_downward_and_takes_the_widest_line(self) -> None:
        one = measure_label("aaa")
        two = measure_label("aaa\nbbbbbb")
        self.assertGreater(two.h, one.h)
        self.assertEqual(two.w, measure_label("bbbbbb").w)

    def test_an_empty_label_still_has_a_box(self) -> None:
        size = measure_label("")
        self.assertGreater(size.w, 0)
        self.assertGreater(size.h, 0)

    def test_a_circle_is_square(self) -> None:
        node = Node(id="n", label="a much longer label", shape=Shape.CIRCLE)
        node.measure()
        self.assertEqual(node.w, node.h)


class GraphTests(unittest.TestCase):
    def test_edges_create_their_endpoints(self) -> None:
        graph = Graph()
        graph.edge("a", "b")
        self.assertEqual(sorted(graph.nodes), ["a", "b"])

    def test_parallel_edges_are_kept(self) -> None:
        graph = Graph()
        graph.edge("a", "b")
        graph.edge("a", "b")
        self.assertEqual(len(graph.edges), 2)

    def test_node_is_idempotent_and_updates(self) -> None:
        graph = Graph()
        graph.node("a", label="first")
        graph.node("a", label="second")
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.nodes["a"].label, "second")

    def test_normalise_moves_the_drawing_to_the_margin(self) -> None:
        graph = Graph()
        graph.edge("a", "b")
        graph.measure()
        graph.nodes["a"].x, graph.nodes["a"].y = -500.0, -500.0
        graph.nodes["b"].x, graph.nodes["b"].y = -400.0, -300.0
        graph.normalise(margin=10.0)
        min_x, min_y, _, _ = graph.bounds()
        self.assertAlmostEqual(min_x, 10.0)
        self.assertAlmostEqual(min_y, 10.0)

    def test_copy_is_deep_enough_to_lay_out_twice(self) -> None:
        graph = Graph()
        graph.edge("a", "b", label="x")
        clone = graph.copy()
        clone.nodes["a"].x = 99.0
        clone.edges[0].points = [(1.0, 2.0)]
        self.assertEqual(graph.nodes["a"].x, 0.0)
        self.assertEqual(graph.edges[0].points, [])

    def test_bounds_of_an_empty_graph_is_a_point(self) -> None:
        self.assertEqual(Graph().bounds(), (0.0, 0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
