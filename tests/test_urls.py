import unittest

from crawler.urls import url_key


class UrlKeyTest(unittest.TestCase):
    def test_url_key_ignores_fragment_and_scheme_and_host_case(self) -> None:
        first = url_key("HTTPS://Example.COM/a#top")
        second = url_key("https://example.com/a#end")

        self.assertEqual(first, "https://example.com/a")
        self.assertEqual(first, second)

    def test_url_key_keeps_path_and_query(self) -> None:
        self.assertEqual(
            url_key("https://EXAMPLE.COM/Docs%2FIntro?p=1&sort=new#top"),
            "https://example.com/Docs%2FIntro?p=1&sort=new",
        )
        self.assertNotEqual(url_key("/a?p=1"), url_key("/a?p=2"))
        self.assertNotEqual(url_key("/Docs"), url_key("/docs"))
        self.assertEqual(url_key("/a?#top"), "/a?")

    def test_url_key_keeps_user_info_and_port(self) -> None:
        self.assertEqual(
            url_key("https://User:Pass@EXAMPLE.COM:08443/Docs#top"),
            "https://User:Pass@example.com:08443/Docs",
        )

    def test_url_key_keeps_ipv6_zone_and_port(self) -> None:
        self.assertEqual(
            url_key("http://[FE80::1%25Eth0]:8080/a#top"),
            "http://[fe80::1%25Eth0]:8080/a",
        )
