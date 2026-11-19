from unittest import mock


def run_inline(fn, *args):
    fn(*args)


class SyncBackgroundMixin:
    """
    Runs work queued on helpers.background_tasks in the calling thread, so a
    test sees its effects at once and on its own database connection.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patcher = mock.patch('helpers.background_tasks.submit', side_effect=run_inline)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
