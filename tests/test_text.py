from maibot_qq_voice_call.text import (
    CONTROL_MARKER,
    TranscriptGate,
    clean_tts_text,
    is_barge_in_transcript,
    is_filler_transcript,
)


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


def test_incomplete_turn_is_merged_with_next_segment() -> None:
    now = [10.0]
    gate = TranscriptGate(4.0, clock=lambda: now[0])
    assert gate.process("我觉得") is None
    now[0] = 11.0
    assert gate.process("这个方案可以继续") == "我觉得，这个方案可以继续"


def test_stale_incomplete_turn_is_not_merged() -> None:
    now = [10.0]
    gate = TranscriptGate(4.0, clock=lambda: now[0])
    assert gate.process("然后") is None
    now[0] = 15.0
    assert gate.process("换一个模型") == "换一个模型"


def test_tts_cleanup_blocks_control_prompt_leaks() -> None:
    assert clean_tts_text(f"{CONTROL_MARKER} hidden") == ""


def test_tts_cleanup_removes_markdown_and_urls() -> None:
    assert clean_tts_text("看看[这里](https://example.com) 🙂") == "看看这里"


def test_barge_in_requires_an_explicit_command_or_wake_name() -> None:
    assert is_barge_in_transcript("等等，先听我说")
    assert is_barge_in_transcript("不对，我问的是另一个问题")
    assert is_barge_in_transcript("please hold on")
    assert is_barge_in_transcript("麦麦，停一下", wake_names=("麦麦",))
    assert not is_barge_in_transcript("今天天气还不错", wake_names=("麦麦",))
    assert not is_barge_in_transcript("我们继续说", wake_names=("麦麦",))
    assert not is_barge_in_transcript("waiting for the train")
