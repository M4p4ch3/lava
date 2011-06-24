import datetime
import json
import logging

from django.db import IntegrityError, transaction

from twisted.internet.threads import deferToThread

from zope.interface import implements

from lava_scheduler_app.models import Device, TestJob
from lava_scheduler_daemon.jobsource import IJobSource


logger = logging.getLogger(__name__)


class DatabaseJobSource(object):

    implements(IJobSource)

    logger = logger.getChild('DatabaseJobSource')

    def getBoardList_impl(self):
        return [d.hostname for d in Device.objects.all()]

    def getBoardList(self):
        return deferToThread(self.getBoardList_impl)

    @transaction.commit_manually()
    def getJobForBoard_impl(self, board_name):
        while True:
            device = Device.objects.get(hostname=board_name)
            if device.status != Device.IDLE:
                return None
            jobs_for_device = TestJob.objects.all().filter(
                target=device, status=TestJob.SUBMITTED)
            jobs_for_device.order_by('submit_time')
            jobs = jobs_for_device[:1]
            if jobs:
                job = jobs[0]
                job.status = TestJob.RUNNING
                job.start_time = datetime.datetime.utcnow()
                device.status = Device.RUNNING
                device.current_job = job
                try:
                    # The unique constraint on current_job may cause this to
                    # fail in the case of concurrent requests for different
                    # boards grabbing the same job.  If there are concurrent
                    # requests for the *same* board they may both return the
                    # same job -- this is an application level bug though.
                    device.save()
                except IntegrityError:
                    transaction.rollback()
                    continue
                else:
                    job.save()
                    transaction.commit()
                    return json.loads(job.definition)
            else:
                return None

    def getJobForBoard(self, board_name):
        return deferToThread(self.getJobForBoard_impl, board_name)

    @transaction.commit_on_success()
    def jobCompleted_impl(self, board_name, log_stream):
        self.logger.debug('marking job as complete on %s', board_name)
        device = Device.objects.get(hostname=board_name)
        device.status = Device.IDLE
        device.current_job = None
        job = TestJob.objects.get(target=device, status=TestJob.RUNNING)
        job.status = TestJob.COMPLETE
        job.end_time = datetime.datetime.utcnow()
        device.save()
        job.save()

    def jobCompleted(self, board_name, log_file_path):
        return deferToThread(
            self.jobCompleted_impl, board_name, log_file_path)
