"""OCR detectors return phrases; the row parser needs words with geometry."""
from app.importers.pdf import _split_words


def test_merged_phrase_is_split_into_words():
    merged = [{'text': '07 Nov 26 VIS LIDL GB', 'x0': 100.0, 'x1': 300.0, 'top': 50.0}]
    words = _split_words(merged)
    assert [w['text'] for w in words] == ['07', 'Nov', '26', 'VIS', 'LIDL', 'GB']


def test_split_positions_are_monotonic_and_cover_the_box():
    merged = [{'text': 'SALARY ACME LTD', 'x0': 60.0, 'x1': 260.0, 'top': 10.0}]
    words = _split_words(merged)
    assert words[0]['x0'] == 60.0
    assert 259.0 <= words[-1]['x1'] <= 260.0
    for left, right in zip(words, words[1:]):
        assert left['x1'] <= right['x0'] + 1e-6


def test_single_words_are_untouched():
    items = [{'text': 'TESCO', 'x0': 1.0, 'x1': 9.0, 'top': 2.0}]
    assert _split_words(items) == items
