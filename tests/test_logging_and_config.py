from pathlib import Path

import pytest

from config import FatalWarningError, PipelineConfig, report_warning
from logging_setup import configure_logging, get_logger


def test_get_logger_returns_the_shared_logger():
    logger = get_logger()
    assert logger.name == "py2rust"


def test_configure_logging_writes_a_log_file(tmp_path: Path):
    configure_logging(output_dir=tmp_path)
    logger = get_logger()
    logger.warning("hello from a test")

    log_path = tmp_path / "py2rust.log"
    assert log_path.exists()
    assert "hello from a test" in log_path.read_text()


def test_configure_logging_without_output_dir_does_not_require_a_directory():
    configure_logging(output_dir=None)


def test_pipeline_config_defaults_to_warnings_not_fatal():
    config = PipelineConfig()
    assert config.warnings_as_fatal is False


def test_report_warning_raises_when_strict():
    config = PipelineConfig(warnings_as_fatal=True)
    with pytest.raises(FatalWarningError):
        report_warning(config, "uh oh", code="TEST001")


def test_report_warning_does_not_raise_when_not_strict():
    config = PipelineConfig(warnings_as_fatal=False)
    report_warning(config, "just a warning", code="TEST001")
