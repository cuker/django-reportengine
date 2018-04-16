"""
Based loosely on admin filterspecs, these are more focused on delivering controls appropriate per field type

Different filter controls can be registered per field type. When assembling a set of filter controls, these field
types will generate the appropriate set of fields. These controls will be based upon what is appropriate for that field.
For instance, a datetimefield for filtering requires a start/end. A boolean field needs an "all", "true" or "false" in
radio buttons.

It is sometimes necessary to manually add FilterControls to a report/field because there is no default registration, or
because multiple controls may be appropriate for a particular field.

"""
from django import forms
from django.db import models
from django.utils.translation import ugettext as _
from django.utils.datastructures import SortedDict

# TODO build register and lookup functions
# TODO figure out how to manage filters and actual request params, which aren't always 1-to-1 (e.g. datetime)

class FilterControl(object):
    """
    FilterControl is a quasi-abstract factory parent.  It's subclasses determine how the fields should be represented
    and how they should behave.

    By default, this will return an exact match charfield.

    """
    filter_controls=[]
    def __init__(self,field_name,label=None):
        """
        Constructor for the FilterControl

        :param field_name:  The name of the field that this filtercontrol will filter on.
        :param label:   The label for this filtercontrol, if none is set, the field_name will be when rendering.
        """
        self.field_name=field_name
        self.label=label

    def get_fields(self):
        """
        Gets the FormField objects for this filter control.  This is an exact match field.

        :return: a dictionary of field name -> django formfield pairs.
        """
        return {self.field_name:forms.CharField(label=self.label or self.field_name,required=False)}

    # Pulled from django.contrib.admin.filterspecs
    def register(cls, test, factory, datatype):
        """
        Registers a filter control against a specific datatype.  The filter controller is added to
        filter_controls.

        Depending on whether create_from_modelfield or create_from_datatype (below) is used, the test or datatype will
        be used to create FilterControls in the future.

        :param test: A function to test whether the filtercontrol should be displayed (?)
        :param factory:  The filter control class.  When called, you will get a subclass of FilterControl.
        :param datatype:  The field type that is filtered by this control (?)
        """
        cls.filter_controls.append((test, factory, datatype))
    register = classmethod(register)

    def create_from_modelfield(cls, f, field_name, label=None):
        """
        create_from_modelfield returns a FilterControl subclass, based on the field type.
        "f" must be accepted by the test methods defined during Filter Control registration,
        so it's type could actually vary depending on various implementations, but to match the registered
        test functions in this module, it should be a subclass of django.db.models.field.Field

        :param f: The modelfield to test.  If the test passes, the related factory will be returned.
        :param field_name: The name of the field.
        :param label: The label for the filter control.
        :return:  A FilterControl subclass.
        """
        for test, factory, datatype in cls.filter_controls:
            if test(f):
                return factory(field_name,label)
    create_from_modelfield = classmethod(create_from_modelfield)

    def create_from_datatype(cls, datatype, field_name, label=None):
        """
        create_from_datatype returns a FilterControl subclass, based on the datatype.  If datatype matches a registered
        datatype, then the appropriate subclass is instantiated and returned.

        :param datatype: The datatype of the field.  A string.
        :param field_name:  The name of the FilterControl subclass - passed thru to the constructor
        :param label:  The label for the FilterControl subclass - passed through to the constructor
        :return:  A FilterControl subclass.
        """
        for test, factory, dt in cls.filter_controls:
            if dt == datatype:
                return factory(field_name,label)
    create_from_datatype = classmethod(create_from_datatype)

FilterControl.register(lambda m: isinstance(m,models.CharField),FilterControl,"char")

class DateTimeFilterControl(FilterControl):
    def get_fields(self):
        """
        Returns DateTimeInput Form Fields for filtering reports.

        :return:  A dictionary containing hte start and end dates for the filtercontrol
        """
        ln=self.label or self.field_name
        start=forms.CharField(label=_("%s From")%ln,required=False,widget=forms.DateTimeInput(attrs={'class': 'vDateField','placeholder':'YYYY-MM-DD'}))
        end=forms.CharField(label=_("%s To")%ln,required=False,widget=forms.DateTimeInput(attrs={'class': 'vDateField','placeholder':'YYYY-MM-DD'}))
        return SortedDict([("%s__gte"%self.field_name, start),
                           ("%s__lt"%self.field_name, end),])

FilterControl.register(lambda m: isinstance(m,models.DateTimeField),DateTimeFilterControl,"datetime")

class BooleanFilterControl(FilterControl):
    def get_fields(self):
        """
        Returns a Boolean Filter Control for filtering reports.

        :return: A dictionary of the field name and filter controls for the field.
        """
        return {self.field_name:forms.CharField(label=self.label or self.field_name,
                required=False,widget=forms.RadioSelect(choices=(('','All'),('1','True'),('0','False'))),initial='A')}

FilterControl.register(lambda m: isinstance(m,models.BooleanField),BooleanFilterControl,"boolean")

# TODO How do I register this one?
class StartsWithFilterControl(FilterControl):
    def get_fields(self):
        """
        Returns a charfield to generate a report where a value "starts with" the given string.

        :return: A dictionary of the field name with a __startswith and filter controls for the field.
        """
        return {"%s__startswith"%self.field_name:forms.CharField(label=_("%s Starts With")%(self.label or self.field_name),
                required=False)}

# CONSIDER How to register the choicefiltercontrol as well.
class ChoiceFilterControl(FilterControl):
    def __init__(self, *args, **kwargs):
        """
        Constructor for the Choice Filter Control
        :param kwargs: Can contain "choices" and "initial" value, used to render the form.  Must contain "field_name"
        and "label," as these are passed to the FilterControl constructor.
        """
        self.choices = kwargs.pop('choices', [])
        self.initial = kwargs.pop('initial', None)
        super(ChoiceFilterControl, self).__init__(*args, **kwargs)

    def get_fields(self):
        """
        Returns a dictionary with the field name and choice field.

        :return:  The dictionary's key is the field's name, the value is a choice field
                  with the choices and initial value as passed to the constructor.
        """
        return {self.field_name: forms.ChoiceField(
            choices=self.choices,
            label=self.label or self.field_name,
            required=False,
            initial=self.initial,
            )}
