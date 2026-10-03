import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcap2mp4.naming import build_names, topic_label, sanitize

class NamingTests(unittest.TestCase):
    def test_single_channel(self):
        self.assertEqual(build_names("rekaman_01", ["/a/cam/0"]), {"/a/cam/0": "rekaman_01.mp4"})
    def test_multi_channel(self):
        n = build_names("rekaman_01", ["/a/cam/0", "/a/cam/1"])
        self.assertEqual(n["/a/cam/0"], "rekaman_01_a_cam_0.mp4")
        self.assertEqual(n["/a/cam/1"], "rekaman_01_a_cam_1.mp4")
    def test_illegal_and_reserved(self):
        self.assertEqual(sanitize('a<b>:c?'), "a_b__c_")
        self.assertEqual(sanitize("CON"), "_CON")
    def test_collision(self):
        n = build_names("x", ["/a", "/b"], pattern="{nama}")
        self.assertEqual(sorted(n.values()), ["x.mp4", "x_2.mp4"])
    def test_custom_pattern(self):
        n = build_names("x", ["/a/b"], pattern="{nama}_{indeks}")
        self.assertEqual(n["/a/b"], "x_0.mp4")
    def test_topic_label(self):
        self.assertEqual(topic_label("/head/camera/image_compressed"), "head_camera_image_compressed")

if __name__ == "__main__":
    unittest.main()
