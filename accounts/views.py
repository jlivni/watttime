from django.shortcuts import render
from django.http import HttpResponse, HttpResponseRedirect
from accounts import models, forms, messages, twilio_utils, regions
from watttime_shift.models import ShiftRequest
from django.contrib.auth.models import User
from django.contrib.auth import login, authenticate, REDIRECT_FIELD_NAME
from django.core.urlresolvers import reverse
import random
from django.utils.timezone import now
from django.core.mail import send_mail
import settings

def redirect(url):
    return HttpResponseRedirect(reverse(url))

class FormView:
    def __init__(self, html_file, form):
        self.require_authentication = True
        self.html_file = html_file
        self._form = form

    def form(self, user):
        return self._form

    def form_initial(self, user):
        return {}

    def html_params(self, user):
        return {}

    def form_submitted(self, request, vals):
        raise NotImplementedError()

    def render(self, request, html, vals):
        return render(request, html, vals)

    def __call__(self, request):
        user = request.user
        if (not self.require_authentication) or user.is_authenticated():
            if request.method == 'POST':
                form = self.form(user)(request.POST)
                if form.is_valid():
                    return self.form_submitted(request, form.cleaned_data)
            else:
                form = self.form(user)(initial = self.form_initial(user))

            vals = self.html_params(user)
            vals['form'] = form

            return self.render(request, 'accounts/{}.html'.format(self.html_file), vals)
        else:
            return redirect('authenticate')

class ProfileEdit(FormView):
    def __init__(self):
        FormView.__init__(self, 'profile_settings', forms.UserProfileForm)

    def form(self, user):
        return user.get_profile().region().user_prefs_form

    def form_initial(self, user):
        return user.get_profile().form_initial_values()

    def html_params(self, user):
        up = user.get_profile()

        # get shift scores
        uid = user.id
        hrs_shifted = 0.0
        fraction_clean = 0.0
        fraction_improved = 0.0
        for r in ShiftRequest.objects.all().filter(requested_by=uid):
            hrs_shifted += r.usage_hours
            fraction_clean += r.usage_hours*r.recommended_fraction_green
            fraction_improved += r.usage_hours*(r.recommended_fraction_green - r.baseline_fraction_green) / r.baseline_fraction_green
        if hrs_shifted > 0:
            av_clean = fraction_clean / hrs_shifted * 100
            av_improved = fraction_improved / hrs_shifted * 100
        else:
            av_clean = 0
            av_improved = 0
            
        return {'name' : up.name,
                'email' : up.email,
                'state' : up.state,
                'phone' : up.phone,
                'region' : up.region().name,
                'phone_verified' : up.is_verified,
                'deactivated' : not user.is_active,
                'has_phone' : len(up.phone) > 0,
                'supported_location' : up.supported_location(),
                'forecasted_location' : up.supported_location_forecast(),
                'hrs_shifted' : round(hrs_shifted, 1),
                'av_clean': round(av_clean, 1),
                'av_improved': round(av_improved, 1),
                }

    def form_submitted(self, request, vals):
        up = request.user.get_profile()
        up.save_from_form(vals)
        if up.phone and not up.is_verified:
            return redirect('phone_verify_view')
        else:
            return redirect('profile_settings')

class ProfileCreate(FormView):
    def __init__(self):
        FormView.__init__(self, 'profile_create', forms.AccountCreateForm)
       # self.require_authentication = False

    def html_params(self, user):
        up = user.get_profile()
        return {'name' : up.name, 'region_supported' : up.supported_location()}

    def form_submitted(self, request, vals):
        request.user.get_profile().save_from_form(vals)
        return redirect('profile_first_edit')


class ProfileFirstEdit(FormView):
    def __init__(self):
        FormView.__init__(self, 'profile_first_edit', forms.UserProfileFirstForm)

    def form_initial(self, user):
        up = user.get_profile()
        return {'phone' : up.phone, 'state' : up.state}

    def html_params(self, user):
        up = user.get_profile()
        return {'name' : up.name, 'region_supported' : up.supported_location(),
                'region_forecasted': up.supported_location_forecast()}

    def form_submitted(self, request, vals):
        request.user.get_profile().save_from_form(vals)
        return redirect('phone_verify_view')

# A bit hackish 
class PhoneVerifyView(FormView):
    def __init__(self):
        FormView.__init__(self, '', forms.PhoneVerificationForm)

    def form_submitted(self, request, vals):
        up = request.user.get_profile()
        code = int(vals['verification_code'])
        if code == up.verification_code:
            up.is_verified = True
            up.save()
            return redirect('profile_settings')
        else:
            return render(request, 'accounts/phone_verification_wrong.html',
                    {'phone_number' : up.phone, 'form' : self.form()})

    def render(self, request, html, vals):
        user = request.user
        up = user.get_profile()
        vals['phone_number'] = up.phone
        if up.is_verified:
            return render(request, 'accounts/phone_already_verified.html', vals)
        else:
            # TODO specialized error message for blank phone number
            sent = send_verification_code(user)
            if sent:
                return render(request, 'accounts/phone_verify.html', vals)
            else:
                return render(request, 'accounts/phone_bad_number.html', vals)

class LoginView(FormView):
    def __init__(self):
        FormView.__init__(self, 'login', forms.LoginForm)
        self.require_authentication = False

    def form_submitted(self, request, vals):
        email       = vals['email']
        password    = vals['password']
        users = list(User.objects.filter(email__iexact = email))

        if len(users) == 0:
            return render(request, 'accounts/no_such_user.html', {'email' : email})
        user = users[0]

        user = authenticate(username = user.username, password = password)
        # if username was set wrong, try with email
        if user is None:
            user = authenticate(username = email, password = password)
        
        # try to login
        if user is not None:
            login(request, user)
            redirect_to = request.GET.get(REDIRECT_FIELD_NAME, '')
            if redirect_to:
                HttpResponseRedirect(redirect_to)
            else:
                return redirect('profile_settings')
        else:
            return render(request, 'accounts/wrong_password.html', {'email' : email})

    def __call__(self, request):
        if request.user.is_authenticated():
            return redirect('profile_settings')
        else:
            return FormView.__call__(self, request)

class CreateUserView(FormView):
    def __init__(self):
        FormView.__init__(self, 'signup', forms.SignupForm)

    def __call__(self, request):
        if request.method == 'POST':
            form = self._form(request.POST)
            if form.is_valid():
                return self.form_submitted(request, form.cleaned_data)

        else:
            form = self._form
            return redirect('authenticate')
        
        return redirect('authenticate')

    def form_submitted(self, request, vals):
        user = create_and_email_user(vals['email'], state = vals['state'])
        if user:
            if user.get_profile().supported_location():
                return redirect('signed_up')
            else:
                return redirect('signed_up_future')
        else:
            ups = models.UserProfile.objects.filter(email__iexact = vals['email'])
            if len(ups) > 0:
                email = ups[0].email
            else:
                email = vals['email']
            return render(request, 'accounts/user_already_exists.html',
                    {'email' : email})

profile_settings = ProfileEdit()

profile_first_edit = ProfileFirstEdit()

profile_create = ProfileCreate()

phone_verify_view = PhoneVerifyView()

#user_login = LoginView()

create_user = CreateUserView()

def new_user_name():
    users = [None]
    while len(users) > 0:
        uid = str(random.randint(10000000, 99999999))
        users = User.objects.filter(username = uid)

    return uid

def new_phone_verification_number():
    return random.randint(100000, 999999)

# TODO all this code needs proper logging and error handling, not using 'print'

def authenticate_view(request):
    # set up forms
    signup_form = forms.SignupForm(initial = {'state' : u'%s' % 'CA'})
    login_form = forms.LoginForm()
    
    # return
    return render(request, 'accounts/authenticate.html',
            {'signup_form' : signup_form,
             'login_form' : login_form})
    

def create_new_user(email, name = None, state = None):
    users = User.objects.filter(email__iexact = email)
    if len(users) > 0:
        print ("User(s) with email {} already exists, aborting user creation!".
                format(email))
        return None

    username = email #new_user_name()
    user = User.objects.create_user(username, email = email, password = None)
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    # The following fields are fields we store in the UserProfile object instead
    #   user.first_name
    #   user.last_name
    #   user.email
    user.save()

    up = models.UserProfile()
    up.user = user
    up.password_is_set = False
    up.magic_login_code = random.randint(100000000, 999999999)
    # If the user doesn't specify a name, email is used as the default
    if name is None:
        up.name = email
    else:
        up.name = name
    up.email = email
    up.phone = ''
    up.verification_code = new_phone_verification_number()
    up.is_verified = False

    if state is None:
        up.state = 'CA'
    else:
        up.state = state

    up.ca_settings = None
    up.ne_settings = None
    up.null_settings = None

    up.get_region_settings() # so that the region settings are not None

    up.set_equipment([])
    up.beta_test = True
    up.ask_feedback = False

    # In the future, we should separate phone-number, etc., into a separate model

    up.save()

    print ("User {} created.".format(email))
    return user

def create_and_email_user(email, name = None, state = None):
    user = create_new_user(email, name, state)
    if user:
        up = user.get_profile()
        magic_url = "http://watttime.com/profile/{:d}".format(
                up.magic_login_code)
        if up.supported_location():
            msg = messages.invite_message(email, magic_url, name)
        else:
            msg = messages.invite_message_unsupported(email, magic_url, name)

        send_mail('Welcome to WattTime',
                msg,
                settings.EMAIL_HOST_USER,
                [email])
        return user
    else:
        return None

def http_invite(request, email):
    if create_and_email_user(email):
        return HttpResponse("Sent email to {}".format(email), "application/json")
    else:
        return HttpResponse("User already exists", "application/json")

def http_invite_with_name(request, email, name):
    if create_and_email_user(email, name):
        return HttpResponse("Sent email to {} ({})".format(name, email), "application/json")
    else:
        return HttpResponse("User already exists", "application/json")

def email_login_user(user):
    magic_url = "http://watttime.com/profile/{:d}".format(
            user.get_profile().magic_login_code)
    send_mail('Account recovery for WattTime',
            messages.resend_login_message(user.get_profile().name, magic_url),
            settings.EMAIL_HOST_USER,
            [user.get_profile().email])

def magic_login(request, magic_login_code):
    magic_login_code = int(magic_login_code)

    # Is there a user with that login code?
    try:
        up = models.UserProfile.objects.get(magic_login_code = magic_login_code)
    except:
        # No such user.
        print ("No user with login code {}".format(magic_login_code))
        return redirect('accounts.views.frontpage')
    else:
        # This is necessary because one cannot login without authenticating
        user = up.user
        pw = str(random.randint(1000000000, 9999999999))
        user.set_password(pw)
        user.is_active = True
        user.save()
        up.password_is_set = False
        up.save()

        # 'authenticate' attaches a 'backend' object to the returned user,
        # which is necessary for the login process
        user = authenticate(username = user.username, password = pw)

        login(request, user)
        print ("Logged in user {}".format(up.name))
        return redirect('profile_create')

# Returns True if code sent successfully, otherwise False
def send_verification_code(user):
    up = user.get_profile()
    code = new_phone_verification_number()
    up.verification_code = code
    up.save()
    print ("Sending {} verification code {:d}".format(up.name, code))
    msg = messages.verify_phone_message(code)
    sent = twilio_utils.send_text(msg, up, force = True)
    if sent:
        print ("Send successful.")
    else:
        print ("Send unsuccessful.")
    return sent

def set_active(request, new_value):
    user = request.user
    if user.is_authenticated():
        user.is_active = new_value
        user.save()
        return redirect('profile_settings')
    else:
        return redirect('authenticate')

def deactivate(request):
    return set_active(request, False)

def reactivate(request):
    return set_active(request, True)
