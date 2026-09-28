"""Offline tags/hashtags must describe the topic, not spoken filler ("when", "thats", "like", "think")."""
import unittest

from shortsforge import metadata
from shortsforge.highlights import Clip

# real transcript of a JRE Short whose old tags were "when, lips, think, thats, happened, its, like, things"
TEXT = ("Would you surgically remove your lips? I think that that's that's I've always assumed that probably what "
        "happened is something rational happened. Like when you look at cattle mutilation, it's like whatever they "
        "think it is, things like that. At your final stages of dying you surgically remove the lips.")
JUNK = {"when", "thats", "that's", "like", "think", "its", "it's", "things", "happened", "where", "because", "12th"}


class OfflineTagsTest(unittest.TestCase):
    def meta(self):
        clip = Clip(743.8, 797.8, 5.0, TEXT, "Would you surgically remove your lips?",
                    "Would you surgically remove your lips?", keywords=["when", "lips", "think", "thats", "cattle"])
        return metadata.offline(clip, "MrBallen Tells the Story of the Dyatlov Pass Mystery",
                                "https://youtube.com/watch?v=x")

    def test_no_filler_tags(self):
        m = self.meta()
        tags = {t.lower() for t in m["tags"]}
        self.assertFalse(tags & JUNK, tags & JUNK)
        self.assertFalse({h.lstrip("#") for h in m["hashtags"]} & JUNK)

    def test_topic_words_kept(self):
        tags = {t.lower() for t in self.meta()["tags"]}
        for want in ("lips", "cattle", "mutilation", "dyatlov"):
            self.assertIn(want, tags)


if __name__ == "__main__":
    unittest.main()
