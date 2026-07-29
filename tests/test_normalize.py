from chronos.normalize import article_id, canonical_url, clean_text, first_sentences


class TestCanonicalUrl:
    def test_strips_tracking_params(self):
        assert (
            canonical_url("https://example.com/post?utm_source=rss&id=7&fbclid=abc")
            == "https://example.com/post?id=7"
        )

    def test_strips_www_fragment_and_trailing_slash(self):
        assert (
            canonical_url("https://www.Example.com/post/#section")
            == "https://example.com/post"
        )

    def test_variants_of_same_article_collapse(self):
        a = canonical_url("https://www.example.com/x/?utm_campaign=news")
        b = canonical_url("https://example.com/x#top")
        assert a == b

    def test_root_path_survives(self):
        assert canonical_url("https://example.com") == "https://example.com/"

    def test_empty_input(self):
        assert canonical_url("") == ""


def test_article_id_is_stable_across_url_variants():
    assert article_id("https://www.example.com/x/?utm_source=a") == article_id(
        "https://example.com/x"
    )


class TestCleanText:
    def test_strips_html_and_entities(self):
        assert clean_text("<p>Hello &amp; welcome</p>") == "Hello & welcome"

    def test_truncates_on_word_boundary(self):
        out = clean_text("word " * 300, limit=50)
        assert len(out) <= 51 and out.endswith("…")

    def test_none_is_safe(self):
        assert clean_text(None) == ""


class TestFirstSentences:
    def test_takes_two_sentences(self):
        text = "First one. Second one. Third one."
        assert first_sentences(text) == "First one. Second one."

    def test_unpunctuated_text_returned_whole(self):
        assert first_sentences("no punctuation here") == "no punctuation here"

    def test_empty(self):
        assert first_sentences("") == ""
