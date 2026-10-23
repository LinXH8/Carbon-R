"""Logging: Carbon R's run_id lines at INFO, the HTTP clients kept quiet."""

import logging

import carbon_r.main as main


def test_carbon_r_logs_at_info_through_its_own_handler():
    ours = logging.getLogger("carbon_r.agent")
    assert ours.getEffectiveLevel() == logging.INFO
    assert logging.getLogger("carbon_r").handlers
    assert logging.getLogger("carbon_r").propagate is False


def test_http_clients_are_held_at_warning():
    for name in main.HTTP_CLIENT_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name


def test_the_root_logger_is_not_reconfigured():
    assert logging.getLogger().level == logging.WARNING
