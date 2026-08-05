from mml_cloud_transfer.service.controller import JobController


def test_request_only_reaches_the_active_job():
    controller = JobController()
    assert controller.request(1, "pause") is False   # nothing active
    stop = controller.job_started(1)
    assert controller.active_job_id == 1
    assert controller.request(2, "pause") is False   # wrong job
    assert not stop.is_set()
    assert controller.request(1, "cancel") is True
    assert stop.is_set()
    assert controller.job_finished() == "cancel"
    assert controller.active_job_id is None
    assert controller.job_finished() is None         # intent consumed


def test_each_job_gets_a_fresh_stop_event():
    controller = JobController()
    first = controller.job_started(1)
    controller.request(1, "pause")
    controller.job_finished()
    second = controller.job_started(2)
    assert first.is_set()
    assert not second.is_set()
