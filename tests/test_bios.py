from datetime import datetime

from ingest import bios
from ingest.biotext import lines_for
from ingest.push import filing_card

PAGES = ["Mr. Rahul Sharma has been appointed Chief Financial Officer. He has over 20 years of experience in finance, previously at Unilever and Tata Motors."]
QUOTE = "He has over 20 years of experience in finance, previously at Unilever and Tata Motors."


def change(**over):
    return {"person": "Rahul Sharma", "role": "CFO", "change_type": "appointed", "effective": "1 Dec 2026", "profile": "20 years in finance at Unilever and Tata Motors, a long career.  Yes indeed.",
            "profile_quote": QUOTE, "reason": None, **over}


def test_profile_with_a_real_quote_is_kept():
    assert bios.verify_change(change(), PAGES)["profile"]


def test_profile_with_an_invented_quote_loses_only_the_profile():
    kept = bios.verify_change(change(profile_quote="Formerly chief executive of a large global bank in London."), PAGES)
    assert kept["person"] == "Rahul Sharma" and kept["profile"] is None


def test_person_not_in_the_filing_is_dropped():
    assert bios.verify_change(change(person="Anita Desai"), PAGES) is None


def test_only_incoming_people_without_a_profile_go_to_the_web():
    assert not bios.needs_web(change())
    assert bios.needs_web(change(profile=None))
    assert not bios.needs_web(change(profile=None, change_type="resigned"))


def _grounded(text, chunks):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "groundingMetadata": {"groundingChunks": chunks}}]}


WEB = [{"web": {"title": "linkedin.com", "uri": "https://example.com/1"}}]


def test_web_answer_needs_found_and_sources():
    assert bios.parse_web(_grounded('```json\n{"found": true, "summary": "Ex-Unilever finance head"}\n```', WEB))["summary"] == "Ex-Unilever finance head"
    assert bios.parse_web(_grounded('{"found": false, "summary": ""}', WEB)) is None
    assert bios.parse_web(_grounded('{"found": true, "summary": "x"}', [])) is None
    assert bios.parse_web(_grounded("no json here", WEB)) is None


def test_card_text_says_where_each_background_came_from():
    filing = lines_for({"changes": [change()]})
    assert filing[0] == "Appointed: Rahul Sharma, CFO from 1 Dec 2026" and filing[1].endswith("(from the filing)")
    web = lines_for({"changes": [change(profile=None, web={"summary": "Ex-Unilever", "sources": [{"title": "linkedin.com", "uri": "https://x"}]})]})
    assert web[1] == "Ex-Unilever (from the web: linkedin.com)"
    assert lines_for({"changes": [change(profile=None, web_checked=True)]})[1] == "No background found in the filing or on the web."
    assert lines_for({"changes": [change(profile=None)]})[1] == "Background search still to run."
    assert lines_for({"changes": [change(change_type="resigned", profile=None, reason="Personal reasons")]})[1] == "Reason given: Personal reasons"


SEBI_PAGE = ["5. Brief Profile (in case of appointment) Mr. Prithvi Vachani is currently serving as the Vice President at Dixon Technologies. "
             "A 2017 graduate of Bentley University, he led new business lines. He also built the mobile vertical. "
             "6. Disclosure of relationships between directors (in case of appointment of a director). "
             "Mr. Prithvi Vachani is the son of Mr. Sunil Vachani, Executive Chairman of Dixon Technologies (India) Limited. Apart from the aforesaid, he is not related."]


def test_sebi_template_gives_profile_and_family_link_without_a_model():
    f = bios.template_fields(SEBI_PAGE, "Prithvi Vachani")
    assert f["profile"].startswith("Mr Prithvi Vachani is currently serving") and "Bentley University" in f["profile"] and "mobile vertical" not in f["profile"]
    assert f["relationship"] == "son of Mr Sunil Vachani, Executive Chairman of Dixon Technologies (India) Limited"


def test_template_ignores_a_profile_about_someone_else_and_a_not_related_statement():
    other = ["Brief Profile Ms. Anita Rao has led treasury for many years at a large bank in Mumbai. Disclosure of relationship: Mr. Rahul Sharma is not related to any director."]
    assert bios.template_fields(other, "Rahul Sharma") == {}


def test_relationship_shows_on_the_card():
    assert lines_for({"changes": [change(relationship="son of Mr A Shah")]})[1] == "Related: son of Mr A Shah (from the filing)"


def test_a_failed_search_is_retried_and_finished_work_kept(monkeypatch):
    monkeypatch.setattr(bios, "web_background", lambda *a: (_ for _ in ()).throw(RuntimeError("busy")))
    monkeypatch.setattr(bios, "extract_changes", lambda *a: ([change(profile=None)], "m"))
    first = bios.process({}, "Co", [], "k", None)
    assert first["status"] == "pending" and first["changes"][0]["person"] == "Rahul Sharma"
    monkeypatch.setattr(bios, "extract_changes", lambda *a: (_ for _ in ()).throw(AssertionError("re-extracted")))
    monkeypatch.setattr(bios, "web_background", lambda *a: {"summary": "s", "sources": []})
    second = bios.process({}, "Co", [], "k", first)
    assert second["status"] == "done" and second["changes"][0]["web"]["summary"] == "s"


def test_a_person_with_no_web_trace_is_not_searched_again(monkeypatch):
    calls = []
    monkeypatch.setattr(bios, "web_background", lambda *a: calls.append(1))
    first = bios.process({}, "Co", [], "k", {"changes": [change(profile=None)], "model": "m"})
    bios.process({}, "Co", [], "k", first)
    assert len(calls) == 1 and first["status"] == "done"


def test_role_from_the_filing_raises_a_vague_headline_to_p1():
    event = {"symbol": "X", "category": "management_change", "priority": 2, "time": "2026-11-05T10:00:00", "headline": "Change in Management", "pdf": None, "filing_ids": [1]}
    bio = lambda role, kind="appointed": {"X:management_change:1": {"changes": [change(role=role, change_type=kind)]}}
    assert filing_card(event, [], bio("Additional Director (Executive Director)"))["priority"] == 1
    assert filing_card(event, [], bio("Group Head, Business Banking"))["priority"] == 2
    assert filing_card(event, [], bio("Managing Director", "reappointed"))["priority"] == 2


def test_management_card_uses_the_bio_and_other_cards_keep_the_headline():
    event = {"symbol": "X", "category": "management_change", "priority": 1, "time": "2026-11-05T10:00:00", "headline": "Change in Management", "pdf": None, "filing_ids": [1]}
    card = filing_card(event, [], {"X:management_change:1": {"changes": [change()]}})
    assert card["lines"][0].startswith("Appointed: Rahul Sharma")
    assert filing_card(event, [], {})["lines"] == ["Change in Management"]
