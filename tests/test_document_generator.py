from datetime import datetime, timezone

from better_morning.config import GlobalConfig, OutputSettings
from better_morning.document_generator import DocumentGenerator


def test_save_and_load_digest_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    global_config = GlobalConfig(
        output_settings=OutputSettings(),
        context_digest_size=2,
    )
    generator = DocumentGenerator(global_config.output_settings, global_config)

    collection_summaries = {"News": "Summary content"}
    today = datetime(2025, 1, 5, tzinfo=timezone.utc)

    generator.save_digest_to_history(collection_summaries, today)

    previous = generator.load_previous_digests()
    assert len(previous) == 1
    assert "Summary content" in previous[0]["content"]

    context = generator.get_context_for_llm()
    assert "Digest from 2025-01-05" in context
