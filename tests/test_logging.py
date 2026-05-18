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
