"""The three input formats."""

from __future__ import annotations

import json
import unittest

from kilix_graphs import parse
from kilix_graphs.model import Shape
from kilix_graphs.parse import dot, jgf, kg


class DotTests(unittest.TestCase):
    def test_chains_and_attributes(self) -> None:
        graph = dot.loads('digraph G { a -> b -> c [label="x"]; b [shape=box]; }')
        self.assertTrue(graph.directed)
        self.assertEqual([(e.tail, e.head) for e in graph.edges], [("a", "b"), ("b", "c")])
        self.assertEqual([e.label for e in graph.edges], ["x", "x"])
        self.assertEqual(graph.nodes["b"].shape, Shape.BOX)

    def test_defaults_apply_to_later_statements_only(self) -> None:
        graph = dot.loads("digraph { a; node [shape=box]; b; }")
        self.assertEqual(graph.nodes["a"].shape, Shape.ROUND)
        self.assertEqual(graph.nodes["b"].shape, Shape.BOX)

    def test_an_edge_does_not_reset_an_already_declared_node(self) -> None:
        graph = dot.loads(
            "digraph { node [shape=round]; start [shape=point]; start -> idle }"
        )
        self.assertEqual(graph.nodes["start"].shape, Shape.POINT)
        self.assertEqual(graph.nodes["idle"].shape, Shape.ROUND)

    def test_undirected_edges_carry_no_direction(self) -> None:
        graph = dot.loads("graph { a -- b }")
        self.assertFalse(graph.directed)
        self.assertEqual(graph.edges[0].attrs.get("dir"), "none")

    def test_clusters_claim_their_members(self) -> None:
        graph = dot.loads("digraph { subgraph cluster_x { a; b } c }")
        self.assertIn("cluster_x", graph.clusters)
        self.assertEqual(graph.nodes["a"].cluster, "cluster_x")
        self.assertIsNone(graph.nodes["c"].cluster)

    def test_comments_and_quoted_ids(self) -> None:
        graph = dot.loads(
            '''digraph {
            // a line comment
            # a hash comment
            /* a block
               comment */
            "a b" -> "c\\"d";
            }'''
        )
        self.assertEqual([(e.tail, e.head) for e in graph.edges], [("a b", 'c"d')])

    def test_ports_are_accepted_and_discarded(self) -> None:
        graph = dot.loads("digraph { a:north -> b:s:w }")
        self.assertEqual([(e.tail, e.head) for e in graph.edges], [("a", "b")])

    def test_syntax_errors_carry_a_position(self) -> None:
        with self.assertRaises(dot.DotSyntaxError) as caught:
            dot.loads("digraph { a -> }")
        self.assertIn("line 1", str(caught.exception))

    def test_unsupported_constructs_raise_rather_than_mislead(self) -> None:
        with self.assertRaises(dot.DotSyntaxError):
            dot.loads("digraph { a [label=<b>bold</b>] }")
        with self.assertRaises(dot.DotSyntaxError):
            dot.loads("digraph { {a b} -> c }")


class KgTests(unittest.TestCase):
    def test_edges_labels_and_chains(self) -> None:
        graph = kg.loads("a -> b -> c\nb -> d: spill")
        self.assertEqual(
            [(e.tail, e.head, e.label) for e in graph.edges],
            [("a", "b", ""), ("b", "c", ""), ("b", "d", "spill")],
        )

    def test_node_label_and_attributes(self) -> None:
        graph = kg.loads("disk: Cold storage\ndisk {shape=cylinder}")
        self.assertEqual(graph.nodes["disk"].label, "Cold storage")
        self.assertEqual(graph.nodes["disk"].shape, Shape.ROUND)

    def test_groups_nest_by_indentation(self) -> None:
        graph = kg.loads(
            "group outer: Outer\n"
            "    a\n"
            "    group inner: Inner\n"
            "        b\n"
            "c\n"
        )
        self.assertEqual(graph.nodes["a"].cluster, "outer")
        self.assertEqual(graph.nodes["b"].cluster, "inner")
        self.assertIsNone(graph.nodes["c"].cluster)
        self.assertEqual(graph.clusters["inner"].parent, "outer")

    def test_comments_and_quoted_identifiers(self) -> None:
        graph = kg.loads('# a comment\n"a:b" -> c  # trailing\n')
        self.assertEqual([(e.tail, e.head) for e in graph.edges], [("a:b", "c")])

    def test_undirected_header(self) -> None:
        graph = kg.loads("graph Ring\na -- b")
        self.assertFalse(graph.directed)
        self.assertEqual(graph.name, "Ring")


class JgfTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        original = kg.loads("digraph G\na -> b: x\nb: Bee")
        restored = jgf.loads(jgf.dumps(original))
        self.assertEqual(sorted(restored.nodes), ["a", "b"])
        self.assertEqual(restored.nodes["b"].label, "Bee")
        self.assertEqual([(e.tail, e.head, e.label) for e in restored.edges], [("a", "b", "x")])

    def test_positions_are_optional_and_present_when_asked(self) -> None:
        graph = kg.loads("a -> b")
        graph.measure()
        plain = json.loads(jgf.dumps(graph))
        with_positions = json.loads(jgf.dumps(graph, positions=True))
        self.assertNotIn("x", plain["graph"]["nodes"]["a"]["metadata"])
        self.assertIn("x", with_positions["graph"]["nodes"]["a"]["metadata"])

    def test_a_bare_nodes_edges_object_is_accepted(self) -> None:
        graph = jgf.loads('{"nodes": {"a": {}, "b": {}}, "edges": [{"source":"a","target":"b"}]}')
        self.assertEqual(len(graph.edges), 1)

    def test_bad_json_says_so(self) -> None:
        with self.assertRaises(ValueError):
            jgf.loads("{not json")


class DetectTests(unittest.TestCase):
    def test_detection_by_content(self) -> None:
        self.assertEqual(parse.detect("digraph G { a -> b }"), "dot")
        self.assertEqual(parse.detect('{"graph": {}}'), "jgf")
        self.assertEqual(parse.detect("a -> b"), "kg")

    def test_extension_wins_over_content(self) -> None:
        self.assertEqual(parse.detect("a -> b", "x.dot"), "dot")


if __name__ == "__main__":
    unittest.main()
