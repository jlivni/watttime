from django import forms
from django.forms.widgets import *
from django.core.mail import send_mail, BadHeaderError

# A simple contact form with four fields.
class ContactForm(forms.Form):
    name = forms.CharField(max_length=100, initial='Name')
    email = forms.EmailField(label = "Email", initial='Email')
    subject = forms.CharField(max_length=100, initial='Subject')
    message = forms.CharField(widget=Textarea(), label = "Message")
