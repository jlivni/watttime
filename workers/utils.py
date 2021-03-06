# Copyright wattTime 2013
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Authors: Anna Schneider

from django.core.mail import send_mail
from accounts.messages import ca_message_dirty, ca_message_clean
from accounts.models import UserProfile
from workers.models import ScheduledTasks, DailyReport, DebugMessage
from datetime import datetime, timedelta, date
# from accounts.models import SENDTEXT_TIMEDELTAS
from sms_tools.models import TwilioSMSEvent
from random import randint
import settings
from settings import EMAIL_HOST_USER
import accounts.messages
import datetime
import pytz
import traceback

def send_ca_texts(group):
    # group == 0: daily, dirty
    # group == 1: daily, clean
    # group == 2: less than daily
    # group == 3: emergencies only

    # We don't have messages for these groups yet.
    if not (group in [0, 1]):
        return

    for up in UserProfile.objects.all():
        if up.user.is_active and up.is_verified and up.state == 'CA':
            if up.get_region_settings().message_frequency == group:
                if group == 0:
                    message = ca_message_dirty(up)
                else: # group == 1
                    message = ca_message_clean(up)
                res = accounts.twilio_utils.send_text(message, up)

                msg = 'Sent text "{}" to {}'.format(message.msg, up)
                if not res:
                    msg = "FAILED: " + msg
                add_to_report(msg)

def schedule_task(date, command):
    t = ScheduledTasks()
    t.date = date
    t.command = command
    t.repeat = False
    t.save()

def repeat_task(date, command, interval):
    if interval < 30:
        raise ValueError('No repeats shorter than 30 seconds')

    t = ScheduledTasks()
    t.date = date
    t.command = command
    t.repeat = True
    t.repeat_interval = interval

def perform_scheduled_tasks():
    # These might be needed by some commands
    # import workers.tasks
    import workers.utils
    # import workers.views
    import workers.models

    now = datetime.datetime.now(pytz.utc)
    for task in ScheduledTasks.objects.all():
        if task.date <= now:
            command = task.command
            if task.repeat:
                while task.date <= now:
                    task.date += datetime.timedelta(seconds = task.repeat_interval)
                task.save()
            else:
                task.delete()
            try:
                exec (command) # Fixed in Python 3
            except Exception as e:
                msg = 'Scheduled task "{}" threw exception:\n{}'.format(
                        command, traceback.format_exc(e))
                debug (msg)
                add_to_report(msg)

def same_day(t1, t2):
    return t1.year == t2.year and t1.month == t2.month and t1.day == t2.day

def send_daily_report():
    now = datetime.datetime.now(pytz.utc)
    # Get list of emails to send daily report to
    targets = map((lambda admin: admin[1]), settings.ADMINS)
    # targets = ['eric.stansifer@gmail.com', 'gavin.mccormick@gmail.com', 'annarschneider@gmail.com']
    # targets = ['eric.stansifer@gmail.com']
    subj = 'WattTime daily report {}'.format(now.strftime('%Y.%m.%d'))
    message = []
    message.append('Report generated {} UTC'.format(now.strftime('%Y.%m.%d %H.%M')))

    events = []
    for dr in DailyReport.objects.all():
        events.append((dr.date, dr.message))
        dr.delete()
    events.sort()

    if len(events) == 0:
        message.append('No events since last report.')
    else:
        prev_date = None
        for event in events:
            cur_date = event[0]

            if prev_date is None or (not same_day(prev_date, cur_date)):
                message.append('')
                if same_day(cur_date, now):
                    message.append('Today:')
                elif same_day(cur_date + datetime.timedelta(days = 1), now):
                    message.append('Yesterday:')
                else:
                    message.append(cur_date.strftime('%Y.%m.%d:'))
            prev_date = cur_date

            message.append('[{}]: {}'.format(cur_date.strftime('%H.%M'), event[1]))

    message = '\n'.join(message)

    send_mail(subj, message, EMAIL_HOST_USER, targets)

def add_to_report(message):
    event = DailyReport()
    event.date = datetime.datetime.now(pytz.utc)
    event.message = message
    event.save()

def debug(message):
    print (message)
    dm = DebugMessage()
    dm.date = datetime.datetime.now(pytz.utc)
    dm.message = message
    dm.save()
