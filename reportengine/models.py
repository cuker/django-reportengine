from django.db import models
from django.db.models import Q

import datetime
import reportengine

from jsonfield import JSONField
from settings import STALE_REPORT_SECONDS

class AbstractScheduledTask(models.Model):
    """
    Base class of scheduled task.
    """
    request_made = models.DateTimeField(default=datetime.datetime.now, db_index=True)
    completion_timestamp = models.DateTimeField(blank=True, null=True)
    token = models.CharField(max_length=255, db_index=True)
    task = models.CharField(max_length=128, blank=True)
    
    def get_task_function(self):
        raise NotImplementedError
    
    def task_status(self):
        if self.task:
            func = self.get_task_function()
            result = func.AsyncResult(self.task)
            return result.state
        return None
    
    def schedule_task(self):
        func = self.get_task_function()
        return func.delay(self.token)
    
    class Meta:
        abstract = True

class ReportRequestManager(models.Manager):
    """
    The anager for report requests.
    """
    def completed(self):
        """
        Gets all completed requests.
        :return:  A queryset of completed requests.
        """
        return self.filter(completion_timestamp__isnull=False)
    
    def stale(self):
        """
        Gets all stale requests, based on "STALE_REPORT_SECONDS" settings.
        :return:  A queryset of all outstanding requests older than STALE_REPORT_SECONDS.
        """
        cutoff = datetime.datetime.now() - datetime.timedelta(seconds=STALE_REPORT_SECONDS)
        return self.filter(completion_timestamp__lte=cutoff).filter(Q(viewed_on__lte=cutoff) | Q(viewed_on__isnull=True))
    
    def cleanup_stale_requests(self):
        """
        Deletes all stale requests.
        :return:  A queryset of deleted requests.
        """
        return self.stale().delete()

class ReportRequest(AbstractScheduledTask):
    """
    Session based report request. Report request is made, and the token for the request is stored in the session so
    only that user can access this report. Task system generates the report and drops it into "content".
    When content is no longer null, user sees full report and their session token is cleared.
    """
    # TODO consider cleanup (when should this be happening? after the request is made? What about caching? throttling?)
    namespace = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    params = JSONField() #GET params
    viewed_on = models.DateTimeField(blank=True, null=True)
    aggregates = JSONField(datatype=list)
    
    objects = ReportRequestManager()
    
    def get_report(self):
        """
        Gets a report object, based on this ReportRequest's slug and namespace.
        :return:  A subclass of reportengine.Report
        """
        return reportengine.get_report(self.namespace, self.slug)()
    
    @models.permalink
    def get_absolute_url(self):
        """
        This is a URL to a message that either the report is currently being processed, or the report results.

        :return: A tuple of reports-request-view, the report token and no keywords.
        """
        return ('reports-request-view', [self.token], {})
    
    @models.permalink
    def get_report_url(self):
        """
        This is a URL to the report constructor view.

        :return:  A tuple of report-view, and the namespace and slug for the referenced report for this request.
        """
        return ('reports-view', [self.namespace, self.slug], {})
    
    def build_report(self):
        """
        build_report does this:
            fetch the report associated to this report request,
            constructs the filter form, based on this report request's params,
            get the report's default mask,
            updates it with the filters (again from params),
            gets the report's results, ordered by the 'order_by' value in params
            this then saves every row in the report as a reportrequestrow object.
            the aggregates and completion timestamp are stored on this request.
        """
        kwargs = self.params

        # THis is like 90% the same 
        #reportengine.autodiscover() ## Populate the reportengine registry
        try:
            report = self.get_report()
        except Exception, err:
            raise err  
        
        filter_form = report.get_filter_form(kwargs)
        if filter_form.fields:
            if filter_form.is_valid():
                filters = filter_form.cleaned_data
            else:
                filters = {}
        else:
            if report.allow_unspecified_filters:
                filters = kwargs
            else:
                filters = {}
        
        # Remove blank filters
        for k in filters.keys():
            if filters[k] == '':
                del filters[k]
        
        ## Update the mask and run the report!
        mask = report.get_default_mask()
        mask.update(filters)
        rows, aggregates = report.get_rows(mask, order_by=kwargs.get('order_by',None))
        
        ReportRequestRow.objects.filter(report_request=self).delete()
        
        for index, row in enumerate(rows):
            report_row = ReportRequestRow(report_request=self, row_number=index)
            report_row.data = row
            report_row.save()
        
        self.aggregates = aggregates
        self.completion_timestamp = datetime.datetime.now()
        self.save()
    
    def get_task_function(self):
        from tasks import async_report
        return async_report

class ReportRequestRow(models.Model):
    """
    Report Request Row holds report result rows for each report request.

    The data is stored in a list-typed JSONField.
    """
    report_request = models.ForeignKey(ReportRequest, related_name='rows')
    row_number = models.PositiveIntegerField()
    data = JSONField(datatype=list)

class ReportRequestExport(AbstractScheduledTask):
    report_request = models.ForeignKey(ReportRequest, related_name='exports')
    format = models.CharField(max_length=10)
    #mimetype = models.CharField(max_length=50)
    #content_disposition = models.CharField(max_length=200)
    payload = models.FileField(upload_to='reportengine/exports/%Y/%m/%d')
    
    def build_report(self):
        """
        Builds the export from a previously-run report.
        """
        from views import ReportRowQuery
        from urllib import urlencode
        
        from django.test.client import RequestFactory
        from django.core.files.base import ContentFile
        
        report = self.report_request.get_report()
        object_list = ReportRowQuery(self.report_request.rows.all())
        
        
        kwargs = {'report': report,
                  'title':report.verbose_name,
                  'rows':object_list,
                  'filter_form':report.get_filter_form(data=None),
                  "aggregates":self.report_request.aggregates,
                  "cl":None,
                  'report_request':self.report_request,
                  "urlparams":urlencode(self.report_request.params)}
        
        
        outputformat = None
        for of in report.output_formats:
            if of.slug == self.format:
                outputformat = of
        
        response = outputformat.get_response(kwargs, RequestFactory().get('/'))
        #TODO this is a hack
        filename = response.get('Content-Disposition', '').rsplit('filename=',1)[-1]
        if not filename:
            filename = u'%s.%s' % (self.token, self.format)
        self.payload.save(filename, ContentFile(response.content), False)
        
        self.completion_timestamp = datetime.datetime.now()
        self.save()
    
    def get_task_function(self):
        from tasks import async_report_export
        return async_report_export

