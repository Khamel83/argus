import logging


def test_setup_logging_defaults_to_stderr(capsys):
    from argus.logging import setup_logging

    logger = setup_logging("INFO")
    logging.getLogger("argus.mcp.server").info("stdio-safe log")

    captured = capsys.readouterr()
    assert "stdio-safe log" not in captured.out
    assert "stdio-safe log" in captured.err

    for handler in list(logger.handlers):
        logger.removeHandler(handler)


def test_causal_logging_redacts_urls_and_secrets(capsys):
    from argus.logging import log_failure, setup_logging

    logger = setup_logging("WARNING")
    log_failure(
        logger,
        code="acquisition_blocked",
        request_id="request-1",
        operation_id="operation-1",
        detail="GET https://example.test/path?q=secret token=hidden",
    )

    captured = capsys.readouterr()
    assert "<url>" in captured.err
    assert "example.test" not in captured.err
    assert "hidden" not in captured.err
    assert "request-1" in captured.err
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
