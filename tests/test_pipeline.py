from datetime import timedelta

from chronos.models import Query
from chronos.pipeline import (
    dedup,
    final_rank,
    keyword_match,
    recency,
    score_all,
    select_candidates,
    spread_by_source,
    tokenize,
)
from tests.conftest import NOW, SINCE, make_article


class TestTokenize:
    def test_drops_stopwords_and_case(self):
        assert tokenize("The Rise of AI Agents") == {"rise", "ai", "agents"}


class TestDedup:
    def test_collapses_identical_canonical_urls(self):
        a = make_article("Title A", "https://example.com/x?utm_source=rss")
        b = make_article("Title A", "https://www.example.com/x/")
        assert len(dedup([a, b])) == 1

    def test_collapses_near_identical_titles(self):
        a = make_article("OpenAI launches agentic payments", "https://one.com/a")
        b = make_article("OpenAI Launches Agentic Payments!", "https://two.com/b")
        assert len(dedup([a, b])) == 1

    def test_keeps_genuinely_different_stories(self):
        a = make_article("OpenAI launches payments", "https://one.com/a")
        b = make_article("Cardano adds new staking tier", "https://two.com/b")
        assert len(dedup([a, b])) == 2

    def test_earliest_copy_wins(self):
        early = make_article(
            "Same story", "https://one.com/a", published_at=NOW - timedelta(days=2)
        )
        late = make_article("Same story", "https://two.com/b", published_at=NOW)
        assert dedup([late, early])[0].url == "https://one.com/a"


class TestKeywordMatch:
    tokens = {"agentic", "payments"}

    def test_title_match_scores_above_body_match(self):
        in_title = make_article("Agentic payments arrive", "https://a.com/1")
        in_body = make_article(
            "Something else", "https://a.com/2", snippet="about agentic payments"
        )
        assert keyword_match(in_title, self.tokens) > keyword_match(in_body, self.tokens)

    def test_no_overlap_is_zero(self):
        article = make_article("Weather report", "https://a.com/3", snippet="sunny")
        assert keyword_match(article, self.tokens) == 0.0

    def test_result_is_clamped_to_one(self):
        article = make_article(
            "Agentic payments", "https://a.com/4", snippet="agentic payments"
        )
        assert keyword_match(article, self.tokens) == 1.0

    def test_empty_topic_is_zero(self):
        assert keyword_match(make_article("x", "https://a.com/5"), set()) == 0.0


class TestRecency:
    def test_newer_scores_higher(self):
        fresh = make_article("a", "https://a.com/1", published_at=NOW)
        stale = make_article(
            "b", "https://a.com/2", published_at=NOW - timedelta(days=10)
        )
        assert recency(fresh, NOW) > recency(stale, NOW)

    def test_bounded_zero_to_one(self):
        old = make_article("c", "https://a.com/3", published_at=NOW - timedelta(days=90))
        assert 0.0 <= recency(old, NOW) <= 1.0


class TestScoring:
    def test_on_topic_recent_beats_offtopic_recent(self, query):
        on_topic = make_article(
            "Agentic payments land on Cardano", "https://a.com/1", published_at=NOW
        )
        off_topic = make_article("Sourdough recipes", "https://a.com/2", published_at=NOW)
        score_all([on_topic, off_topic], query, now=NOW)
        assert on_topic.score > off_topic.score

    def test_source_weight_breaks_ties(self, query):
        strong = make_article(
            "Agentic payments", "https://a.com/1", published_at=NOW, weight=1.0
        )
        weak = make_article(
            "Agentic payments", "https://a.com/2", published_at=NOW, weight=0.2
        )
        score_all([strong, weak], query, now=NOW)
        assert strong.score > weak.score


class TestSpreadBySource:
    def test_cap_holds_when_other_sources_can_fill_the_digest(self):
        articles = [
            make_article(f"Paper {i}", f"https://arxiv.org/{i}", source="rss:arxiv")
            for i in range(10)
        ] + [
            make_article("Funding round", "https://tc.com/1", source="rss:techcrunch"),
            make_article("Model release", "https://vb.com/1", source="rss:vb"),
            make_article("Chip supply", "https://verge.com/1", source="rss:verge"),
            make_article("Policy shift", "https://mit.com/1", source="rss:mit"),
        ]
        picked = spread_by_source(articles, limit=6, cap=2)
        assert len(picked) == 6
        assert sum(1 for a in picked if a.source == "rss:arxiv") == 2

    def test_overflow_fills_rather_than_returning_a_short_digest(self):
        # Nothing else exists, so a dominant source is allowed past its cap.
        articles = [
            make_article(f"Paper {i}", f"https://arxiv.org/{i}", source="rss:arxiv")
            for i in range(5)
        ]
        assert len(spread_by_source(articles, limit=4, cap=2)) == 4

    def test_diverse_items_always_outrank_overflow_items(self):
        articles = [
            make_article(f"Paper {i}", f"https://arxiv.org/{i}", source="rss:arxiv")
            for i in range(5)
        ] + [make_article("Funding round", "https://tc.com/1", source="rss:techcrunch")]
        picked = spread_by_source(articles, limit=4, cap=2)
        assert any(a.source == "rss:techcrunch" for a in picked)


class TestSelectCandidates:
    def test_drops_offtopic_when_enough_on_topic_remain(self):
        query = Query(topic="agentic payments", since=SINCE, limit=2, candidate_limit=10)
        on_topic = [
            make_article(f"Agentic payments {i}", f"https://a.com/{i}", source=f"s{i}")
            for i in range(4)
        ]
        off_topic = [make_article("Gardening tips", "https://b.com/1", source="s9")]
        score_all(on_topic + off_topic, query, now=NOW)
        titles = [a.title for a in select_candidates(on_topic + off_topic, query)]
        assert "Gardening tips" not in titles

    def test_returns_nothing_rather_than_offtopic_filler(self):
        query = Query(topic="cardano staking", since=SINCE, limit=3, candidate_limit=10)
        articles = [
            make_article(f"Unrelated story {i}", f"https://a.com/{i}", source=f"s{i}")
            for i in range(4)
        ]
        score_all(articles, query, now=NOW)
        assert select_candidates(articles, query) == []

    def test_returns_the_few_genuine_matches_for_a_narrow_topic(self):
        query = Query(topic="cardano staking", since=SINCE, limit=5, candidate_limit=10)
        articles = [
            make_article("Cardano staking rewards change", "https://a.com/1", source="s1"),
            make_article("Unrelated story", "https://a.com/2", source="s2"),
        ]
        score_all(articles, query, now=NOW)
        picked = select_candidates(articles, query)
        assert [a.title for a in picked] == ["Cardano staking rewards change"]


class TestFinalRank:
    def test_llm_relevance_outranks_prior_score(self):
        low_prior = make_article("a", "https://a.com/1", source="s1")
        low_prior.score, low_prior.relevance = 0.2, 0.95
        high_prior = make_article("b", "https://a.com/2", source="s2")
        high_prior.score, high_prior.relevance = 0.8, 0.1
        assert final_rank([high_prior, low_prior], 2)[0] is low_prior

    def test_falls_back_to_prior_score_without_relevance(self):
        weak = make_article("a", "https://a.com/1", source="s1")
        weak.score = 0.2
        strong = make_article("b", "https://a.com/2", source="s2")
        strong.score = 0.9
        assert final_rank([weak, strong], 2)[0] is strong

    def test_respects_limit(self):
        articles = []
        for i in range(10):
            article = make_article(f"t{i}", f"https://a.com/{i}", source=f"s{i}")
            article.score = i / 10
            articles.append(article)
        assert len(final_rank(articles, 3)) == 3
