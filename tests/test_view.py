"""The session both interactive viewers drive."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from kilix_graphs.view import Session, Settings


def temp_file(text: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "g.kg"
    path.write_text(text, encoding="utf-8")
    return path


class SettingsTests(unittest.TestCase):
    def test_cycling_wraps_in_both_directions(self) -> None:
        settings = Settings()
        forward = settings
        for _ in range(5):
            forward = forward.cycle("engine")
        self.assertEqual(forward.engine, settings.engine)
        self.assertEqual(settings.cycle("engine", -1).engine, "grid")

    def test_direction_and_ranker_only_reach_the_engines_that_use_them(self) -> None:
        self.assertIn("direction", Settings(engine="layered").layout_options())
        self.assertIn("ranker", Settings(engine="layered").layout_options())
        self.assertNotIn("ranker", Settings(engine="tree").layout_options())
        self.assertEqual(Settings(engine="force").layout_options(), {})

    def test_settings_are_frozen_so_a_viewer_cannot_mutate_shared_state(self) -> None:
        with self.assertRaises(Exception):
            Settings().engine = "force"  # type: ignore[misc]


class CacheTests(unittest.TestCase):
    """Recomputing only what changed is the whole reason this class exists."""

    def setUp(self) -> None:
        self.session = Session(source="digraph\na -> b -> c\nb -> d\n")

    def test_an_unchanged_session_returns_the_same_scene_object(self) -> None:
        first = self.session.scene()
        self.assertIs(self.session.scene(), first)

    def test_changing_the_theme_recomposes_without_laying_out_again(self) -> None:
        self.session.scene()
        graph = self.session.graph()
        self.session.cycle("theme")
        self.assertIsNot(self.session.scene(), None)
        # The positions survived: only the composition was redone.
        self.assertIs(self.session.graph(), graph)

    def test_changing_the_engine_lays_out_again(self) -> None:
        self.session.scene()
        graph = self.session.graph()
        self.session.cycle("engine")
        self.assertIsNot(self.session.graph(), graph)

    def test_an_unknown_setting_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.session.update(enigne="force")


class ReloadTests(unittest.TestCase):
    def test_a_second_save_in_the_same_clock_tick_is_still_seen(self) -> None:
        """Back-to-back writes can share an st_mtime_ns exactly -- measured as
        a delta of 0 on this filesystem -- so a stamp comparison alone misses
        the second save, and a viewer that ignores your save looks broken."""
        path = temp_file("a -> b\n")
        session = Session(path=path)
        session.graph()
        path.write_text("c -> d\n")
        self.assertTrue(session.reload())
        path.write_text("e -> f\n")
        self.assertTrue(session.reload())
        self.assertEqual(sorted(session.graph().nodes), ["e", "f"])

    def test_reloading_invalidates_the_scene_and_not_only_the_graph(self) -> None:
        """A viewer that asks for `scene()` -- which the GUI does -- must not
        get the previous file back after a reload that reported success."""
        path = temp_file("a -> b\nb -> c\nc -> d\n")
        session = Session(path=path)
        session.scene()
        self.assertEqual(session.stats.nodes, 4)
        path.write_text("x -> y\n")
        self.assertTrue(session.reload())
        session.scene()
        self.assertEqual(session.stats.nodes, 2)

    def test_an_unchanged_file_reports_no_change(self) -> None:
        path = temp_file("a -> b\n")
        session = Session(path=path)
        session.graph()
        time.sleep(0.01)
        self.assertFalse(session.reload())

    def test_a_half_written_file_is_held_as_an_error_not_raised(self) -> None:
        path = temp_file("a -> b\n")
        session = Session(path=path)
        session.scene()
        path.write_text("a -> \n")
        session.reload()
        self.assertIsNone(session.scene())
        self.assertTrue(session.error)

    def test_it_recovers_once_the_file_parses_again(self) -> None:
        path = temp_file("a -> \n")
        session = Session(path=path)
        self.assertIsNone(session.scene())
        path.write_text("a -> b\n")
        session.reload()
        self.assertIsNotNone(session.scene())
        self.assertIsNone(session.error)

    def test_a_vanished_file_does_not_raise(self) -> None:
        path = temp_file("a -> b\n")
        session = Session(path=path)
        session.graph()
        path.unlink()
        self.assertFalse(session.reload())

    def test_a_source_only_session_has_nothing_to_reload(self) -> None:
        session = Session(source="a -> b\n")
        self.assertFalse(session.reload())
        self.assertFalse(session.reload(force=True))


class RenderTests(unittest.TestCase):
    def test_text_never_raises_even_with_nothing_to_draw(self) -> None:
        session = Session(source="# only a comment\n")
        self.assertIn("no nodes", session.text())

    def test_svg_and_stats_come_from_the_same_scene(self) -> None:
        session = Session(source="a -> b\n")
        document = session.svg()
        self.assertIn("<svg", document)
        self.assertIn(f'width="{session.stats.width:g}"', document)

    def test_the_summary_names_the_settings(self) -> None:
        session = Session(source="a -> b\n")
        session.scene()
        self.assertIn("layered", session.summary())
        session.cycle("theme")
        self.assertIn("light", session.summary())

    def test_a_session_needs_something_to_show(self) -> None:
        with self.assertRaises(ValueError):
            Session()


if __name__ == "__main__":
    unittest.main()
