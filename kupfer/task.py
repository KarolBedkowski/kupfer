import sys
import threading
import typing as ty
import types

from gi.repository import GLib

from kupfer import scheduler, pretty


TaskCallback = ty.Callable[[ty.Any], None]
ExecInfo = tuple[ty.Type[Exception], Exception, types.TracebackType]

class Task:
    """Represent a task that can be done in the background

    The finish_callback received in Task.start(..) must be stored,
    and regardless if the task exits with an error, or completes
    successfully, the callback *must* be called.

    The finish callback must pass the Task instance itself as
    the only and first argument:

        finish_callback(self)
    """

    def __init__(self, name: ty.Optional[str]=None)->None:
        self.name = name

    def __repr__(self)->str:
        name = repr(getattr(self, "name", None))
        return f"<{type(self).__module__}.{type(self).__name__} name={name}>"

    def start(self, finish_callback: TaskCallback) -> None:
        raise NotImplementedError


class ThreadTask(Task):
    """Run in a thread"""

    def __init__(self, name: ty.Optional[str]=None):
        Task.__init__(self, name)
        self._finish_callback : ty.Optional[TaskCallback] = None

    def thread_do(self) -> None:
        """Override this to run what should be done in the thread"""
        raise NotImplementedError

    def thread_finish(self)->None:
        """This finish function runs in the main thread after thread
        completion, and can be used to communicate with the GUI.
        """

        pass
    def thread_finally(self, exc_info: ExecInfo) -> None:
        """Always run at thread finish"""
        if exc_info is not None:
            etype, value, tb = exc_info
            raise etype(value).with_traceback(tb)

    def _thread_finally(self, exc_info: ExecInfo) -> None:
        try:
            self.thread_finally(exc_info)
        finally:
            if self._finish_callback:
                self._finish_callback(self)

    def _run_thread(self) -> None:
        exc_info = None
        try:
            self.thread_do()
            GLib.idle_add(self.thread_finish)
        except:
            exc_info = sys.exc_info()
        finally:
            GLib.idle_add(self._thread_finally, exc_info)

    def start(self, finish_callback: TaskCallback) -> None:
        self._finish_callback = finish_callback
        thread = threading.Thread(target=self._run_thread)
        thread.start()


class TaskRunner(pretty.OutputMixin):
    """Run Tasks in the idle Loop"""

    def __init__(self, end_on_finish: bool) -> None:
        self.tasks :set[Task] = set()
        self.end_on_finish = end_on_finish
        scheduler.GetScheduler().connect("finish", self._finish_cleanup)

    def _task_finished(self, task):
        self.output_debug("Task finished", task)
        self.tasks.remove(task)

    def add_task(self, task: Task)->None:
        """Register @task to be run"""
        self.tasks.add(task)
        task.start(self._task_finished)

    def _finish_cleanup(self, _sched: ty.Any)->None:
        if self.end_on_finish:
            self.tasks.clear()
            return

        if self.tasks:
            self.output_info("Uncompleted tasks:")
            for task in self.tasks:
                self.output_info(task)
