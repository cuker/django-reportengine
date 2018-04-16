from settings import ASYNC_REPORTS, MAX_ROWS_FOR_QUICK_EXPORT

from django.shortcuts import render_to_response,redirect
from django.template.context import RequestContext
from django.contrib.admin.views.decorators import staff_member_required
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, Http404
from django.conf import settings
from django.views.generic import ListView, View, TemplateView
from django.views.decorators.cache import never_cache

import reportengine
from reportengine.models import ReportRequest, ReportRequestExport
from urllib import urlencode
import datetime,calendar,hashlib

def next_month(d):
    """helper to get next month"""
    return datetime.datetime(year=d.month<12 and d.year or d.year +1,month=d.month<12 and d.month+1 or 1,day=1)


# TODO Maybe use a class based view? how do i make it easy to build SQLReports?
@staff_member_required
def report_list(request):
    # TODO make sure to constrain based upon permissions
    reports = [{'namespace': r.namespace, 'slug': r.slug, 'verbose_name': r.verbose_name} \
            for s, r in reportengine.all_reports()]
    return render_to_response('reportengine/list.html', {'reports': reports},
                              context_instance=RequestContext(request))

class ReportRowQuery(object):
    def __init__(self, queryset):
        self.queryset = queryset
    
    def wrap(self, entry):
        return entry.data
    
    def __len__(self):
        return len(self.queryset)
    
    def count(self):
        return self.queryset.count()
    
    def __getitem__(self, val):
        if isinstance(val, slice):
            results = list()
            for entry in self.queryset[val]:
                results.append(self.wrap(entry))
            return results
        else:
            return self.wrap(self.queryset[val])

class RequestReportMixin(object):
    asynchronous_report = ASYNC_REPORTS
    
    def check_report_status(self):
        #check to see if the report is not complete but async is off
        if not self.report_request.completion_timestamp and (not self.asynchronous_report or getattr(settings, 'CELERY_ALWAYS_EAGER', False)):
            self.report_request.build_report()
            assert self.report_request.completion_timestamp
        
        #check to see if the task failed
        if self.report_request.task_status() in ('FAILURE',):
            return {'error':'Task Failed', 'completed':False}
        
        return {'completed':bool(self.report_request.completion_timestamp)}

'''
creport requested
'''

class RequestReportView(TemplateView, RequestReportMixin):
    template_name = 'reportengine/request_report.html'
    
    def report_params(self):
        '''
        Return report params without the output format
        '''
        if hasattr(self, 'form'):
            return self.form.cleaned_data
        params = dict(self.request.POST.iteritems())
        params.pop('output', None)
        params.pop('page', None)
        params.pop('_submit', None)
        params.pop('csrfmiddlewaretoken', None)
        return params
    
    def create_report_request(self):
        report_params = self.report_params()
        token_params = [str(datetime.datetime.now()), self.kwargs['namespace'], self.kwargs['slug'], urlencode(report_params)]
        token = hashlib.md5("|".join(token_params)).hexdigest()
        rr = ReportRequest(token=token,
                           namespace=self.kwargs['namespace'],
                           slug=self.kwargs['slug'],
                           params=report_params)
        rr.save()
        return rr
    
    #CONSIDER inherit from a form view
    def get_report_class(self):
        return reportengine.get_report(self.kwargs['namespace'], self.kwargs['slug'])
    
    def get_form(self):
        report_cls = self.get_report_class()
        report = report_cls()
        if self.request.method.upper() == 'POST':
            form = report.get_filter_form(data=self.request.POST)
        else:
            form = report.get_filter_form(data=None)
        return form
    
    def get_requested_reports(self,offset=0,limit=30):
        qs = ReportRequest.objects.filter(namespace=self.kwargs['namespace'],
                                          slug=self.kwargs['slug'],).order_by("-request_made")[offset:limit]
        return qs
    
    def get_context_data(self, **kwargs):
        context = TemplateView.get_context_data(self, **kwargs)
        context['filter_form'] = self.get_form()
        context['report'] = self.get_report_class()()
        context['requested_reports'] = self.get_requested_reports()
        return context
    
    def create_and_redirect_to_report_request(self):
        self.report_request = self.create_report_request()
        self.report = self.report_request.get_report()
        if self.asynchronous_report:
            self.task = self.report_request.schedule_task()
        else:
            self.report_request.build_report()
            self.report_request = ReportRequest.objects.get(pk=self.report_request.pk)
            assert self.report_request.completion_timestamp
        return HttpResponseRedirect(self.report_request.get_absolute_url())
    
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        if not context['filter_form'].fields and not context['requested_reports']:
            return self.create_and_redirect_to_report_request()
        return self.render_to_response(context)
    
    def post(self, request, *args, **kwargs):
        self.form = self.get_form()
        if self.form.is_valid(): #TODO filter controls need to be optional
            return self.create_and_redirect_to_report_request()
        else:
            context = self.get_context_data(**kwargs)
            return self.render_to_response(context)

request_report = never_cache(staff_member_required(RequestReportView.as_view()))

class ReportView(ListView, RequestReportMixin):
    asynchronous_report = ASYNC_REPORTS
    paginate_by = 50
    
    def get_report_request(self):
        token = self.kwargs['token']
        self.report_request = ReportRequest.objects.get(token=token)
        self.report = self.report_request.get_report()
        ReportRequest.objects.filter(pk=self.report_request.pk).update(viewed_on=datetime.datetime.now())
    
    def get_queryset(self):
        return ReportRowQuery(self.report_request.rows.all())
    
    def get_filter_form(self):
        filter_form = self.report.get_filter_form(self.request.REQUEST)
        return filter_form
    
    def get_changelist(self, info):
        paginator = info['paginator']
        p = info['page_obj']
        page = p.number
        rows = p.object_list
        order_by = None #TODO
        params = dict(self.request.GET.iteritems())

        # HACK: fill up a fake ChangeList object to use the admin paginator
        class MiniChangeList:
            def __init__(self,paginator, page, params, report):
                self.paginator = paginator
                self.page_num = page-1
                self.show_all = report.can_show_all
                self.can_show_all = False
                self.multi_page = True
                self.params = params

            def get_query_string(self,new_params=None,remove=None):
                # Do I need to deal with new_params/remove?
                if remove != None:
                    for k in remove:
                        del self.params[k]
                if new_params != None:
                    self.params.update(new_params)
                params = dict(self.params)
                if 'p' in params:
                    params['page'] = params.pop('p') + 1
                return "?%s"%urlencode(params)

        cl_params = order_by and dict(params,order_by=order_by) or params
        cl = MiniChangeList(paginator, page, cl_params, self.report)
        return cl
    
    def get_context_data(self, **kwargs):
        data = ListView.get_context_data(self, **kwargs)
        data.update({'report': self.report,
                    'title':self.report.verbose_name,
                    'rows':self.object_list,
                    'filter_form':self.get_filter_form(),
                    "aggregates":self.report_request.aggregates,
                    "cl":self.get_changelist(data),
                    'report_request':self.report_request,
                    "urlparams":urlencode(self.report_request.params)})
        return data
    
    def get(self, request, *args, **kwargs):
        try:
            self.get_report_request()
        except ReportRequest.DoesNotExist:
            raise Http404()
        status = self.check_report_status()
        if 'error' in status: #there was an error, try recreating the report
            #CONSIDER add max retries
            return HttpResponseRedirect(self.report_request.get_report_url())
        if not status['completed']:
            assert self.asynchronous_report
            cx = {"report_request":self.report_request,
                  "report":self.report,
                  'title':self.report.verbose_name,}
            return render_to_response("reportengine/async_wait.html",
                                      cx,
                                      context_instance=RequestContext(self.request))
        
        self.object_list = self.get_queryset()
        kwargs['object_list'] = self.object_list
        data = self.get_context_data(**kwargs)
        outputformat = None
        output = kwargs.get('output', 'admin')
        if output:
            for of in self.report.output_formats:
                if of.slug == output:
                    outputformat=of
        if not outputformat:
            outputformat = self.report.output_formats[0]
        return outputformat.get_response(data, request)

view_report = never_cache(staff_member_required(ReportView.as_view()))

class ReportExportView(TemplateView, RequestReportMixin):
    asynchronous_report = ASYNC_REPORTS
    
    def get_report_request(self):
        token = self.kwargs['token']
        self.report_request = ReportRequest.objects.get(token=token)
        self.report = self.report_request.get_report()
        ReportRequest.objects.filter(pk=self.report_request.pk).update(viewed_on=datetime.datetime.now())
    
    def get_report_export_request(self):
        try:
            self.report_export_request = self.report_request.exports.get(format=self.kwargs['output'])
        except ReportRequestExport.DoesNotExist:
            self.report_export_request = ReportRequestExport(report_request=self.report_request,
                                                             format=self.kwargs['output'],
                                                             token=(self.report_request.token + self.kwargs['output']),)
            self.report_export_request.save()
            #TODO if the parent report is done and has under a certain number of rows, then no async is needed
            #however if opting the no-async route then it may not be necessary to create this object and upload the result to s3
            if self.asynchronous_report:
                self.task = self.report_export_request.schedule_task()
            else:
                self.report_export_request.build_report()
    
    def check_report_export_status(self):
        #check to see if the report is not complete but async is off
        if not self.report_export_request.completion_timestamp and (not self.asynchronous_report or getattr(settings, 'CELERY_ALWAYS_EAGER', False)):
            self.report_export_request.build_report()
            assert self.report_export_request.completion_timestamp
        
        #check to see if the task failed
        if self.report_export_request.task_status() in ('FAILURE',):
            return {'error':'Task Failed', 'completed':False}
        
        return {'completed':bool(self.report_export_request.completion_timestamp)}
    
    def get(self, request, *args, **kwargs):
        try:
            self.get_report_request()
        except ReportRequest.DoesNotExist:
            raise Http404()
        status = self.check_report_status()
        if 'error' in status: #there was an error, try recreating the report
            #CONSIDER add max retries
            return HttpResponseRedirect(self.report_request.get_report_url())
        if not status['completed']:
            assert self.asynchronous_report
            cx = {"report_request":self.report_request,
                  "report":self.report,
                  'title':self.report.verbose_name,
                  'format':self.kwargs['output'],}
            print cx
            return render_to_response("reportengine/async_wait.html",
                                      cx,
                                      context_instance=RequestContext(self.request))
        
        #if the report is small enough there is no need to create a task to export
        if self.report_request.rows.all().count() <=  MAX_ROWS_FOR_QUICK_EXPORT:
            return ReportView.as_view()(self.request, *self.args, **self.kwargs)
        
        self.get_report_export_request()
        status = self.check_report_export_status()
        if 'error' in status: #there was an error, try recreating the report
            #CONSIDER add max retries
            return HttpResponseRedirect(self.report_request.get_report_url())
        if not status['completed']:
            assert self.asynchronous_report
            cx = {"report_request":self.report_request,
                  "report":self.report,
                  'title':self.report.verbose_name,
                  'format':self.kwargs['output'],}
            return render_to_response("reportengine/async_wait.html",
                                      cx,
                                      context_instance=RequestContext(self.request))
        return HttpResponseRedirect(self.report_export_request.payload.url)

view_report_export = never_cache(staff_member_required(ReportExportView.as_view()))

@staff_member_required
def current_redirect(request, daterange, namespace, slug, output=None):
    # TODO make month and year more intelligent per calendar
    days={"day":1,"week":7,"month":30,"year":365}
    d2=datetime.datetime.now()
    d1=d2 - datetime.timedelta(days=days[daterange])
    return redirect_report_on_date(request,d1,d2,namespace,slug,output)

@staff_member_required
def day_redirect(request, year, month, day, namespace, slug, output=None):
    year,month,day=int(year),int(month),int(day)
    d1=datetime.datetime(year=year,month=month,day=day)
    d2=d1 + datetime.timedelta(hours=24)
    return redirect_report_on_date(request,d1,d2,namespace,slug,output)

def redirect_report_on_date(request,start_day,end_day,namespace,slug,output=None):
    """Utility that allows for a redirect of a report based upon the date range to the appropriate filter"""
    report=reportengine.get_report(namespace,slug)
    params = dict(request.REQUEST)
    if report.date_field:
        # TODO this only works with model fields, needs to be more generic
        dates = {"%s__gte"%report.date_field:start_day,"%s__lt"%report.date_field:end_day}
        params.update(dates)
    if output:
        return HttpResponseRedirect("%s?%s"%(reverse("reports-view-format",args=[namespace,slug,output]),urlencode(params)))
    return HttpResponseRedirect("%s?%s"%(reverse("reports-view",args=[namespace,slug]),urlencode(params)))

@staff_member_required
def calendar_current_redirect(request):
    d=datetime.datetime.today()
    return redirect("reports-calendar-month",year=d.year,month=d.month)

@staff_member_required
def calendar_month_view(request, year, month):
    # TODO make sure to constrain based upon permissions
    # TODO find all date_field accessible reports
    year,month=int(year),int(month)
    reports=[r[1] for r in reportengine.all_reports() if r[1].date_field]
    date=datetime.datetime(year=year,month=month,day=1)
    prev_month=date-datetime.timedelta(days=1)
    nxt_month=next_month(date)
    cal=calendar.monthcalendar(year,month)
    # TODO possibly pull in date based aggregates?
    cx={"reports":reports,"date":date,"calendar":cal,"prev":prev_month,"next":nxt_month}
    return render_to_response("reportengine/calendar_month.html",cx,
                              context_instance=RequestContext(request))

@staff_member_required
def calendar_day_view(request, year, month,day):
    # TODO make sure to constrain based upon permissions
    # TODO find all date_field accessible reports
    year,month,day=int(year),int(month),int(day)
    reports=[r[1] for r in reportengine.all_reports() if r[1].date_field]
    date=datetime.datetime(year=year,month=month,day=day)
    cal=calendar.monthcalendar(year,month)
    # TODO possibly pull in date based aggregates?
    cx={"reports":reports,"date":date,"calendar":cal}
    return render_to_response("reportengine/calendar_day.html",cx,
                              context_instance=RequestContext(request))

