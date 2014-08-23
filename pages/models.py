from django.db import models
from django import forms
from django.forms.widgets import *
from django.core.mail import send_mail, BadHeaderError

# A simple contact form with four fields.
class ContactForm(forms.Form):
    name = forms.CharField(max_length=100,
                           widget=forms.TextInput(attrs={'placeholder': 'Name'}))
    email = forms.EmailField(label = "Email",
                           widget=forms.TextInput(attrs={'placeholder': 'Email'}))
    subject = forms.CharField(max_length=100,
                           widget=forms.TextInput(attrs={'placeholder': 'Subject'}))
    message = forms.CharField(widget=Textarea(attrs={'placeholder': 'Message'}))


class Article(models.Model):
	# article title
	title = models.CharField(max_length=500)

	# date article was published
	published_on = models.DateField()

	# link to article
	link = models.URLField()

	# name of newspaper, blog, etc
	outlet = models.CharField(max_length=100)

	class Meta:
		get_latest_by = 'published_on'
