import unittest

from crawler import RobotsParser


class RobotsParserTest(unittest.TestCase):
    def test_agent_group_and_longest_path_rule_determine_access(self) -> None:
        parser = RobotsParser(
            """
            User-agent: ScoutBot
            Disallow: /private/
            Allow: /private/public/ # The longer rule wins
            User-agent: IndexBot
            Disallow: /private/
            User-agent: *
            Disallow: /fallback/
            """
        )
        public = "https://example.com/private/public/docs"

        self.assertTrue(parser.can_fetch(public, "scoutbot/1.0"))
        self.assertFalse(parser.can_fetch(public, "IndexBot/2.0"))
        self.assertFalse(parser.can_fetch("https://example.com/private/notes", "ScoutBot"))
        self.assertFalse(parser.can_fetch("https://example.com/fallback/", "OtherBot"))
        self.assertTrue(parser.can_fetch("https://example.com/fallback/", "ScoutBot"))

    def test_matching_groups_are_combined_and_allow_wins_an_equal_match(self) -> None:
        parser = RobotsParser(
            """
            User-agent: ScoutBot
            User-agent: IndexBot
            Disallow: /shared
            Disallow: /equal

            User-agent: scoutbot
            Allow: /equal
            Disallow: /extra
            """
        )

        self.assertFalse(parser.can_fetch("/shared", "ScoutBot"))
        self.assertFalse(parser.can_fetch("/shared", "IndexBot"))
        self.assertTrue(parser.can_fetch("/equal", "ScoutBot"))
        self.assertFalse(parser.can_fetch("/equal", "IndexBot"))
        self.assertFalse(parser.can_fetch("/extra", "ScoutBot"))
        self.assertTrue(parser.can_fetch("/extra", "IndexBot"))

    def test_wildcards_path_end_query_and_encoding(self) -> None:
        parser = RobotsParser(
            """
            User-agent: *
            Disallow: /*.zip$
            Disallow: /search?private=*
            Disallow: /каталог
            Disallow: /data/%62
            Disallow: /a%2Fb
            """
        )

        self.assertFalse(parser.can_fetch("/docs/archive.zip"))
        self.assertTrue(parser.can_fetch("/docs/archive.zip?preview=1"))
        self.assertFalse(parser.can_fetch("/search?private=1"))
        self.assertTrue(parser.can_fetch("/searchXprivate=1"))
        self.assertFalse(parser.can_fetch("/%d0%ba%d0%b0%d1%82%d0%b0%d0%bb%d0%be%d0%b3"))
        self.assertFalse(parser.can_fetch("/data/b"))
        self.assertFalse(parser.can_fetch("/a%2fb"))
        self.assertTrue(parser.can_fetch("/a/b"))

    def test_crawl_delay_uses_the_matched_group_and_ignores_invalid_values(self) -> None:
        parser = RobotsParser(
            """
            User-agent: ScoutBot
            Crawl-delay: 1.5
            Crawl-delay: -1
            Crawl-delay: nan
            User-agent: ScoutBot
            Crawl-delay: 2.5
            User-agent: IndexBot
            Crawl-delay: invalid
            User-agent: *
            Crawl-delay: 5
            """
        )

        self.assertEqual(parser.get_crawl_delay("ScoutBot/1.0"), 2.5)
        self.assertEqual(parser.get_crawl_delay("IndexBot"), 0.0)
        self.assertEqual(parser.get_crawl_delay("OtherBot"), 5.0)
        self.assertEqual(RobotsParser("").get_crawl_delay(), 0.0)
