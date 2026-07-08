import os
import pathlib

import core


# =============================================================================
# clean_vtt_content
# =============================================================================

VTT_SAMPLE = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
<c>Hello</c> world

00:00:02.000 --> 00:00:04.000
Hello world
this is a test

00:00:04.000 --> 00:00:06.000
this is a test sentence
"""

SRT_SAMPLE = """1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,000
Second line
"""


def test_clean_vtt_content_strips_header_and_timestamps():
    cleaned = core.clean_vtt_content(VTT_SAMPLE)
    assert "WEBVTT" not in cleaned
    assert "-->" not in cleaned


def test_clean_vtt_content_strips_inline_tags():
    cleaned = core.clean_vtt_content(VTT_SAMPLE)
    assert "<c>" not in cleaned
    assert "Hello world" in cleaned


def test_clean_vtt_content_deduplicates_rolling_window():
    cleaned = core.clean_vtt_content(VTT_SAMPLE)
    lines = cleaned.splitlines()
    # "Hello world" appears as a standalone cue and then again as the first
    # line of the next cue; it must not be duplicated in the output.
    assert lines.count("Hello world") <= 1
    # The growing "this is a test" -> "this is a test sentence" cue should
    # collapse to just the longer line.
    assert "this is a test" not in lines
    assert "this is a test sentence" in lines


def test_clean_vtt_content_strips_srt_indices_and_timestamps():
    cleaned = core.clean_vtt_content(SRT_SAMPLE)
    assert "1" not in cleaned.splitlines()
    assert "2" not in cleaned.splitlines()
    assert "-->" not in cleaned
    assert "Hello world" in cleaned
    assert "Second line" in cleaned


# =============================================================================
# sanitize_description
# =============================================================================

def test_sanitize_description_strips_urls_and_hashtags():
    raw = "Check this out https://example.com/foo and www.example.org #cool #stuff"
    result = core.sanitize_description(raw)
    assert "http" not in result
    assert "www." not in result
    assert "#cool" not in result
    assert "#stuff" not in result


def test_sanitize_description_collapses_blank_lines():
    raw = "Line one\n\n\n\n\nLine two"
    result = core.sanitize_description(raw)
    assert "\n\n\n" not in result
    assert "Line one" in result
    assert "Line two" in result


# =============================================================================
# format_for_llm
# =============================================================================

def test_format_for_llm_formats_upload_date():
    metadata = {
        "title": "My Video",
        "webpage_url": "https://youtu.be/abc123",
        "upload_date": "20240115",
        "description": "Some description",
    }
    result = core.format_for_llm(metadata, "transcript text")
    assert "# TITLE: My Video" in result
    assert "# PUBLISH DATE: 2024-01-15" in result
    assert "# URL: https://youtu.be/abc123" in result
    assert "transcript text" in result


def test_format_for_llm_handles_missing_fields():
    result = core.format_for_llm({}, "transcript text")
    assert "Unknown Title" in result
    assert "Unknown URL" in result
    assert "Unknown Date" in result


# =============================================================================
# _safe_name
# =============================================================================

def test_safe_name_strips_unsafe_characters():
    assert core._safe_name("Hello/World: Test?*") == "HelloWorld Test"
    assert core._safe_name("Keep - (parens) _ok") == "Keep - (parens) _ok"


# =============================================================================
# _pick_best_sub_file / _has_usable_subtitles
# =============================================================================

def test_pick_best_sub_file_prefers_ru_orig(tmp_path):
    en = tmp_path / "Video [abc].en.vtt"
    ru = tmp_path / "Video [abc].ru.vtt"
    ru_orig = tmp_path / "Video [abc].ru-orig.vtt"
    for f in (en, ru, ru_orig):
        f.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n")

    picked = core._pick_best_sub_file([en, ru, ru_orig])
    assert picked == ru_orig


def test_pick_best_sub_file_excludes_live_chat(tmp_path):
    live_chat = tmp_path / "Video [abc].live_chat.vtt"
    live_chat.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n")
    assert core._pick_best_sub_file([live_chat]) is None


def test_pick_best_sub_file_returns_none_for_empty_list():
    assert core._pick_best_sub_file([]) is None


def test_has_usable_subtitles_rejects_short_content(tmp_path):
    f = tmp_path / "short.vtt"
    f.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n")
    assert core._has_usable_subtitles(f) is False


def test_has_usable_subtitles_accepts_real_content(tmp_path):
    f = tmp_path / "real.vtt"
    f.write_text(VTT_SAMPLE)
    assert core._has_usable_subtitles(f) is True


def test_has_usable_subtitles_handles_missing_file(tmp_path):
    assert core._has_usable_subtitles(tmp_path / "missing.vtt") is False
    assert core._has_usable_subtitles(None) is False


# =============================================================================
# merge_files
# =============================================================================

def _make_files(tmp_path, contents):
    paths = []
    for i, content in enumerate(contents):
        p = tmp_path / f"{i:02d}.txt"
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    return paths


def test_merge_files_no_merge_returns_input_unchanged(tmp_path):
    files = _make_files(tmp_path, ["a", "b"])
    result = core.merge_files(files, "No Merge", tmp_path)
    assert result == files


def test_merge_files_one_file_joins_with_separator(tmp_path):
    files = _make_files(tmp_path, ["AAA", "BBB", "CCC"])
    result = core.merge_files(files, "One File", tmp_path)
    assert len(result) == 1
    content = result[0].read_text(encoding="utf-8")
    assert content == "AAA\n\n" + "=" * 40 + "\n\nBBB\n\n" + "=" * 40 + "\n\nCCC"


def test_merge_files_chunking_uses_separator_between_every_file(tmp_path):
    files = _make_files(tmp_path, ["AAA", "BBB", "CCC"])
    result = core.merge_files(files, "Medium Chunks (~50k chars)", tmp_path)
    assert len(result) == 1
    content = result[0].read_text(encoding="utf-8")
    # Regression test for the merge separator bug: every file boundary must
    # have the "====" separator between it and the next file's content, not
    # glued to the front of the first file's content.
    assert content == "AAA\n\n" + "=" * 40 + "\n\nBBB\n\n" + "=" * 40 + "\n\nCCC"
    assert not content.startswith("\n\n" + "=" * 40)


def test_merge_files_chunking_splits_on_size_limit(tmp_path):
    big_a = "A" * 30_000
    big_b = "B" * 30_000
    files = _make_files(tmp_path, [big_a, big_b])
    result = core.merge_files(files, "Medium Chunks (~50k chars)", tmp_path)
    # 30k + 30k > 50k limit, so the two files must land in separate chunks.
    assert len(result) == 2


def test_merge_files_empty_list_returns_empty():
    assert core.merge_files([], "One File", pathlib.Path("/tmp")) == []


# =============================================================================
# cleanup_old_sessions
# =============================================================================

def test_cleanup_old_sessions_removes_only_stale_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "BASE_TEMP_DIR", tmp_path)

    old_dir = tmp_path / "old-session"
    old_dir.mkdir()
    (old_dir / "file.txt").write_text("x")
    old_time = core.time.time() - 8 * 86400
    os.utime(old_dir, (old_time, old_time))

    fresh_dir = tmp_path / "fresh-session"
    fresh_dir.mkdir()

    core.cleanup_old_sessions(max_age_days=7)

    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_cleanup_old_sessions_handles_missing_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "BASE_TEMP_DIR", tmp_path / "does-not-exist")
    core.cleanup_old_sessions()  # must not raise
