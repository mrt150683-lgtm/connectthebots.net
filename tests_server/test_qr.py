"""The self-hosted QR encoder and the share options built on it.

Oracle: the matrix hashes below were produced by an independent implementation
(the python-qrcode 8.2 library, error correction M, byte mode, forced mask),
not by this encoder. Every one of the eight masks must match, at version 3 (the
size of a real share link) and at version 10 (which also carries version
information blocks).
"""
from hashlib import sha256
import re
import struct
import sys
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from connectthebots_server import qr  # noqa: E402
from connectthebots_server.campaign import share_options  # noqa: E402

LINK = "https://connectthebots.net/r/CTB-7KQ2MX"
LONG = LINK + "?" + "a" * 170
REFERENCE = {
    (LINK, 3): ("0cff648394ec00fd49e96e363fbc8af8c068a902f8555bdeee292343a5eee23a",
                "3e526c6360c1f0f68557821d54fa9d67ce0277a45f6ab8685cb1daefa8b3d930",
                "9f4fd8a52350e99afdadf326e232eb4a9be8e4e16e3e25cc60a3cb7615736d8a",
                "bc3f23ecb9a6c7abb9b5b8fe5a34e9611ed61934094acd0f23cb9ebc62ae94bc",
                "bf6dbd60ca7ee38d09aec4ef3cdf4811be4233cae7a1c1dea3c5afc110b6c892",
                "7250d9d71b4cc998b17b8e1b297a48012d4635d28037f7e894629b2bc85a0137",
                "5bead05cafa5c2cdb602dffbaca5fc4b6a3ad44c2baa153d94092b27a38db55c",
                "194be5e1472193bd7392ad1749be7f049a54a152827206ca95d47809950dbd03"),
    (LONG, 10): ("594980cab6726f2b686cc13f05c42684ce8dd80e16b6e64422856e3d9ed7d30b",
                 "76339bd6fb668e67aa9fc9cee55299cb5db431a163fc4294ac2349f13160b374",
                 "86b18f004f04b7b3be159d6a32b04eca78fb7ec306ea3684a442df877b689516",
                 "5fde1738f0f4c4a763fa7ba8c4283882a61c8f81260c310bc2ff02cf9032ebcc",
                 "bee7a3a7d622cf50af1c5b54a9c515a63aa062b321c9e1a5c3b9975491663f98",
                 "e20d486d3332d96a5de9a0d6bd3bbb08eb30c8ba17ef23e33d94ee799fb5d3b4",
                 "dca3ee2f1a4e369b98521b85fc41ada3b8f745deecce4b07a75989c4fd3264c3",
                 "ec25afc9adf2a5cef36f4028bf97c51cee8ed96a3315db7f36b8390928d19476"),
}


def digest(matrix):
    return sha256("\n".join("".join("1" if value else "0" for value in row) for row in matrix).encode()).hexdigest()


class QrEncoderTests(unittest.TestCase):
    def test_every_mask_matches_an_independent_encoder(self):
        for (text, version), hashes in REFERENCE.items():
            for mask, expected in enumerate(hashes):
                with self.subTest(version=version, mask=mask):
                    matrix = qr.encode(text, mask)
                    self.assertEqual(len(matrix), version * 4 + 17)
                    self.assertEqual(digest(matrix), expected)

    def test_default_picks_one_of_the_valid_masks_and_is_deterministic(self):
        chosen = digest(qr.encode(LINK))
        self.assertIn(chosen, REFERENCE[(LINK, 3)])
        self.assertEqual(chosen, digest(qr.encode(LINK)))

    def test_refuses_text_beyond_version_ten(self):
        with self.assertRaises(ValueError):
            qr.encode("x" * 214)

    def test_svg_is_a_single_inert_path_with_a_quiet_zone(self):
        image = qr.svg(qr.encode(LINK), label="QR <code>")
        self.assertTrue(image.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 37 37"'))
        self.assertIn("<title>QR &lt;code&gt;</title>", image)
        for forbidden in ("<script", "href", "<image", "<foreignObject", "<code>"):
            self.assertNotIn(forbidden, image)
        self.assertIsNone(re.search(r"\son\w+=", image))
        self.assertEqual(image.count("<path"), 1)

    def test_png_is_a_valid_greyscale_image_of_the_same_modules(self):
        matrix = qr.encode(LINK)
        data = qr.png(matrix, scale=2)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
        self.assertEqual((width, height, depth, colour), (74, 74, 8, 0))
        start = data.index(b"IDAT") + 4
        length = struct.unpack(">I", data[start - 8:start - 4])[0]
        raw = zlib.decompress(data[start:start + length])
        rows = [raw[i * 75 + 1:(i + 1) * 75] for i in range(74)]
        self.assertEqual(rows[0], b"\xff" * 74)  # quiet zone
        for y in range(len(matrix)):
            self.assertEqual([rows[(y + 4) * 2][(x + 4) * 2] == 0x0b for x in range(len(matrix))], matrix[y])


class ShareOptionsTests(unittest.TestCase):
    def test_every_network_link_carries_the_share_link_and_nothing_else_is_contacted(self):
        options = share_options(LINK)
        self.assertEqual(options["qr"], {"svg": LINK + "/qr.svg", "png": LINK + "/qr.png", "download": LINK + "/qr.png?download=1"})
        ids = [network["id"] for network in options["networks"]]
        self.assertEqual(ids, ["whatsapp", "instagram", "facebook", "x", "bluesky", "linkedin", "telegram", "email"])
        for network in options["networks"]:
            with self.subTest(network=network["id"]):
                if network["url"] is None:
                    self.assertEqual(network["id"], "instagram")
                    self.assertIn("link sticker", network["how"])
                    continue
                query = " ".join(value for values in parse_qs(urlsplit(network["url"]).query).values() for value in values)
                self.assertIn(LINK, query)
                self.assertEqual(query.count(LINK), 1)
        hosts = {urlsplit(network["url"]).hostname for network in options["networks"] if network["url"]}
        self.assertEqual(hosts, {"wa.me", "www.facebook.com", "x.com", "bsky.app", "www.linkedin.com", "t.me", None})

    def test_text_that_already_contains_the_link_is_not_doubled(self):
        options = share_options(LINK, f"Join with my link {LINK} today.")
        whatsapp = parse_qs(urlsplit(options["networks"][0]["url"]).query)["text"][0]
        self.assertEqual(whatsapp, f"Join with my link {LINK} today.")
        x = parse_qs(urlsplit(options["networks"][3]["url"]).query)
        self.assertEqual((x["text"][0], x["url"][0]), ("Join with my link today.", LINK))


if __name__ == "__main__":
    unittest.main()
