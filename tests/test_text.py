from maibot_qq_voice_call.text import CONTROL_MARKER, clean_tts_text, is_filler_transcript


def test_filler_filter_handles_chinese_and_english() -> None:
    assert is_filler_transcript("嗯……")
    assert is_filler_transcript("uhm")
    assert not is_filler_transcript("嗯我想问一下")


def test_filler_filter_drops_repeated_acknowledgements_and_deictic_fragments() -> None:
    assert is_filler_transcript("好的，好的。")
    assert is_filler_transcript("这个这个这个")
    assert is_filler_transcript("嗯，明白了。")
    assert is_filler_transcript("是吧？")
    assert not is_filler_transcript("你是谁？")


def test_tts_cleanup_blocks_control_prompt_leaks() -> None:
    assert clean_tts_text(f"{CONTROL_MARKER} hidden") == ""


def test_tts_cleanup_removes_markdown_and_urls() -> None:
    assert clean_tts_text("看看[这里](https://example.com) 🙂") == "看看这里"
