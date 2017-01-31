from celery import shared_task
from models import ReportRequest, ReportRequestExport
import reportengine

#TODO - Add fixtures for these tasks, so the report cleanup is loaded into celerybeat.
@shared_task()
def async_report(token):
    print "Running async reprot from ",token 
    try:
        report_request = ReportRequest.objects.get(token=token)
    except ReportRequest.DoesNotExist:
        # Error?
        return 
    # THis is like 90% the same 
    reportengine.autodiscover() ## Populate the reportengine registry
    report_request.build_report()

@shared_task()
def async_report_export(token):
   
    try:
        report_request_export = ReportRequestExport.objects.get(token=token)
    except ReportRequestExport.DoesNotExist:
        # Error?
        return 
    # THis is like 90% the same 
    reportengine.autodiscover() ## Populate the reportengine registry
    report_request_export.build_report()


@shared_task()
def cleanup_stale_reports():
    ReportRequest.objects.cleanup_stale_requests()
