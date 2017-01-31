"""
Report Engine Tests.
"""
from base import Report
from filtercontrols import FilterControl
from django.test import TestCase
from django import forms
import reportengine
from models import ReportRequest

class BasicTestReport(Report):
    """Test Report. set the rows an aggregate to test"""
    slug="test"        
    namespace="testing"
    verbose_name="Basic Test Report"

    def __init__(self,
                    rows=[ [1,2,3] ],
                    labels=["col1","col2","col3"],
                    aggregate = (('total',1),),
                    filterform = forms.Form() ):
        self.rows=rows
        self.labels=labels
        self.aggregate=aggregate
        self.filterform=filterform

    def get_rows(self,filters={},order_by=None):
        return self.rows,self.aggregate

    def get_filter_form(self,request):
        return self.filterform


class BasicReportTest(TestCase):
    def _register_test_report(self):
        r=BasicTestReport()
        reportengine.register(r)
        return r

    def test_report_register(self):
        """
        Tests registering a report, and verifies report is now accessible
        """
        r = self._register_test_report()
        assert(reportengine.get_report("testing","test") == r)

        found=False
        for rep in reportengine.all_reports():
            if rep[0] == (r.namespace,r.slug):
                assert(rep[1] == r)
                found=True
        assert(found)

    def test_report_request(self):
        test_token = "ABC123"
        r = self._register_test_report()
        rr = ReportRequest(  token="ABC123",
                        namespace="testing",
                        slug="test",
                        params={} )
        rr.save()
        print rr


